"""
Controlus backends for Windows: hidapi (HID feature reports / HID++) + OpenRGB.

Supports:
  - Gigabyte/AORUS keyboards (via HID feature reports)
  - Logitech G Pro Wireless mouse (via HID++)
  - Any OpenRGB-supported device (via the OpenRGB SDK / CLI)

This is the Windows port of the original Linux backend. On Linux the keyboard
was driven through /dev/hidraw* + fcntl.ioctl(HIDIOCSFEATURE); on Windows we use
the cross-platform `hidapi` library's send_feature_report() instead.

Requires (optional, install what you have hardware for):
  pip install hidapi          # Gigabyte keyboard + Logitech mouse
  pip install openrgb-python  # any OpenRGB-supported device (needs OpenRGB server running)
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Tuple, Optional, List, Dict, Any

# Gigabyte keyboard constants
GIGABYTE_VENDOR_ID = 0x0414
GIGABYTE_PRODUCT_ID = 0x7A44

# Logitech constants
LOGITECH_VENDOR_ID = 0x046D
LOGITECH_G_PRO_WIRELESS_PID = 0x4079  # Virtual wireless device
LOGITECH_LIGHTSPEED_PID = 0xC539      # Lightspeed receiver

# HID++ constants
HIDPP_LONG_MESSAGE = 0x11
DEVICE_INDEX_WIRELESS = 0x01
MODE_STATIC = 0x01


def _clamp_rgb(rgb: Tuple[int, int, int]) -> Tuple[int, int, int]:
    return tuple(max(0, min(255, int(v))) for v in rgb)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Gigabyte / AORUS keyboard (HID feature reports via hidapi)
# ---------------------------------------------------------------------------

def _build_keyboard_packet(zone: int, r: int, g: int, b: int, brightness: int) -> bytes:
    """9-byte Gigabyte SetZoneColors feature report.

    Layout (verified against Gigabyte Control Center's IteKeyBoard.SetZoneColors):
        [reportID=0, cmd=0x08, zoneIndex, r, g, b, brightness, 0, checksum]
    checksum = 255 - sum(bytes[1..7]).
    """
    packet = [0x00, 0x08, zone, r, g, b, brightness, 0x00]
    checksum = (255 - sum(packet[1:8])) & 0xFF
    packet.append(checksum)
    return bytes(packet)


# Gigabyte's keyboard backlight has a firmware idle timeout ("one minute mode")
# that dims/turns off the backlight after inactivity. GCC disables it by sending
# command 0x0A with OnOff=1 (IteKeyBoard.SetKeyboardBackLightOneMinuteMode).
# Without this the colour applies but the backlight pulses off/on on its own.
def _build_keyboard_idle_packet(disable: bool = True) -> bytes:
    """9-byte report toggling the backlight idle timeout: [0, 0x0A, OnOff, 0..., checksum]."""
    packet = [0x00, 0x0A, 0x01 if disable else 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
    checksum = (255 - sum(packet[1:8])) & 0xFF
    packet.append(checksum)
    return bytes(packet)


def _set_color_hidraw(rgb: Tuple[int, int, int], brightness: int = 100) -> bool:
    """Set Gigabyte keyboard color via HID feature reports.

    Sends the same 9-byte feature report the Linux driver sent through
    HIDIOCSFEATURE, but using hidapi so it works on Windows.

    The keyboard exposes ~11 HID collections; only the vendor RGB interface
    actually accepts the report. hidapi's send_feature_report returns the
    number of bytes written and **-1 on failure without raising**, so we must
    check the return value rather than just catching exceptions: most
    collections silently return -1 and only the real control interface writes
    the full report. We try every matching path until a write succeeds.
    """
    try:
        import hid
    except ImportError:
        return False

    r, g, b = _clamp_rgb(rgb)
    brightness = max(0, min(100, int(brightness)))

    try:
        candidates = hid.enumerate(GIGABYTE_VENDOR_ID, GIGABYTE_PRODUCT_ID)
    except Exception:
        return False
    if not candidates:
        return False

    for info in candidates:
        device = None
        try:
            device = hid.device()
            device.open_path(info["path"])
            # Probe zone 0xFF (all zones) first; if the write doesn't take on
            # this collection, skip it without spamming the others.
            probe = device.send_feature_report(_build_keyboard_packet(0xFF, r, g, b, brightness))
            if probe is None or probe <= 0:
                continue
            for zone in list(range(10)) + [0xFF]:
                device.send_feature_report(_build_keyboard_packet(zone, r, g, b, brightness))
            # Disable the firmware idle timeout so the backlight stays lit.
            device.send_feature_report(_build_keyboard_idle_packet(disable=True))
            return True
        except Exception:
            # Wrong interface for this report; try the next one.
            pass
        finally:
            if device is not None:
                try:
                    device.close()
                except Exception:
                    pass

    return False


# ---------------------------------------------------------------------------
# Logitech G Pro Wireless (HID++ via hidapi) - unchanged from Linux backend
# ---------------------------------------------------------------------------

def _set_logitech_color_hidpp(rgb: Tuple[int, int, int], brightness: int = 100) -> bool:
    """Set Logitech G Pro color via HID++ protocol using hidapi."""
    try:
        import hid
    except ImportError:
        return False

    r, g, b = _clamp_rgb(rgb)
    brightness = max(0, min(100, int(brightness)))

    # Try G Pro Wireless via Lightspeed receiver first (most reliable)
    pids_to_try = [
        (LOGITECH_LIGHTSPEED_PID, DEVICE_INDEX_WIRELESS),  # 0xC539 receiver, paired device
        (LOGITECH_G_PRO_WIRELESS_PID, 0xFF),               # 0x4079 virtual device
    ]

    device = None
    device_index = DEVICE_INDEX_WIRELESS

    for pid, dev_idx in pids_to_try:
        try:
            devices = hid.enumerate(LOGITECH_VENDOR_ID, pid)
        except Exception:
            continue
        for dev_info in devices:
            # HID++ interface: usage_page=0xFF00, usage=2 for long messages
            usage_page = dev_info.get("usage_page", 0)
            usage = dev_info.get("usage", 0)
            if usage_page == 0xFF00 and usage == 2:
                try:
                    device = hid.device()
                    device.open_path(dev_info["path"])
                    device.set_nonblocking(False)
                    device_index = dev_idx
                    break
                except Exception:
                    continue
        if device:
            break

    if not device:
        return False

    try:
        # Find the RGB Effects feature index (feature pages 0x8070 / 0x8071)
        rgb_feature_index = None
        for feature_page in [0x8070, 0x8071]:
            query_data = bytearray(20)
            query_data[0] = HIDPP_LONG_MESSAGE
            query_data[1] = device_index
            query_data[2] = 0x00  # Root feature
            query_data[3] = 0x00  # Function: get feature index
            query_data[4] = (feature_page >> 8) & 0xFF
            query_data[5] = feature_page & 0xFF

            device.write(bytes(query_data))
            response = device.read(20, timeout_ms=500)

            if response and len(response) >= 5 and response[4] != 0:
                rgb_feature_index = response[4]
                break

        if not rgb_feature_index:
            device.close()
            return False

        # Set color for each zone (0=battery indicator, 1=logo)
        for zone in [0, 1]:
            data = bytearray(20)
            data[0] = HIDPP_LONG_MESSAGE
            data[1] = device_index
            data[2] = rgb_feature_index
            data[3] = 0x30  # SET_LED_EFFECT for FP8070/8071
            data[4] = zone
            data[5] = MODE_STATIC
            data[6] = r
            data[7] = g
            data[8] = b
            data[9] = 0x00   # Speed high
            data[10] = 0x00  # Speed low
            data[11] = 0x00  # Curve type
            data[12] = brightness
            data[16] = 0x01  # Persistence

            device.write(bytes(data))
            device.read(20, timeout_ms=200)

        device.close()
        return True
    except Exception:
        if device:
            device.close()
        return False


# ---------------------------------------------------------------------------
# OpenRGB (cross-platform SDK / CLI) - unchanged from Linux backend
# ---------------------------------------------------------------------------

def _set_color_openrgb(rgb: Tuple[int, int, int], brightness: int = 100) -> bool:
    """Set color via OpenRGB SDK (supports many devices including Logitech)."""
    try:
        from openrgb import OpenRGBClient
        from openrgb.utils import DeviceType, RGBColor
    except Exception:
        return False

    r, g, b = _clamp_rgb(rgb)
    brightness = max(0, min(100, int(brightness)))
    scale = brightness / 100.0
    color = RGBColor(int(r * scale), int(g * scale), int(b * scale))

    try:
        client = OpenRGBClient(name="Controlus", timeout=1.0)
    except Exception:
        return False

    try:
        devices = client.devices
        target_types = (DeviceType.KEYBOARD, DeviceType.LIGHT, DeviceType.MOUSE, DeviceType.MOTHERBOARD)
        for dev in devices:
            try:
                if getattr(dev, "type", None) in target_types:
                    dev.set_color(color)
            except Exception:
                continue
        return True
    except Exception:
        return False


def _set_color_openrgb_cli(rgb: Tuple[int, int, int], brightness: int = 100) -> bool:
    """Set color via OpenRGB CLI (fallback if SDK not available)."""
    if not shutil.which("openrgb"):
        return False

    r, g, b = _clamp_rgb(rgb)
    brightness = max(0, min(100, int(brightness)))
    scale = brightness / 100.0
    r, g, b = int(r * scale), int(g * scale), int(b * scale)
    color_hex = f"{r:02X}{g:02X}{b:02X}"

    try:
        result = subprocess.run(
            ["openrgb", "-m", "Static", "-c", color_hex],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_available_devices() -> List[Dict[str, Any]]:
    """Get list of available RGB devices."""
    devices: List[Dict[str, Any]] = []

    try:
        import hid
    except ImportError:
        hid = None  # type: ignore[assignment]

    # Gigabyte keyboard
    if hid is not None:
        try:
            if hid.enumerate(GIGABYTE_VENDOR_ID, GIGABYTE_PRODUCT_ID):
                devices.append({
                    "name": "Gigabyte/AORUS Keyboard",
                    "type": "keyboard",
                    "backend": "hid",
                })
        except Exception:
            pass

    # Logitech via hidapi
    if hid is not None:
        try:
            for pid in [LOGITECH_LIGHTSPEED_PID, LOGITECH_G_PRO_WIRELESS_PID]:
                if hid.enumerate(LOGITECH_VENDOR_ID, pid):
                    devices.append({
                        "name": "Logitech G Pro Wireless",
                        "type": "mouse",
                        "backend": "hidpp",
                    })
                    break
        except Exception:
            pass

    # OpenRGB devices
    try:
        from openrgb import OpenRGBClient
        client = OpenRGBClient(name="Controlus-probe", timeout=1.0)
        for dev in client.devices:
            devices.append({
                "name": dev.name,
                "type": str(dev.type).split(".")[-1].lower(),
                "backend": "openrgb",
            })
    except Exception:
        pass

    return devices


def set_color(r: int, g: int, b: int, brightness: int = 100) -> Tuple[bool, str]:
    """Set color on ALL available devices.

    Tries all backends to ensure every connected device gets the same color.

    Returns (success, message) where success is True if at least one device was set.
    """
    rgb = (r, g, b)
    successful_backends: List[str] = []
    failed_backends: List[str] = []

    if _set_color_openrgb(rgb, brightness):
        successful_backends.append("OpenRGB")
    else:
        failed_backends.append("OpenRGB SDK")

    if _set_logitech_color_hidpp(rgb, brightness):
        successful_backends.append("Logitech")
    else:
        failed_backends.append("Logitech HID++")

    if _set_color_hidraw(rgb, brightness):
        successful_backends.append("Keyboard")
    else:
        failed_backends.append("Gigabyte HID")

    if not successful_backends:
        if _set_color_openrgb_cli(rgb, brightness):
            successful_backends.append("OpenRGB CLI")
        else:
            failed_backends.append("OpenRGB CLI")

    if successful_backends:
        return True, f"{'+'.join(successful_backends)} RGB({r}, {g}, {b}) at {brightness}%"

    return False, f"No backend available (tried: {', '.join(failed_backends)})"


def get_current_color() -> Optional[Tuple[int, int, int]]:
    """Try to read current RGB color from devices.

    Most RGB protocols are write-only. This tries OpenRGB, which caches the
    last set color. Returns (r, g, b) or None if unable to read.
    """
    try:
        from openrgb import OpenRGBClient
        from openrgb.utils import DeviceType

        client = OpenRGBClient(name="Controlus-reader", timeout=1.0)
        for dev in client.devices:
            if dev.type in (DeviceType.KEYBOARD, DeviceType.MOUSE):
                if dev.colors and len(dev.colors) > 0:
                    c = dev.colors[0]
                    return (c.red, c.green, c.blue)
    except Exception:
        pass

    return None


def detect_current_settings() -> Dict[str, Any]:
    """Detect current RGB settings from devices.

    Returns dict with 'color' (r,g,b tuple or None), 'brightness' (0-100 or None),
    and 'devices' list.
    """
    result: Dict[str, Any] = {
        "color": None,
        "brightness": None,
        "devices": get_available_devices(),
    }

    color = get_current_color()
    if color:
        result["color"] = color
        result["brightness"] = max(color) * 100 // 255 if max(color) > 0 else 100

    return result
