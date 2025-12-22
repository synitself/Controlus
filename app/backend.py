"""
Controlus backends: OpenRGB preferred, hidraw fallback.

Supports:
  - Gigabyte/AORUS keyboards (via HID)
  - Logitech G Pro Wireless mouse (via HID++ or OpenRGB)
  - Any OpenRGB-supported device

Requires (optional): 
  pacman -S openrgb  # app
  pip install openrgb-python hidapi  # SDK client lib
"""

from __future__ import annotations

import os
import glob
import fcntl
import subprocess
import shutil
from typing import Tuple, Optional, List, Dict, Any

# HID constants for Gigabyte keyboard
HIDIOCSFEATURE = 0xC0094806
GIGABYTE_VENDOR_ID = 0x0414
GIGABYTE_PRODUCT_ID = 0x7A44

# Logitech constants
LOGITECH_VENDOR_ID = 0x046D
LOGITECH_G_PRO_WIRELESS_PID = 0x4079  # Virtual wireless device
LOGITECH_LIGHTSPEED_PID = 0xC539     # Lightspeed receiver

# HID++ constants
HIDPP_LONG_MESSAGE = 0x11
DEVICE_INDEX_WIRELESS = 0x01
MODE_STATIC = 0x01


def _find_hidraw_device(vendor_id: int, product_id: int) -> str | None:
    """Find hidraw device by vendor and product ID"""
    for hidraw in glob.glob('/dev/hidraw*'):
        try:
            device_path = f'/sys/class/hidraw/{os.path.basename(hidraw)}/device'
            for meta in ('modalias', 'uevent'):
                p = f'{device_path}/{meta}'
                if os.path.exists(p):
                    content = open(p, 'r').read().lower()
                    if f'{vendor_id:04x}' in content and f'{product_id:04x}' in content:
                        return hidraw
        except Exception:
            continue
    return None


def _find_gigabyte_keyboard() -> str | None:
    """Find Gigabyte/AORUS keyboard hidraw device"""
    return _find_hidraw_device(GIGABYTE_VENDOR_ID, GIGABYTE_PRODUCT_ID)


def _set_color_hidraw(rgb: Tuple[int, int, int], brightness: int = 100) -> bool:
    """Set Gigabyte keyboard color via hidraw"""
    r, g, b = (max(0, min(255, v)) for v in rgb)
    brightness = max(0, min(100, int(brightness)))

    dev = _find_gigabyte_keyboard()
    if not dev:
        return False
    try:
        with open(dev, 'r+b', buffering=0) as fd:
            for zone in list(range(10)) + [0xFF]:
                packet = [0x00, 0x08, zone, r, g, b, brightness, 0x00]
                checksum = (255 - sum(packet[1:8])) & 0xFF
                packet.append(checksum)
                buf = bytearray(9)
                buf[:] = bytes(packet)
                fcntl.ioctl(fd.fileno(), HIDIOCSFEATURE, buf)
        return True
    except Exception:
        return False


def _set_logitech_color_hidpp(rgb: Tuple[int, int, int], brightness: int = 100) -> bool:
    """Set Logitech G Pro color via HID++ protocol using hidapi"""
    try:
        import hid
    except ImportError:
        return False
    
    r, g, b = (max(0, min(255, v)) for v in rgb)
    brightness = max(0, min(100, int(brightness)))
    
    # Try to find G Pro Wireless via Lightspeed receiver first (most reliable)
    pids_to_try = [
        (LOGITECH_LIGHTSPEED_PID, DEVICE_INDEX_WIRELESS),  # 0xC539 Receiver, paired device
        (LOGITECH_G_PRO_WIRELESS_PID, 0xFF),               # 0x4079 Virtual device
    ]
    
    device = None
    device_index = DEVICE_INDEX_WIRELESS
    
    for pid, dev_idx in pids_to_try:
        devices = hid.enumerate(LOGITECH_VENDOR_ID, pid)
        for dev_info in devices:
            # HID++ interface: usage_page=0xFF00, usage=2 for long messages
            usage_page = dev_info.get('usage_page', 0)
            usage = dev_info.get('usage', 0)
            if usage_page == 0xFF00 and usage == 2:
                try:
                    device = hid.device()
                    device.open_path(dev_info['path'])
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
        # Try RGB Effects feature pages (0x8070, 0x8071)
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
            data[9] = 0x00  # Speed high
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


def _set_color_openrgb(rgb: Tuple[int, int, int], brightness: int = 100) -> bool:
    """Set color via OpenRGB SDK (supports many devices including Logitech)"""
    try:
        from openrgb import OpenRGBClient
        from openrgb.utils import DeviceType, RGBColor
    except Exception:
        return False

    r, g, b = (max(0, min(255, v)) for v in rgb)
    brightness = max(0, min(100, int(brightness)))
    scale = brightness / 100.0
    color = RGBColor(int(r * scale), int(g * scale), int(b * scale))

    try:
        client = OpenRGBClient(name='Controlus', timeout=1.0)
    except Exception:
        return False

    try:
        devices = client.devices
        # Target devices: keyboard, mouse, light bar, motherboard
        target_types = (DeviceType.KEYBOARD, DeviceType.LIGHT, DeviceType.MOUSE, DeviceType.MOTHERBOARD)
        for dev in devices:
            try:
                if getattr(dev, 'type', None) in target_types:
                    dev.set_color(color)
            except Exception:
                continue
        return True
    except Exception:
        return False


