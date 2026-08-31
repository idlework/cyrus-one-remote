# cyrus-one-remote

Command-line control for the Cyrus ONE and Cyrus ONE HD integrated amplifier
over Bluetooth Low Energy.

The manufacturer's mobile app has been withdrawn, leaving no way to reach the
settings it exposed. This is an independent reimplementation of the control
protocol, reverse-engineered and verified against a Cyrus ONE HD.

Unofficial. Not affiliated with or endorsed by Cyrus Audio.

## Requirements

Python 3.10 or newer, a machine with Bluetooth LE, and an amplifier in range.

```sh
make install        # or: python3 -m venv .venv && .venv/bin/pip install bleak
```

`./cyrus.py` locates that virtual environment itself. There is no need to
activate it or name the interpreter, and it works from any directory.

## Usage

```sh
./cyrus.py status              # everything the amplifier reports
./cyrus.py volume 45           # absolute, 0-90
./cyrus.py volume +5           # relative step
./cyrus.py mute toggle         # also: on, off
./cyrus.py input optical
./cyrus.py balance 10          # 0-20, 10 is centre
./cyrus.py brightness up       # also: down
./cyrus.py maxvol 60           # local volume cap; bare 'maxvol' shows it
./cyrus.py watch               # live state, including front-panel changes
./cyrus.py scan                # list nearby BLE devices
./cyrus.py raw F V             # send an arbitrary command
```

Inputs on a ONE HD: `bluetooth` `usb` `optical` `spdif` `phono` `aux6` `aux7`
`av`. On a ONE without the DAC: `bluetooth` `phono` `aux3` `aux4` `aux5` `av`.
The correct set is chosen automatically.

The amplifier's address is cached in `~/.cache/cyrus-one-address` after the
first scan. Delete that file to scan again, or pass `--address`.

Only one BLE connection is accepted at a time, so disconnect any phone first.

## AV Direct

AV Direct turns the amplifier into a fixed-gain power amplifier for the front
channels of a home cinema system. It pins the output at a high level on the AV
input, and toggling it **also switches the amplifier to that input**, so it
takes effect immediately.

Fed by an AV receiver's pre-outs, that is the intent. Fed by a CD player, a
phone, or any other line-level source, it can damage loudspeakers.

It pins the **output**, not the volume setting. The volume readout does not
change, so the volume line tells you nothing here. The `av-direct` and `input`
lines of `status` do — the loud state is both of them together.

`cyrus.py` therefore requires `--force` to

- turn AV Direct **on**, or
- select the **AV input while AV Direct is already on**.

```sh
./cyrus.py avdirect --force
./cyrus.py input av --force
```

`raw` needs it too, for any command that could reach the same state
(`raw A ...`, `raw I 6`, `raw I 8`).

Turning AV Direct **off** never requires `--force` when the amplifier answers.
If it does not answer, the tool refuses rather than assume the mode is off —
but `--force` will then toggle blind, so you are never locked out of turning it
off.

## When it refuses

Two commands on this amplifier toggle rather than set, so acting on a stale or
missing reading can invert them. Rather than guess, `cyrus.py` stops and says so
when it cannot read the state a command depends on: the mute flag, the current
volume for a relative step, which variant the amplifier is, or whether AV Direct
is armed. Each exits non-zero.

Two things always work regardless, because neither needs a reading first:

```sh
./cyrus.py volume 0        # absolute write; the reliable way to silence it
./cyrus.py input 3         # codes 1-5 are the same input on both variants
```

## Volume cap

`maxvol` stores an integer in `~/.config/cyrus-one-maxvol` and clamps what this
tool will send. The amplifier has no such command, so the cap is advisory: the
front panel and the IR remote ignore it.

## What is not supported

The amplifier has no equalizer or tone controls, so neither does this. Power
on and off are not exposed over BLE either.

## Protocol

[PROTOCOL.md](PROTOCOL.md) documents the transport, frame format, command set,
and the behaviours that are easy to get wrong: two commands toggle rather than
set, writes need a response, and the amplifier drops writes for two seconds
after an input change.

## Development

```sh
make dev      # venv + linter + type checker
make check    # lint, type check, tests
make          # list every target
```

`make test` covers frame encoding and decoding, the toggle logic, the `--force`
conditions, the volume cap, and that an unreadable amplifier reply is reported
as unknown rather than guessed. No hardware or network required.

`pyrightconfig.json` points the type checker at `.venv`, so editors resolve
`bleak` without any per-machine setup.

## Licence

MIT. See [LICENSE](LICENSE).

Cyrus and Cyrus ONE are trademarks of their respective owner, used here only to
identify the hardware this tool controls. Provided without warranty; you use it
at your own risk.
