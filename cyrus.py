#!/usr/bin/env python3
"""Control a Cyrus ONE / ONE HD amplifier over BLE.

Reverse-engineered protocol; see PROTOCOL.md.
Frame: '@' <pad> <CMD> <len-digit> <payload...> '%'
"""

import os
import sys

# The shebang picks the system python, but bleak lives in the project venv.
# Re-exec there so ./cyrus.py works without having to spell out the interpreter.
_VENV = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".venv", "bin", "python3"
)
if __name__ == "__main__" and sys.prefix == sys.base_prefix and os.path.exists(_VENV):
    os.execv(_VENV, [_VENV, os.path.abspath(__file__), *sys.argv[1:]])

import argparse
import asyncio
import time

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    sys.exit(
        "bleak is not installed. From the project directory run:\npython3 -m venv .venv && .venv/bin/pip install bleak"
    )

SERVICE = "bc2f4cc6-aaef-4351-9034-d66268e328f0"
CHAR = "06d1e5e7-79ad-4a71-8faa-373789f7d93c"
CACHE = os.path.expanduser("~/.cache/cyrus-one-address")
MAXVOL = os.path.expanduser("~/.config/cyrus-one-maxvol")

VOL_MAX, BAL_MAX = 90, 20

# Inputs by device variant. "HD"/ONE-D has the internal DAC.
INPUTS_DAC = {
    "bluetooth": "1",
    "usb": "2",
    "optical": "3",
    "spdif": "4",
    "phono": "5",
    "aux6": "6",
    "aux7": "7",
    "av": "8",
}
INPUTS_PLAIN = {
    "bluetooth": "1",
    "phono": "2",
    "aux3": "3",
    "aux4": "4",
    "aux5": "5",
    "av": "6",
}

SAFE_CODES = ("1", "2", "3", "4", "5")  # non-AV on both variants

FETCHABLE = "DIMVXSTHA"  # dac, input, mute, volume, balance, serial, firmware, headphones, av-direct


def frame(cmd: str, payload: str = "") -> bytes:
    """Build an outgoing frame. Length is a single ASCII digit, so payload <= 9 bytes."""
    assert len(payload) <= 9, f"payload too long for 1-digit length: {payload!r}"
    return b"@+" + f"{cmd}{len(payload)}{payload}".encode() + b"%"


def parse(buf: bytearray):
    """Pull complete '@...%' frames out of buf. Returns [(cmd, payload), ...], mutating buf."""
    out = []
    while True:
        start = buf.find(b"@")
        if start < 0:
            buf.clear()
            return out
        end = buf.find(b"%", start)
        if end < 0:
            del buf[:start]
            return out
        # Resync to the LAST '@' before this '%'. Without it, a frame whose tail
        # was lost merges with the next one: b"@+V2" + b"@+M11%" would decode as
        # ("V", "@+M11"), destroying the real update and poisoning state.
        start = buf.rfind(b"@", start, end)
        f = bytes(buf[start : end + 1])
        del buf[: end + 1]
        if len(f) >= 6:  # >= 6 so a truncated frame never enters state as ""
            out.append((chr(f[2]), f[4:-1].decode("utf-8", "replace")))


def describe(cmd: str, payload: str, has_dac) -> str:
    """One status line. `has_dac` may be None, meaning the amp never told us
    which variant it is, since the same input code means different things on
    each (6 is aux6 on an HD but AV on a plain ONE).
    """
    # isdigit() is True for characters int() rejects ('²' is two valid UTF-8
    # bytes and survives the lossy decode), so require plain ASCII digits.
    if cmd in "VX" and not (payload.isascii() and payload.isdigit()):
        return f"{cmd}         {payload!r} (unreadable)"
    if cmd in "MHAD" and payload not in ("0", "1"):
        return f"{cmd}         {payload!r} (unreadable)"
    if cmd == "V":
        return f"volume    {int(payload)}/{VOL_MAX}"
    if cmd == "X":
        return f"balance   {int(payload)}/{BAL_MAX} (10 = centre)"
    if cmd == "M":
        return f"mute      {'on' if payload != '0' else 'off'}"
    if cmd == "H":
        return f"headphone {'connected' if payload != '0' else 'not connected'}"
    if cmd == "A":
        return f"av-direct {'on' if payload != '0' else 'off'}"
    if cmd == "D":
        return f"dac       {'yes (ONE HD)' if payload != '0' else 'no (ONE)'}"
    if cmd == "S":
        return f"serial    {payload}"
    if cmd == "T":
        return f"firmware  {payload[:1]}.{payload[1:]}"
    if cmd == "I":
        if has_dac is None:
            return f"input     code {payload} (amp variant unknown)"
        names = INPUTS_DAC if has_dac else INPUTS_PLAIN
        label = next(
            (k for k, v in names.items() if v == payload), f"unknown({payload})"
        )
        return f"input     {label}"
    return f"{cmd}         {payload}"


