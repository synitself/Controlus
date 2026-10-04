# Controlus

Controlus is a lightweight utility for controlling RGB lighting on Gigabyte AORUS
laptops (keyboard + light bar) and Logitech G Pro Wireless mice, talking to the
hardware directly — no Gigabyte Control Center, no G HUB. It runs on **Linux**
(standalone GTK app and GNOME Shell extension) and **Windows** (tray app, see
[windows/README.md](windows/README.md)).

## Features

- **Hardware support**
  - Gigabyte AORUS laptop keyboard (`0414:7A44`) and the light bar above it (`0414:7A43`).
  - Logitech G Pro Wireless over the Lightspeed receiver *and* over the cable.
  - Anything OpenRGB supports, if an OpenRGB server is running.
- **Real brightness**: the firmware ignores its brightness bytes, so Controlus
  scales the colour — exactly what Gigabyte Control Center does.
- **Keeps the mouse colour**: the G Pro forgets a host-set colour whenever it
  reconnects (cable in/out, waking up). A tiny background service notices and
  re-applies it.
- **Restores colours at login**.
- **GNOME integration**: control RGB from Quick Settings (GNOME 49+).

## Repository structure

| path | what |
|---|---|
| `common/controlus_backend.py` | the device protocols — the one implementation everything uses |
| `app/` | Linux GTK app, `controlus-watcher` background service, `controlus-detect` |
| `extension/` | GNOME Shell extension and its command-line helper |
| `windows/` | Windows tray app (PySide6), build scripts |
| `install.sh` | Linux installer |
| `99-controlus.rules` | udev rules so no root is needed |

## Linux

### Install

```bash
git clone https://github.com/synitself/Controlus.git
cd Controlus
./install.sh          # or: ./install.sh app | extension | all | uninstall
```

The installer copies the backend to `/usr/local/lib/controlus`, installs the udev
rules, the app and/or the extension, and enables the user service
`controlus.service` (`systemctl --user status controlus`).

### Requirements

- `python3`, `python-gobject` + GTK 4 / libadwaita (for the app)
- **hidapi** for Python — Controlus uses its `hidraw` module:
  `pip install --user hidapi` (or `sudo pacman -S python-hidapi` on Arch).
  Do not rely on the libusb-based `hid` module alone: opening the built-in
  keyboard through libusb detaches it from the kernel and it stops typing.
- `gnome-shell` 49+ (for the extension)
- `openrgb` + `openrgb-python` (optional, for other OpenRGB devices)

### Usage

- **App**: run `controlus` from the application menu or a terminal.
- **Extension**: open Quick Settings to find the "Keyboard RGB" panel.
- **CLI**: `controlus-helper set-color 255 0 128 70`, `controlus-helper status`,
  `controlus-detect`.
- The last colour is stored in `~/.config/controlus/config.json`; the
  background service logs mouse reconnects to `~/.local/state/controlus/watcher.log`.

## Known hardware quirk

On the AORUS this was developed on, the light bar's blue LEDs are dead — even
Gigabyte Control Center cannot light it blue. If yours shows red/green but not
blue, that is the hardware, not Controlus.

## License

MIT — see [LICENSE](LICENSE).
