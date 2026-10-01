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

import os
import shutil
import subprocess
import threading
import time
from typing import Callable, Tuple, Optional, List, Dict, Any

# Gigabyte keyboard constants
GIGABYTE_VENDOR_ID = 0x0414
GIGABYTE_PRODUCT_ID = 0x7A44

# Logitech constants
LOGITECH_VENDOR_ID = 0x046D
LOGITECH_G_PRO_WIRED_PID = 0xC088     # G Pro Wireless plugged in by cable
LOGITECH_LIGHTSPEED_PID = 0xC539      # Lightspeed receiver

# HID++ constants
HIDPP_LONG_MESSAGE = 0x11
DEVICE_INDEX_WIRELESS = 0x01
DEVICE_INDEX_WIRED = 0xFF
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
# Logitech G Pro Wireless (HID++ 2.0 via hidapi)
# ---------------------------------------------------------------------------
#
# Protocol verified against OpenRGB's LogitechProtocolCommon. Feature page
# 0x8070 (COLOR_LED_EFFECTS). Two things are required for a colour to actually
# stick, and both were missing from the original port:
#   1. The device must be put into software/direct control first via
#      SET_SW_CTL (function 0x80) - otherwise it stays on its onboard profile
#      and silently ignores colour writes (they still ACK).
#   2. The static colour goes through SET_EFFECT (function 0x30) as
#      [zone, mode=static, r, g, b] - a single-byte mode, not a 2-byte id.
# 0x8070 has no brightness byte for the static effect, so brightness is applied
# by scaling RGB.
LOGITECH_FP8070 = 0x8070
LOGITECH_FP8070_SET_EFFECT = 0x30
LOGITECH_FP8070_SET_SW_CTL = 0x80
LOGITECH_HIDPP_ROOT_GET_FEATURE = 0x00

# The GUI's apply worker and the reconnect watcher both talk HID++; responses are
# read back positionally, so two conversations at once would read each other's.
_logitech_lock = threading.Lock()


def _hidpp_call(device, device_index, feature_index, func, params=()):
    """Send a 20-byte HID++ long request and return the response list (or None)."""
    buf = bytearray(20)
    buf[0] = HIDPP_LONG_MESSAGE
    buf[1] = device_index
    buf[2] = feature_index
    buf[3] = func
    for i, p in enumerate(params):
        buf[4 + i] = p
    device.write(bytes(buf))
    resp = device.read(20, timeout_ms=500)
    return list(resp) if resp else None


def _set_logitech_color_hidpp(rgb: Tuple[int, int, int], brightness: int = 100) -> bool:
    """Set Logitech G Pro Wireless colour via HID++ 2.0 (feature 0x8070)."""
    try:
        import hid
    except ImportError:
        return False

    r, g, b = _clamp_rgb(rgb)
    brightness = max(0, min(100, int(brightness)))
    scale = brightness / 100.0
    r, g, b = int(r * scale), int(g * scale), int(b * scale)

    # Wireless: reached through the Lightspeed receiver as paired device index 1.
    # Wired (USB cable plugged in, e.g. while charging): the mouse enumerates
    # as its own USB device and answers on index 0xFF - the receiver then
    # stays silent, so every path has to be tried, not just the first that opens.
    pids_to_try = [
        (LOGITECH_LIGHTSPEED_PID, DEVICE_INDEX_WIRELESS),  # 0xC539 receiver
        (LOGITECH_G_PRO_WIRED_PID, DEVICE_INDEX_WIRED),    # 0xC088 over cable
    ]

    with _logitech_lock:
        for pid, dev_idx in pids_to_try:
            try:
                devices = hid.enumerate(LOGITECH_VENDOR_ID, pid)
            except Exception:
                continue
            for dev_info in devices:
                # HID++ long-message interface: usage_page=0xFF00, usage=2.
                if dev_info.get("usage_page", 0) == 0xFF00 and dev_info.get("usage", 0) == 2:
                    if _apply_logitech_color(hid, dev_info["path"], dev_idx, r, g, b):
                        return True
    return False


def _apply_logitech_color(hid, path, device_index, r, g, b) -> bool:
    """Run the 0x8070 colour sequence on one HID++ path; False if the mouse doesn't answer."""
    device = None
    try:
        device = hid.device()
        device.open_path(path)
        device.set_nonblocking(False)

        # Resolve the 0x8070 feature index for this device.
        resp = _hidpp_call(device, device_index, 0x00,
                           LOGITECH_HIDPP_ROOT_GET_FEATURE,
                           ((LOGITECH_FP8070 >> 8) & 0xFF, LOGITECH_FP8070 & 0xFF))
        if not resp or len(resp) < 5 or resp[2] == 0xFF or resp[4] == 0:
            return False
        feature_index = resp[4]

        # Discover how many LED zones the device exposes (getInfo); default to 2.
        info = _hidpp_call(device, device_index, feature_index, 0x00)
        zone_count = info[4] if info and len(info) > 4 and 0 < info[4] <= 8 else 2

        # 1) Hand LED control to the host, then 2) set each zone to a static colour.
        _hidpp_call(device, device_index, feature_index,
                    LOGITECH_FP8070_SET_SW_CTL, (0x01, 0x01))
        for zone in range(zone_count):
            _hidpp_call(device, device_index, feature_index,
                        LOGITECH_FP8070_SET_EFFECT, (zone, MODE_STATIC, r, g, b))
        return True
    except Exception:
        return False
    finally:
        if device is not None:
            try:
                device.close()
            except Exception:
                pass


