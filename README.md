# Controlus

Controlus is a lightweight utility for controlling RGB lighting on Gigabyte AORUS keyboards and Logitech G Pro Wireless mice on Linux. It includes both a standalone GUI application and a GNOME Shell extension for seamless integration with Quick Settings.

## Features

- **Dual Interface**: Standalone Python GUI and GNOME Shell Extension.
- **Hardware Support**:
  - Gigabyte AORUS Keyboards.
  - Logitech G Pro Wireless (via Lightspeed receiver).
- **Fast & Lightweight**: Minimal overhead, direct hardware communication.
- **Persistence**: Restore colors automatically on boot/login.
- **GNOME Integration**: Control RGB directly from the Quick Settings menu (GNOME 49+).

## Repository Structure

- `app/`: Standalone Python application (GUI and backend).
- `extension/`: GNOME Shell extension files.
- `install.sh`: Unified installation script.

## Installation

You can install either the standalone application, the GNOME extension, or both.

### Quick Install

```bash
git clone https://github.com/synitself/Controlus.git
cd Controlus
chmod +x install.sh
./install.sh
```

Follow the on-screen prompts to choose which components to install.

### Requirements

- `python3`
- `python-gobject` (for GUI)
- `gnome-shell` 49+ (for extension)
- `hidapi` (optional, for enhanced Logitech support)

## Usage

### Standalone Application
Run `controlus` from your application menu or terminal.

### GNOME Extension
Open Quick Settings (Super+S) to find the "Keyboard RGB" panel.

## License
MIT (or choice - check source files)
