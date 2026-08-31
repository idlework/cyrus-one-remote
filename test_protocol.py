#!/usr/bin/env python3
"""Frame encoding/decoding and command-logic checks. No hardware required."""
import asyncio
import contextlib
import io
import os
import tempfile

import cyrus
from cyrus import av_risk, clamp, describe, frame, mute_target, parse

# Outgoing frames.
assert frame("V", "50") == b"@+V250%"
assert frame("V", "07") == b"@+V207%"   # callers zero-pad to two digits
assert frame("M", "1")  == b"@+M11%"    # payload ignored; M toggles
assert frame("M", "0")  == b"@+M10%"
assert frame("B", "+")  == b"@+B1+%"
assert frame("B", "-")  == b"@+B1-%"
assert frame("I", "8")  == b"@+I18%"    # AV on a ONE HD
assert frame("X", "05") == b"@+X205%"
assert frame("A", "1")  == b"@+A11%"    # payload ignored; A toggles
assert frame("F", "V")  == b"@+F1V%"    # read the volume

# Incoming frames: command at offset 2, payload from offset 4 to the '%'.
b = bytearray(b"@+V250%")
assert parse(b) == [("V", "50")] and not b
assert parse(bytearray(b"@+S5CF2HB%")) == [("S", "CF2HB")]
assert parse(bytearray(b"@+T210%")) == [("T", "10")]

# Serial number shape from a ONE HD (digits altered). The length field is a
# single digit and saturates at 9, so a 10-character serial reports 9 --
# read to the '%' rather than trusting it.
assert parse(bytearray(b"@+S9AB1CD00000%")) == [("S", "AB1CD00000")]

# Two frames in one notification.
assert parse(bytearray(b"@+M11%@+V206%")) == [("M", "1"), ("V", "06")]

# A frame split across notifications must survive reassembly.
buf = bytearray(b"@+V2")
assert parse(buf) == []
buf += b"45%"
assert parse(buf) == [("V", "45")] and not buf

# Leading garbage is dropped, not mistaken for a frame.
assert parse(bytearray(b"xx@+M10%")) == [("M", "0")]

# mute_target() gives the wanted flag; the caller sends only when it differs
# from the current one. That send guard lives in run() and is not covered here.
assert mute_target(0, "on") == 1 and mute_target(1, "on") == 1
assert mute_target(1, "off") == 0 and mute_target(0, "off") == 0
assert mute_target(0, "toggle") == 1 and mute_target(1, "toggle") == 0

# av_risk() only decides; run() combines it with --force. That combination
# needs a live client, so it is not covered here.
assert av_risk(True,  "input-av") is True    # AV input while AV Direct is on
assert av_risk(False, "input-av") is False   # AV input is fine otherwise
assert av_risk(False, "avdirect") is True    # turning AV Direct ON
assert av_risk(True,  "avdirect") is False   # turning it OFF is always allowed
assert av_risk(True,  "volume") is False     # unrelated actions unaffected

# The volume cap is local and advisory; a missing or junk file means "no cap".
cyrus.MAXVOL = os.path.join(tempfile.gettempdir(), "cyrus-maxvol-test")
if os.path.exists(cyrus.MAXVOL):
    os.unlink(cyrus.MAXVOL)
assert cyrus.vol_ceiling() == 90                    # no file -> no cap
cyrus.write_setting(cyrus.MAXVOL, "60")
assert cyrus.vol_ceiling() == 60
assert clamp(75, 0, cyrus.vol_ceiling()) == 60      # request above the cap is clamped
assert clamp(45, 0, cyrus.vol_ceiling()) == 45      # below it passes through
cyrus.write_setting(cyrus.MAXVOL, "500")     # hand-edited, or from an older build
assert cyrus.vol_ceiling() == 90             # still capped at the hardware maximum
cyrus.write_setting(cyrus.MAXVOL, "garbage")
_err = io.StringIO()
with contextlib.redirect_stderr(_err):              # expected warning; keep it off the
    assert cyrus.vol_ceiling() == 90                # test output. Unparseable -> no cap
assert "unreadable volume cap" in _err.getvalue()   # ...and the user is told, not ignored
os.unlink(cyrus.MAXVOL)

# A frame whose tail was lost must not merge with the next one. Before the
# rfind() resync this decoded as ("V", "@+M11"): the real update destroyed and a
# garbage payload left in its place.
assert parse(bytearray(b"@+V2@+M11%")) == [("M", "1")]
assert parse(bytearray(b"@@+V250%")) == [("V", "50")]
assert parse(bytearray(b"@+V2%")) == []      # truncated: never stored as ""
assert parse(bytearray(b"@%@+V250%")) == [("V", "50")]   # a runt does not eat the next
g = bytearray(b"xx@+V2")                     # garbage trimmed, partial frame kept
assert parse(g) == [] and bytes(g) == b"@+V2"

# describe() feeds `watch`, which runs for hours over a checksum-free link, so a
# single garbled notification must not take it down.
for _cmd in "VXMIHADST":
    describe(_cmd, "", True)
    describe(_cmd, "4x", True)
assert describe("V", "48", True) == "volume    48/90"
assert describe("X", "10", True) == "balance   10/20 (10 = centre)"
assert describe("T", "23", True) == "firmware  2.3"
assert describe("Z", "99", True) == "Z         99"        # unknown command falls through
for _bad in ("", "4x", "00", "\ufffd"):
    for _cmd in "MHAD":
        assert "unreadable" in describe(_cmd, _bad, True), (_cmd, _bad)
assert describe("D", "1", True) == "dac       yes (ONE HD)"   # clean values still read
assert describe("A", "0", True) == "av-direct off"
assert "unreadable" in describe("V", "\u00b2", True)     # isdigit() is True, int() rejects

# The same wire code is a different physical input per variant. (The refusal
# that depends on this lives in run() and is not covered here.)
assert describe("I", "5", True) == "input     phono"
assert describe("I", "5", False) == "input     aux5"
assert describe("I", "6", True) == "input     aux6"
assert describe("I", "6", False) == "input     av"        # !! AV on a plain ONE
assert "unknown" in describe("I", "6", None)              # variant unread -> no guess

# Relative volume is arithmetic we do ourselves; the amp has no step command.
assert clamp(-5, 0, 90) == 0 and clamp(95, 0, 90) == 90
assert clamp(5 - 20, 0, 90) == 0                          # `volume -20` from 5 floors
assert clamp(85 + 10, 0, 90) == 90                        # `volume +10` from 85 tops out
assert frame("V", f"{clamp(-15, 0, 90):02d}") == b"@+V200%"   # clamped value stays valid

# get_int() must answer None, never 0, when the amp says nothing usable. Every
# refusal in run() keys off that: 0 reads as "not muted" and as "AV Direct off",
# the direction that selects the AV input while the loud mode is armed.
class _FakeClient:          # stands in for BleakClient; no BLE, no I/O
    reply: "str | None" = None

    async def write_gatt_char(self, _char, data, response=True):
        if chr(data[2]) == "F" and self.reply is not None:
            _amp.state[chr(data[4])] = self.reply


_amp = cyrus.Amp(_FakeClient())
assert asyncio.run(_amp.get_int("A")) is None    # no reply at all -> unknown
_amp.client.reply = "x"
assert asyncio.run(_amp.get_int("A")) is None    # garbled reply -> unknown
_amp.client.reply = "0"
assert asyncio.run(_amp.get_int("A")) == 0       # ...but a real zero is still zero

print("all protocol checks passed")
