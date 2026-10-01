# Controlus — RGB lighting control

Controls RGB on **Gigabyte AORUS keyboards** and **Logitech G Pro Wireless
mice** by talking to the hardware directly over HID, without vendor software.
Started on Linux; Windows port (PySide6 + hidapi) landed 2026-06-23. Mouse
support uses the HID++ protocol over the Lightspeed receiver.

Has **its own git repo** — run git from this folder. See `README.md` for
features and install steps.

## Layout

| path | what |
|---|---|
| `app/` | standalone Python GUI |
| `extension/` | GNOME Shell extension (Quick Settings integration) |
| `windows/` | Windows port |
| `install.sh` | Linux installer |
| `99-controlus.rules` | udev rules for non-root HID access |
| `research/` | reverse-engineering input, see below |

## Mouse traps (Windows)

- The G Pro Wireless has two HID++ paths: via the Lightspeed receiver
  (`046D:C539`, device index `0x01`) and, when the cable is plugged in, as its
  own USB device (`046D:C088`, index `0xFF`). When wired, the receiver stays
  silent — so every path has to be tried. Fixed 2026-10-01; before that a
  plugged-in mouse was never recoloured. (`0x4079` is the mouse's ID *inside*
  the radio link and never shows up as a USB device.)
- The mouse drops a host-set colour on every link change (cable in/out,
  waking from sleep). `LogitechReconnectWatcher` in `windows/controlus/backend.py`
  listens for the receiver's HID++ `0x41` notification and for `C088`
  appearing/disappearing, and re-applies the last colour. It runs whenever
  Controlus runs (tray included), and logs to `%APPDATA%\Controlus\watcher.log`. Added
  2026-10-01; whether the `0x41` path fires on this receiver is not confirmed yet.
- Windows GUI redesigned 2026-10-01: frameless Sota-VPN-style window (own
  title bar, DWM rounded corners), colour ring around a glowing power button,
  live apply (debounced, worker thread), tray icon tinted with the current
  colour, autostart via HKCU Run `--tray`, single instance via QLocalServer.
  Build is one-folder (`dist/Controlus/`), not one-file — see the spec comment.

## Keyboard traps (AORUS laptop, Windows)

- Two HID devices: `0414:7A44` is the 3-zone keyboard, `0414:7A43` is the
  **light bar** above it. GCC opens them separately; the old code never
  touched 7A43.
- Command `0x08` is overloaded by byte 2: `0` = SetLightEffect
  `[type, speed, brightness, color, dir]`. The old loop over "zones"
  0..9+0xFF fired garbage effect/sync commands — why brightness never changed
  and the light bar went dark (Fn+↑ revived it).
- **What GCC actually sends** (recorded 2026-10-02 by hooking GCC 26.09's HID
  calls with Frida, `research/usb-capture/hidhook.py` / `hidspawn.py`):
  keyboard `[08, 0A, r, g, b, 32, 00]` (zone 0x0A = whole keyboard), light bar
  `[08, 01, 09, r, g, b, 00]`. Brightness = scaling RGB on both; the
  brightness fields are ignored. No effect command on apply or at startup.
  Controlus now sends exactly this.
- **The light bar's blue LEDs are dead** on this laptop: GCC itself cannot
  light it blue. So blue = dark bar, white has no blue in it, purple looks
  red. Earlier probes (`windows/tools/light_probe.py`, `lightbar_probe.py`)
  misread this as "the command does not work" — don't re-chase it.
- USBPcap 1.5.4 does not work here: `\\.\USBPcapN` exist but opening fails
  with error 2 (USB4 host). Hook the process instead of sniffing the bus.
- Since 2026-07-18 Windows has `logi_lamparray_service` (Logitech's Dynamic
  Lighting driver). With Dynamic Lighting on, Windows drives the same LEDs and
  can overwrite whatever Controlus set.

## research/ — where the protocol came from

The AORUS keyboard protocol was recovered by tearing apart Gigabyte Control
Center rather than from documentation.

- `research/GCC_26.03.31.01.zip` — the original 897 MB vendor installer
  (`GIGABYTE Control Center_2026_Mar_release_All_Setup_26.03.31.01.exe`).
- `research/gcc-extracted/` — its unpacked internals: `kbd/`,
  `KeyboardDomainLogic2023/`, `KeyboardModel2023/`, `core15/`, plus the
  analysis scripts used to mine them (`dbdump.py`, `find.py`, `classes.txt`).

**`research/` is ~3 GB and untracked by git.** It shows up as `?? research/`
in `git status`. Add it to `.gitignore` if that noise gets annoying — the repo
currently has no `.gitignore` at all.