async def find_address(timeout: float) -> str:
    cached = read_setting(CACHE)
    if cached:
        return cached
    print("Scanning for a Cyrus ONE...", file=sys.stderr)
    for d in await BleakScanner.discover(timeout=timeout):
        if d.name and d.name.startswith("ONE"):
            write_setting(CACHE, d.address)
            print(f"Found {d.name} at {d.address}", file=sys.stderr)
            return d.address
    sys.exit(
        "No Cyrus ONE found. Is it powered on and not already connected to a phone?"
    )


class Amp:
    def __init__(self, client):
        self.client, self.buf, self.state = client, bytearray(), {}

    def _on_notify(self, _, data: bytearray):
        self.buf += data
        for cmd, payload in parse(self.buf):
            self.state[cmd] = payload

    async def start(self):
        await self.client.start_notify(CHAR, self._on_notify)

    async def send(self, cmd: str, payload: str = ""):
        # Must be a write WITH response: the data characteristic advertises only
        # 'write', and the amp silently drops write-without-response.
        await self.client.write_gatt_char(CHAR, frame(cmd, payload), response=True)

    async def fetch(self, cmds: str, settle: float = 1.2) -> dict:
        """Ask for each command in `cmds`, then snapshot state.

        The snapshot is everything the amp has reported on this connection --
        not only replies to `cmds` -- because it volunteers state when the front
        panel is used. Callers wanting a fresh value pop the key first.
        """
        for c in cmds:
            await self.send("F", c)
            await asyncio.sleep(0.06)
        await asyncio.sleep(settle)
        return dict(self.state)  # a copy: callers must not alias live state

    async def set_verified(self, cmd: str, payload: str):  # -> str | None
        """Send, then wait for the amp to confirm. Returns the value it last
        reported, or None if it never answered.

        Only for commands that SET. A toggle (M, A) would be inverted by the
        re-send below.

        The amp ignores writes for ~2s after an input change. Re-sending inside
        that window just restarts the lockout, so this polls with reads and
        re-sends at most once, spaced past it.
        """
        for attempt in range(2):
            self.state.pop(cmd, None)
            await self.send(cmd, payload)
            deadline = time.monotonic() + 2.5
            while time.monotonic() < deadline:
                await asyncio.sleep(0.4)
                if self.state.get(cmd) == payload:
                    return payload
                await self.send("F", cmd)  # reads are honoured during the lockout
        return self.state.get(cmd)

    async def get_int(self, cmd: str):
        """Last value the amp reported for `cmd`, or None if it never answered
        with something usable.

        None is NOT 0. A dropped notification and a genuine zero are different
        facts, and every safety decision here depends on telling them apart.
        """
        self.state.pop(cmd, None)
        await self.fetch(cmd, settle=0.8)
        try:
            return int(self.state[cmd])
        except (KeyError, ValueError):
            return None


AV_WARNING = """\
Refusing without --force.

The loud state is AV Direct on AND the AV input selected. Enabling AV Direct
reaches it in one command, because the amp also switches itself to the AV input.

It pins the OUTPUT at a high fixed level. The volume readout does NOT change, so
the volume line still shows whatever you set. Check the 'av-direct' and 'input'
lines of 'status' instead -- the loud state is both of them together.

That is only safe when the AV input is fed by something that controls level
itself, i.e. the front L/R pre-outs of an AV receiver. With a CD player, phone
or any normal line-level source connected, it can damage your speakers.

Re-run with --force if that is really what you want."""


def av_risk(av_direct_on: bool, action: str) -> bool:
    """Whether an action needs --force: selecting the AV input while AV Direct is
    armed, or turning AV Direct on (which also selects the AV input)."""
    if action == "input-av":
        return av_direct_on
    if action == "avdirect":
        return not av_direct_on
    return False


def read_setting(path: str):  # -> str | None
    """Contents of a small local settings file, or None if absent or empty."""
    try:
        with open(path) as f:
            return f.read().strip() or None
    except OSError:
        return None


