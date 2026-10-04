"""
Controlus device backend - shared by the Windows app, the Linux app and the
GNOME extension helper. This file is the single source of truth for the
protocols; windows/controlus/backend.py and app/backend.py only re-export it.

Supports:
  - Gigabyte/AORUS laptop keyboard + light bar (HID feature reports)
  - Logitech G Pro Wireless mouse (HID++ 2.0)
  - Any OpenRGB-supported device (OpenRGB SDK / CLI)

Requires (optional, install what you have hardware for):
  pip install hidapi          # keyboard + mouse; on Linux its `hidraw` module is used
  pip install openrgb-python  # any OpenRGB-supported device (needs OpenRGB server running)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable, Tuple, Optional, List, Dict, Any

# Gigabyte keyboard constants
GIGABYTE_VENDOR_ID = 0x0414
GIGABYTE_PRODUCT_ID = 0x7A44          # AORUS laptop keyboard (3-zone RGB)
GIGABYTE_LIGHTBAR_PID = 0x7A43        # the light bar above it - a separate HID device

# Logitech constants
LOGITECH_VENDOR_ID = 0x046D
LOGITECH_G_PRO_WIRED_PID = 0xC088     # G Pro Wireless plugged in by cable
LOGITECH_LIGHTSPEED_PID = 0xC539      # Lightspeed receiver
LOGITECH_VIRTUAL_PID = 0x4079         # Linux: the mouse as hid-logitech-dj exposes it

# HID++ constants
HIDPP_LONG_MESSAGE = 0x11
DEVICE_INDEX_WIRELESS = 0x01
DEVICE_INDEX_WIRED = 0xFF
MODE_STATIC = 0x01


IS_WINDOWS = sys.platform == "win32"


def _hid():
    """The hidapi module, or None.

    On Linux the pip `hidapi` package ships two modules: `hid` is built on
    libusb, which detaches the kernel driver from the device it opens (the
    built-in keyboard would stop typing), while `hidraw` talks to /dev/hidraw*
    next to the kernel driver. Always prefer `hidraw` there.
    """
    names = ("hid",) if IS_WINDOWS else ("hidraw", "hid")
    for name in names:
        try:
            return __import__(name)
        except ImportError:
            continue
    return None


def config_dir() -> str:
    """Where config.json lives: %APPDATA%/Controlus or ~/.config/controlus."""
    if IS_WINDOWS:
        return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Controlus")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "controlus")


def state_dir() -> str:
    """Where logs go: next to the config on Windows, ~/.local/state/controlus on Linux."""
    if IS_WINDOWS:
        return config_dir()
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "controlus")


CONFIG_FILE = os.path.join(config_dir(), "config.json")


def load_config() -> Dict[str, Any]:
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"favorites": [], "last_color": None, "brightness": 100}


def save_config(config: Dict[str, Any]) -> None:
    os.makedirs(config_dir(), exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def saved_color(config: Optional[Dict[str, Any]] = None) -> Tuple[Tuple[int, int, int], int]:
    """((r, g, b), brightness) to restore from a config; black when lighting is off."""
    config = load_config() if config is None else config
    if not config.get("power", True):
        return (0, 0, 0), 0
    last = config.get("last_color") or {"r": 0, "g": 255, "b": 255}
    return (last["r"], last["g"], last["b"]), int(config.get("brightness", 100))


def remember_color(r: int, g: int, b: int, brightness: int) -> None:
    """Store an applied colour so the watcher / next start can restore it."""
    config = load_config()
    if (r, g, b) == (0, 0, 0) or brightness == 0:
        config["power"] = False
    else:
        config["power"] = True
        config["last_color"] = {"r": r, "g": g, "b": b}
        config["brightness"] = brightness
    save_config(config)


def _clamp_rgb(rgb: Tuple[int, int, int]) -> Tuple[int, int, int]:
    return tuple(max(0, min(255, int(v))) for v in rgb)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Gigabyte / AORUS laptop keyboard + light bar (HID feature reports via hidapi)
# ---------------------------------------------------------------------------
#
# Every command is a 9-byte feature report [0, cmd, b2..b7, checksum] with
# checksum = 255 - sum(bytes[1..7]). The sequence below is exactly what
# Gigabyte Control Center 26.09 sends, recorded by hooking its HID calls with
# Frida (research/usb-capture/hidhook.py, 2026-10-02):
#
#   keyboard  [08, 0A, r, g, b, 32, 00]   zone 0x0A = whole keyboard
#   light bar [08, 01, 09, r, g, b, 00]
#
#   Brightness is not a byte: GCC scales RGB (its slider minimum turns CC00FF
#   into 280033), and both devices ignore the brightness fields anyway. GCC
#   sends no effect command when it applies a colour, and neither do we.
#
#   cmd 0x08 is overloaded by byte 2: 0 = SetLightEffect [type, speed,
#   bright, color, dir]. The old code looped "zones" 0..9 + 0xFF and so fired
#   garbage effect/sync commands - why brightness never changed and the light
#   bar went dark.
#
#   This laptop's light bar has dead blue LEDs: even GCC cannot light it
#   blue, so blues look dark and white comes out without its blue part.
GIGA_CMD_EFFECT = 0x08
GIGA_CMD_IDLE = 0x0A
GIGA_ZONE_ALL = 0x0A
GIGA_LEVEL = 0x32                  # what GCC always sends; the firmware ignores it
GIGA_DELAY_S = 0.065               # GCC sleeps this long after every report


def _giga_packet(*body: int) -> bytes:
    payload = [b & 0xFF for b in body] + [0] * (7 - len(body))
    return bytes([0x00] + payload + [(255 - sum(payload)) & 0xFF])


def _open_giga_control(hid, pid: int):
    """Open the collection of 0414:pid that accepts 9-byte feature reports.

    The device exposes ~11 HID collections and only the vendor one takes the
    report; hidapi returns -1 on the others without raising. The probe is the
    idle-timeout-off command, which GCC sends anyway, so it is harmless.
    """
    try:
        candidates = hid.enumerate(GIGABYTE_VENDOR_ID, pid)
    except Exception:
        return None
    for info in candidates:
        device = None
        try:
            device = hid.device()
            device.open_path(info["path"])
            written = device.send_feature_report(_giga_packet(GIGA_CMD_IDLE, 0x01))
            if written is not None and written > 0:
                return device
        except Exception:
            pass
        if device is not None:
            try:
                device.close()
            except Exception:
                pass
    return None


def _set_color_hidraw(rgb: Tuple[int, int, int], brightness: int = 100) -> bool:
    """Set the AORUS keyboard and its light bar the way GCC does."""
    hid = _hid()
    if hid is None:
        return False

    brightness = max(0, min(100, int(brightness)))
    r, g, b = (round(c * brightness / 100) for c in _clamp_rgb(rgb))
    ok = False

    for pid, packet in (
        (GIGABYTE_PRODUCT_ID, _giga_packet(GIGA_CMD_EFFECT, GIGA_ZONE_ALL, r, g, b, GIGA_LEVEL, 0)),
        (GIGABYTE_LIGHTBAR_PID, _giga_packet(GIGA_CMD_EFFECT, 0x01, 0x09, r, g, b, 0)),
    ):
        dev = _open_giga_control(hid, pid)
        if dev is None:
            continue
        try:
            dev.send_feature_report(packet)
            time.sleep(GIGA_DELAY_S)
            ok = True
        except Exception:
            pass
        finally:
            dev.close()
    return ok


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
    hid = _hid()
    if hid is None:
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
        (LOGITECH_VIRTUAL_PID, DEVICE_INDEX_WIRED),        # Linux fallback
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
#   - the mouse's own device appearing or disappearing: 0xC088 when the cable
#     goes in or out, and on Linux 0x4079, which hid-logitech-dj creates when
#     the mouse connects over radio and removes when it goes away.
# On Linux the receiver belongs to hid-logitech-dj, so the watcher only reads
# from it and never writes its registers.
HIDPP_SHORT_MESSAGE = 0x10
HIDPP_NOTIF_DEVICE_CONNECTION = 0x41
HIDPP_LINK_NOT_ESTABLISHED = 0x40
REAPPLY_DELAY_S = 1.0          # let the mouse settle on the new link first
WATCH_LOG = os.path.join(state_dir(), "watcher.log")
WATCHED_PIDS = ((LOGITECH_G_PRO_WIRED_PID,) if IS_WINDOWS
                else (LOGITECH_G_PRO_WIRED_PID, LOGITECH_VIRTUAL_PID))


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
        self._present = None         # which WATCHED_PIDS exist; unknown until the first poll
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
                    if IS_WINDOWS:
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
        hid = _hid()
        if hid is None:
            return
        while not self._stop.is_set():
            if self._receiver is None:
                self._receiver = self._open_receiver(hid)

            # 1) The mouse's own device appearing / disappearing.
            try:
                present = frozenset(pid for pid in WATCHED_PIDS
                                    if hid.enumerate(LOGITECH_VENDOR_ID, pid))
            except Exception:
                present = self._present
            if self._present is not None and present != self._present:
                came = ", ".join(f"{p:04x}" for p in present - self._present) or "-"
                gone = ", ".join(f"{p:04x}" for p in self._present - present) or "-"
                self._schedule(f"mouse device change: +{came} -{gone}")
            self._present = present

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
# OpenRGB (cross-platform SDK / CLI)
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

    hid = _hid()

    # Gigabyte keyboard
    if hid is not None:
        try:
            if hid.enumerate(GIGABYTE_VENDOR_ID, GIGABYTE_PRODUCT_ID):
                devices.append({
                    "name": "Gigabyte/AORUS Keyboard",
                    "type": "keyboard",
                    "backend": "hid",
                })
            if hid.enumerate(GIGABYTE_VENDOR_ID, GIGABYTE_LIGHTBAR_PID):
                devices.append({
                    "name": "Gigabyte/AORUS Light bar",
                    "type": "lightbar",
                    "backend": "hid",
                })
        except Exception:
            pass

    # Logitech via hidapi
    if hid is not None:
        try:
            for pid, link in [(LOGITECH_G_PRO_WIRED_PID, "cable"),
                              (LOGITECH_LIGHTSPEED_PID, "receiver"),
                              (LOGITECH_VIRTUAL_PID, "receiver")]:
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