def _set_color_openrgb_cli(rgb: Tuple[int, int, int], brightness: int = 100) -> bool:
    """Set color via OpenRGB CLI (fallback if SDK not available)"""
    if not shutil.which('openrgb'):
        return False
    
    r, g, b = (max(0, min(255, v)) for v in rgb)
    brightness = max(0, min(100, int(brightness)))
    scale = brightness / 100.0
    r, g, b = int(r * scale), int(g * scale), int(b * scale)
    color_hex = f"{r:02X}{g:02X}{b:02X}"
    
    try:
        # Set color for all devices
        result = subprocess.run(
            ['openrgb', '-m', 'Static', '-c', color_hex],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def get_available_devices() -> List[Dict[str, Any]]:
    """Get list of available RGB devices"""
    devices = []
    
    # Check Gigabyte keyboard
    if _find_gigabyte_keyboard():
        devices.append({
            'name': 'Gigabyte/AORUS Keyboard',
            'type': 'keyboard',
            'backend': 'hidraw'
        })
    
    # Check Logitech via hidapi
    try:
        import hid
        for pid in [LOGITECH_LIGHTSPEED_PID, LOGITECH_G_PRO_WIRELESS_PID]:
            devs = hid.enumerate(LOGITECH_VENDOR_ID, pid)
            if devs:
                devices.append({
                    'name': 'Logitech G Pro Wireless',
                    'type': 'mouse',
                    'backend': 'hidpp'
                })
                break
    except ImportError:
        pass
    
    # Check OpenRGB devices
    try:
        from openrgb import OpenRGBClient
        client = OpenRGBClient(name='Controlus-probe', timeout=1.0)
        for dev in client.devices:
            devices.append({
                'name': dev.name,
                'type': str(dev.type).split('.')[-1].lower(),
                'backend': 'openrgb'
            })
    except:
        pass
    
    return devices


def set_color(r: int, g: int, b: int, brightness: int = 100) -> Tuple[bool, str]:
    """Set color on ALL available devices.

    Tries all backends to ensure all devices get the same color.
    
    Returns (success, message) where success is True if at least one device was set.
    """
    rgb = (r, g, b)
    successful_backends = []
    failed_backends = []
    
    # Try OpenRGB SDK (covers many devices including Logitech with OpenRGB running)
    if _set_color_openrgb(rgb, brightness):
        successful_backends.append("OpenRGB")
    else:
        failed_backends.append("OpenRGB SDK")
    
    # Try Logitech HID++ direct (works without OpenRGB)
    if _set_logitech_color_hidpp(rgb, brightness):
        successful_backends.append("Logitech")
    else:
        failed_backends.append("Logitech HID++")
    
    # Try Gigabyte HID for keyboard
    if _set_color_hidraw(rgb, brightness):
        successful_backends.append("Keyboard")
    else:
        failed_backends.append("Gigabyte HID")
    
    # Try OpenRGB CLI as fallback
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
    
    Note: Most RGB protocols are write-only. This function tries
    to read from OpenRGB which caches the last set color.
    
    Returns (r, g, b) or None if unable to read.
    """
    # Try OpenRGB SDK - it remembers the color state
    try:
        from openrgb import OpenRGBClient
        from openrgb.utils import DeviceType
        
        client = OpenRGBClient(name='Controlus-reader', timeout=1.0)
        for dev in client.devices:
            if dev.type in (DeviceType.KEYBOARD, DeviceType.MOUSE):
                if dev.colors and len(dev.colors) > 0:
                    c = dev.colors[0]
                    return (c.red, c.green, c.blue)
    except:
        pass
    
    # Try ratbagctl for Logitech (if installed)
    if shutil.which('ratbagctl'):
        try:
            result = subprocess.run(['ratbagctl', 'list'], capture_output=True, text=True, timeout=5)
            device_name = None
            for line in result.stdout.strip().split('\n'):
                if 'G Pro' in line or 'Logitech' in line:
                    device_name = line.split(':')[0].strip()
                    break
            
            if device_name:
                result = subprocess.run(
                    ['ratbagctl', device_name, 'profile', '0', 'led', '0', 'get'],
                    capture_output=True, text=True, timeout=5
                )
                if 'color:' in result.stdout:
                    color_hex = result.stdout.split('color:')[1].strip()[:6]
                    r = int(color_hex[0:2], 16)
                    g = int(color_hex[2:4], 16)
                    b = int(color_hex[4:6], 16)
                    return (r, g, b)
        except:
            pass
    
    return None


def detect_current_settings() -> Dict[str, Any]:
    """Detect current RGB settings from devices.
    
    Returns dict with 'color' (r,g,b tuple or None), 'brightness' (0-100 or None),
    and 'devices' list.
    """
    result = {
        'color': None,
        'brightness': None,
        'devices': get_available_devices()
    }
    
    color = get_current_color()
    if color:
        result['color'] = color
        # Estimate brightness from max component
        result['brightness'] = max(color) * 100 // 255 if max(color) > 0 else 100
    
    return result