def write_setting(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def vol_ceiling() -> int:
    """Local volume cap. Advisory only: the amp has no such command, so the
    front panel and IR remote ignore it."""
    raw = read_setting(MAXVOL)
    if raw is None:
        return VOL_MAX          # no cap set
    try:
        return clamp(int(raw), 0, VOL_MAX)
    except ValueError:
        print(f"ignoring unreadable volume cap in {MAXVOL}: {raw!r}", file=sys.stderr)
        return VOL_MAX


def mute_target(cur: int, state: str) -> int:
    """Desired mute flag (1 = muted) given the current one. `M` toggles, so the
    caller sends it only when this differs from `cur`."""
    return {"on": 1, "off": 0, "toggle": 1 - cur}[state]


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


async def run(args):
    if args.cmd == "scan":
        for d in await BleakScanner.discover(timeout=args.timeout):
            mark = " <- Cyrus" if d.name and d.name.startswith("ONE") else ""
            print(f"{d.address}  {d.name or '(unnamed)'}{mark}")
        return

    if args.cmd == "maxvol":
        if args.value is not None:
            write_setting(MAXVOL, str(clamp(int(args.value), 0, VOL_MAX)))
        c = vol_ceiling()
        print(f"max volume {c}/{VOL_MAX}" + (" (no cap)" if c == VOL_MAX else ""))
        return

    address = args.address or await find_address(args.timeout)
    async with BleakClient(address, timeout=args.timeout) as client:
        amp = Amp(client)
        await amp.start()

        if args.cmd == "status":
            st = await amp.fetch(FETCHABLE, settle=1.5)
            # Anything but a clean "0"/"1" is unknown. `!= "0"` would call a
            # zero-padded "00" an HD while get_int() calls it a plain ONE.
            has_dac = {"0": False, "1": True}.get(st.get("D", ""))
            for c in FETCHABLE:
                if c in st:
                    print(describe(c, st[c], has_dac))
            missing = [c for c in FETCHABLE if c not in st]
            if missing:
                # On stdout, not stderr: a missing 'A' silently drops the
                # av-direct row, which is the row you check for the loud state.
                print(f"(no reply for: {' '.join(missing)})")
                sys.exit(
                    "the amp did not report everything; the list above is incomplete"
                )

        elif args.cmd == "volume":
            ceiling = vol_ceiling()
            if args.value.startswith(("+", "-")):
                cur = await amp.get_int("V")
                if cur is None:
                    sys.exit(
                        "could not read the current volume, so cannot step it. "
                        "Retry, or set one outright: cyrus.py volume 45"
                    )
                wanted = cur + int(args.value)
            else:
                wanted = int(args.value)
            target = clamp(wanted, 0, ceiling)
            await amp.send("V", f"{target:02d}")
            capped = (
                f"   (capped at {ceiling}, see 'maxvol')" if target < wanted else ""
            )
            print(f"volume    {target}/{VOL_MAX}{capped}")

        elif args.cmd == "balance":
            target = clamp(int(args.value), 0, BAL_MAX)
            await amp.send("X", f"{target:02d}")
            print(f"balance   {target}/{BAL_MAX} (10 = centre)")

        elif args.cmd == "mute":
            cur = await amp.get_int("M")
            if cur is None:
                sys.exit(
                    "could not read the mute state. M toggles, so guessing would "
                    "risk inverting it.\nRetry, or silence the amp outright: "
                    "cyrus.py volume 0"
                )
            want = mute_target(cur, args.state)
            if want != cur:
                # M toggles and ignores the payload, but sending the value we
                # actually want is correct under either reading.
                await amp.send("M", str(want))
            print(f"mute      {'on' if want else 'off'}")

        elif args.cmd == "input":
            # Annotated because argparse hands back Any, which otherwise leaks
            # through the dict lookup below and defeats type checking here.
            name: str = args.name.lower()
            names = None
            if name in SAFE_CODES:
                code = name  # same non-AV input on both variants
            else:
                dac = await amp.get_int("D")
                if dac is None:
                    sys.exit(
                        "could not read whether this amp has the internal DAC, "
                        "and the input codes differ between variants. Retry, or "
                        "give a code 1-5, which means the same on both."
                    )
                names = INPUTS_DAC if dac else INPUTS_PLAIN
                code = names.get(name, name)
                if code not in names.values():
                    sys.exit(
                        f"Unknown input {args.name!r}. Choose from: "
                        f"{', '.join(names)}, or a code 1-{len(names)}."
                    )
                if code == names["av"] and not args.force:
                    av = await amp.get_int("A")
                    if av is None:
                        sys.exit(
                            "could not read the AV Direct state; refusing to "
                            "select the AV input while it might be armed. Retry, "
                            "or --force."
                        )
                    if av_risk(av != 0, "input-av"):
                        sys.exit(AV_WARNING)
            got = await amp.set_verified("I", code)
            if got is None:
                sys.exit(f"sent 'input {name}' but the amp never confirmed it")
            if got != code:
                label = {v: k for k, v in (names or {}).items()}.get(got, f"code {got}")
                sys.exit(f"input unchanged: amp still reports {label}")
            print(f"input     {name}")

        elif args.cmd == "brightness":
            await amp.send("B", "+" if args.direction == "up" else "-")
            print(f"brightness {args.direction} one step")

        elif args.cmd == "avdirect":
            cur = await amp.get_int("A")
            if cur is None and not args.force:
                # Refusing by default is right -- A is blind, and half the
                # outcomes arm the loud mode. But --force must remain able to
                # turn it OFF, or a read failure locks the user out of recovery.
                sys.exit(
                    "could not read the AV Direct state. A toggles, so guessing "
                    "would risk arming it.\nRetry, or --force to toggle blind. "
                    "To silence the amp either way: cyrus.py volume 0"
                )
            if cur is not None and not args.force and av_risk(cur != 0, "avdirect"):
                sys.exit(AV_WARNING)
            await amp.send("A", "1")  # payload ignored; A just flips the state
            # set_verified() cannot be used here: A is a toggle, so its single
            # re-send would flip AV Direct straight back. Reads do survive the
            # lockout, but one issued sooner still reports the pre-toggle value,
            # so wait out the input change the toggle itself causes, then read.
            await asyncio.sleep(2.5)
            now = await amp.get_int("A")
            if now is None:
                sys.exit(
                    "the toggle was sent and acknowledged, but the amp did not "
                    "report back. It most likely took effect -- if so the amp is "
                    "now at fixed high gain on the AV input.\nSilence it with "
                    "'cyrus.py volume 0', then check 'cyrus.py status'."
                )
            if cur is not None and now == cur:
                sys.exit(
                    f"the toggle had no effect; AV Direct is still "
                    f"{'on' if now else 'off'}. The amp was probably still "
                    f"ignoring writes -- retry in a few seconds."
                )
            print(f"av-direct {'on' if now else 'off'}")
            if now:
                print("note: the amp also switched itself to the AV input.")

        elif args.cmd == "raw":
            rc = args.command.upper()
            if not args.force and (
                rc == "A" or (rc == "I" and args.payload in ("6", "8"))
            ):
                sys.exit(
                    "raw bypasses every safety check: 'A' toggles AV Direct, and "
                    "'I 6' / 'I 8' are the AV input on one variant or the other."
                    "\n\n" + AV_WARNING
                )
            amp.state.clear()
            await amp.send(args.command, args.payload)
            await asyncio.sleep(1.0)
            for c, pl in sorted(amp.state.items()):
                print(f"<- {c} {pl!r}")
            if not amp.state:
                sys.exit("(no reply)")

        elif args.cmd == "watch":
            st = await amp.fetch("D", settle=0.8)  # the amp never volunteers this
            has_dac = {"0": False, "1": True}.get(st.get("D", ""))
            if has_dac is None:
                print(
                    "(the amp did not report its variant; input names may be "
                    "ambiguous)",
                    file=sys.stderr,
                )
            print(
                "Watching (Ctrl-C to stop). Change things on the amp's front panel..."
            )
            seen = {}
            while True:
                await asyncio.sleep(0.2)
                if not client.is_connected:
                    sys.exit("amp disconnected")
                for c, pl in list(amp.state.items()):
                    if seen.get(c) != pl:
                        seen[c] = pl
                        print(describe(c, pl, has_dac))

        await asyncio.sleep(0.3)


def main():
    ap = argparse.ArgumentParser(
        description="Control a Cyrus ONE / ONE HD amplifier over BLE."
    )
    ap.add_argument("--address", help="BLE address/UUID (default: cached, else scan)")
    ap.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="seconds to scan for, and to wait for a connection",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scan", help="list nearby BLE devices")
    sub.add_parser("status", help="show all amplifier state")
    sub.add_parser("watch", help="print state changes live")
    # --force lives on the subcommands, not the top-level parser, so that the
    # trailing form (`cyrus.py avdirect --force`) works. argparse only accepts a
    # top-level option BEFORE the subcommand, which is not what anyone retypes.
    p = sub.add_parser(
        "avdirect", help="toggle AV Direct mode (needs --force to enable)"
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="turn AV Direct on, or toggle it blind if unreadable",
    )

    p = sub.add_parser("volume", help="set volume 0-90, or step with +N / -N")
    p.add_argument("value")
    p = sub.add_parser("balance", help="set balance 0-20 (10 = centre)")
    p.add_argument("value")
    p = sub.add_parser("mute", help="mute on/off/toggle")
    p.add_argument("state", choices=["on", "off", "toggle"])
    p = sub.add_parser("input", help="select input by name or number")
    p.add_argument("name")
    p.add_argument(
        "--force",
        action="store_true",
        help="allow the AV input while AV Direct is armed",
    )
    p = sub.add_parser("maxvol", help="show/set a local volume cap (advisory)")
    p.add_argument("value", nargs="?")
    p = sub.add_parser("brightness", help="step display brightness")
    p.add_argument("direction", choices=["up", "down"])
    p = sub.add_parser("raw", help="send an arbitrary command (for experimenting)")
    p.add_argument("command")
    p.add_argument("payload", nargs="?", default="")
    p.add_argument(
        "--force",
        action="store_true",
        help="allow a raw command that could arm AV Direct",
    )

    args = ap.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
