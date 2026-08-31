# Cyrus ONE / ONE HD — BLE control protocol

Reverse-engineered. Unofficial and unsupported by Cyrus Audio.

Everything below was confirmed against a Cyrus ONE HD running firmware 2.3.

## Transport

The amplifier exposes a single GATT service carrying a byte pipe. There is no
pairing, bonding, or authentication.

| | |
|---|---|
| Advertised name | begins with `ONE` |
| Service | `bc2f4cc6-aaef-4351-9034-d66268e328f0` |
| Data characteristic | `06d1e5e7-79ad-4a71-8faa-373789f7d93c` (write, notify) |
| Negotiated MTU | 40 |

Connect, discover services, enable notifications on the data characteristic,
then write frames to it.

**Writes must request a response.** The characteristic offers `write` but not
`write-without-response`, and a write-without-response is discarded silently —
no error, no reply. A second characteristic,
`818ae306-9c5b-448d-b51a-7add6a5d314d`, does accept write-without-response; its
purpose is unknown and it is not needed for control.

The amplifier accepts one connection at a time.

## Frame format

Identical in both directions:

```
'@'   <pad>   <CMD>   <len>   <payload…>   '%'
0x40          ASCII   ASCII                0x25
```

- `<pad>` — one byte, ignored on receive. Send `'+'` (0x2B).
- `<CMD>` — a single ASCII letter.
- `<len>` — payload length as one ASCII digit. **Not reliable on receive**: the
  field is a single digit and saturates at 9, so a 10-character serial number
  arrives reporting `9` (serial digits below are altered). Parse the payload as everything between offset 4 and
  the trailing `'%'` instead — but resynchronise first, see **Timing**: taking
  the first `'@'` and the next `'%'` merges two half-frames when a tail is lost.
- `<payload>` — ASCII. Numeric values are zero-padded to a fixed width.

## Commands

| CMD | Meaning | Payload | Direction |
|-----|---------|---------|-----------|
| `V` | Volume | `00`–`90` | read / write |
| `X` | Balance | `00`–`20`, `10` = centre | read / write |
| `M` | Mute | ignored — see *Toggles* | read / write |
| `I` | Input select | `1`–`8` — see *Inputs* | read / write |
| `B` | Display brightness | `+` or `-`, one step | write |
| `A` | AV Direct | ignored — see *Toggles* | read / write |
| `F` | Fetch | one CMD letter | write |
| `S` | Serial number | ASCII | read |
| `T` | Firmware version | 2 digits, `23` = 2.3 | read |
| `D` | Has internal DAC | `0` / `1`, `1` = ONE HD | read |
| `H` | Headphones connected | `0` / `1` | read |

`F` is the only way to read state: `@+F1V%` asks for the volume, which arrives
as `@+V248%`. The amplifier also reports unsolicited when its front panel or IR
remote is used, so a listener sees physical changes live.

`B` has no read-back and no absolute set.

There is no equalizer, tone, bass, treble, or loudness control. The hardware has
none.

## Inputs

The mapping depends on whether the unit has the internal DAC, which `F` `D`
reports.

| Code | ONE HD (`D`=1) | ONE (`D`=0) |
|------|----------------|-------------|
| `1` | Bluetooth | Bluetooth |
| `2` | USB-B | Phono |
| `3` | Optical | AUX3 |
| `4` | S/PDIF coaxial | AUX4 |
| `5` | Phono | AUX5 |
| `6` | AUX6 | AV |
| `7` | AUX7 | — |
| `8` | AV | — |

## Toggles

`M` and `A` **invert the current state and ignore the payload**. Sending
`@+M11%` three times leaves mute off, on, off.

A client must therefore read the current value and send the command only when it
differs from the desired state, or mute ends up inverted.

## AV Direct

AV Direct fixes the output gain at a high level so the amplifier can act as a
power amplifier fed by an AV receiver's pre-outs.

Two behaviours matter:

**Toggling `A` also selects the AV input.** With the amplifier on input `1`, a
single `@+A11%` turned AV Direct on and left the input reporting `8`. Enabling
the mode therefore reaches the high-gain state in one command, with no separate
input change.

**AV Direct does not rewrite the volume value.** At 10/90, `@+F1V%` still
answered `@+V210%` while AV Direct was on. The fixed gain is applied in the
analogue path. A client cannot detect the loud state from the volume readout and
must check `A` and `I` together.

## Timing

The amplifier ignores writes for roughly two seconds after an input change,
returning no error. Because toggling `A` changes the input as a side effect, a
command sent immediately after an AV Direct toggle is dropped too.

A frame can also arrive split across notifications, or with its tail lost. Because
a lost tail leaves the next `'%'` belonging to a later frame, a reader must
resynchronise to the last `'@'` before that `'%'`, or two half-frames merge into
one plausible-looking frame with a garbage payload.

Reads are still honoured during this window. Re-sending a write inside it
restarts the lockout, so a client should confirm by polling with `F` and re-send
at most once, spaced past two seconds.

## No wire command exists for

- **Relative volume.** Read `V`, add the step, write `V`.
- **A maximum-volume limit.** Any cap is client-side only and does not constrain
  the front panel or the IR remote.

## Examples

```
@+V250%    set volume to 50
@+V207%    set volume to 7
@+M11%     toggle mute
@+I18%     select AV on a ONE HD
@+X205%    balance 5, left of centre
@+B1+%     brightness one step up
@+A11%     toggle AV Direct, and select the AV input
@+F1V%     read volume         ->  @+V248%
@+F1S%     read serial number  ->  @+S9AB1CD00000%
```