# The mouse forgets a host-set colour whenever its link changes: plugging or
# unplugging the cable, or waking up from sleep, drops it back to the onboard
# profile (often dark). Nothing reports "colour lost", so the watcher listens
# for the events that cause it and re-applies the last colour:
#   - the receiver's HID++ 0x41 "device connection" notification (mouse came
#     back on the radio link: woke up, or the cable was pulled);
#   - the wired device (0xC088) appearing or disappearing.
HIDPP_SHORT_MESSAGE = 0x10
HIDPP_NOTIF_DEVICE_CONNECTION = 0x41
HIDPP_LINK_NOT_ESTABLISHED = 0x40
REAPPLY_DELAY_S = 1.0          # let the mouse settle on the new link first
WATCH_LOG = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                         "Controlus", "watcher.log")


def _watch_log(message: str) -> None:
    try:
        os.makedirs(os.path.dirname(WATCH_LOG), exist_ok=True)
        if os.path.exists(WATCH_LOG) and os.path.getsize(WATCH_LOG) > 256 * 1024:
            os.replace(WATCH_LOG, WATCH_LOG + ".old")
        with open(WATCH_LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except Exception:
        pass


class LogitechReconnectWatcher(threading.Thread):
    """Background thread that re-applies the mouse colour after a link change.

    `get_color` returns ((r, g, b), brightness) - the colour to restore.
    """

    def __init__(self, get_color: Callable[[], Tuple[Tuple[int, int, int], int]]):
        super().__init__(name="logitech-watcher", daemon=True)
        self._get_color = get_color
        self._stop = threading.Event()
        self._receiver = None
        self._wired = None           # unknown until the first poll
        self._reapply_at = None
        self._retries = 0

    def stop(self) -> None:
        self._stop.set()

    def _open_receiver(self, hid):
        """Open the receiver's short HID++ collection, where 0x41 notifications arrive."""
        try:
            for info in hid.enumerate(LOGITECH_VENDOR_ID, LOGITECH_LIGHTSPEED_PID):
                if info.get("usage_page") == 0xFF00 and info.get("usage") == 1:
                    dev = hid.device()
                    dev.open_path(info["path"])
                    # Receiver register 0x00 (notification flags): wireless
                    # (0x000100) + software present (0x000800), so connection
                    # events are reported at all.
                    dev.write(bytes([HIDPP_SHORT_MESSAGE, 0xFF, 0x80, 0x00, 0x00, 0x09, 0x00]))
                    _watch_log("receiver opened")
                    return dev
        except Exception as e:
            _watch_log(f"receiver open failed: {e}")
        return None

    def _schedule(self, reason: str) -> None:
        _watch_log(reason)
        self._reapply_at = time.monotonic() + REAPPLY_DELAY_S
        self._retries = 3

    def run(self) -> None:
        try:
            import hid
        except ImportError:
            return
        while not self._stop.is_set():
            if self._receiver is None:
                self._receiver = self._open_receiver(hid)

            # 1) Wired device appearing / disappearing.
            try:
                wired = bool(hid.enumerate(LOGITECH_VENDOR_ID, LOGITECH_G_PRO_WIRED_PID))
            except Exception:
                wired = self._wired
            if self._wired is not None and wired != self._wired:
                self._schedule("cable plugged in" if wired else "cable unplugged")
            self._wired = wired

            # 2) Receiver notifications; the read doubles as the loop's sleep.
            if self._receiver is not None:
                try:
                    msg = self._receiver.read(20, timeout_ms=1000)
                except Exception as e:
                    _watch_log(f"receiver lost: {e}")
                    try:
                        self._receiver.close()
                    except Exception:
                        pass
                    self._receiver = None
                    msg = None
                if (msg and len(msg) >= 5 and msg[0] == HIDPP_SHORT_MESSAGE
                        and msg[1] == DEVICE_INDEX_WIRELESS
                        and msg[2] == HIDPP_NOTIF_DEVICE_CONNECTION):
                    if msg[4] & HIDPP_LINK_NOT_ESTABLISHED:
                        _watch_log("radio link lost")
                    else:
                        self._schedule("radio link established")
            else:
                self._stop.wait(1.0)

            if self._reapply_at is not None and time.monotonic() >= self._reapply_at:
                self._reapply_at = None
                rgb, brightness = self._get_color()
                ok = _set_logitech_color_hidpp(rgb, brightness)
                _watch_log(f"re-applied {rgb} @ {brightness}%: {'ok' if ok else 'FAILED'}")
                if not ok and self._retries > 0:
                    # Link may not be up yet; try again a bit later.
                    self._retries -= 1
                    self._reapply_at = time.monotonic() + 2 * REAPPLY_DELAY_S

        if self._receiver is not None:
            try:
                self._receiver.close()
            except Exception:
                pass


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
            for pid, link in [(LOGITECH_G_PRO_WIRED_PID, "cable"),
                              (LOGITECH_LIGHTSPEED_PID, "receiver")]:
                if hid.enumerate(LOGITECH_VENDOR_ID, pid):
                    devices.append({
                        "name": "Logitech G Pro Wireless",
                        "type": "mouse",
                        "backend": "hidpp",
                        "link": link,
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
