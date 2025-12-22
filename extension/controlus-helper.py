#!/usr/bin/env python3
"""
Controlus Helper - RGB Control Backend
This script controls keyboard (AORUS) and mouse (Logitech G Pro) RGB.
"""

import os
import sys
import glob
import fcntl
import json
import argparse
import subprocess
from pathlib import Path

# HID constants for keyboard
HIDIOCSFEATURE = 0xC0094806  # HID Set Feature ioctl
VENDOR_ID = 0x0414
PRODUCT_ID = 0x7A44

CONFIG_DIR = Path.home() / ".config" / "controlus"
CONFIG_FILE = CONFIG_DIR / "config.json"


def find_hidraw_device():
    """Find the correct hidraw device for the AORUS/Gigabyte keyboard"""
    for hidraw in glob.glob('/dev/hidraw*'):
        try:
            device_path = f'/sys/class/hidraw/{os.path.basename(hidraw)}/device'
            
            # Check modalias for VID:PID
            modalias_path = f'{device_path}/modalias'
            if os.path.exists(modalias_path):
                with open(modalias_path, 'r') as f:
                    modalias = f.read().lower()
                    vid = f'{VENDOR_ID:04x}'
                    pid = f'{PRODUCT_ID:04x}'
                    if vid in modalias and pid in modalias:
                        return hidraw
            
            # Alternative: check uevent
            uevent_path = f'{device_path}/uevent'
            if os.path.exists(uevent_path):
                with open(uevent_path, 'r') as f:
                    content = f.read().lower()
                    if f'{VENDOR_ID:04x}' in content and f'{PRODUCT_ID:04x}' in content:
                        return hidraw
        except (IOError, PermissionError):
            continue
    return None


def set_keyboard_color(r, g, b, brightness=100):
    """
    Set keyboard RGB color via HID Feature Report
    
    Args:
        r: Red component (0-255)
        g: Green component (0-255)
        b: Blue component (0-255)
        brightness: Brightness percentage (0-100)
    
    Returns:
        tuple: (success: bool, message: str)
    """
    # Validate inputs
    r = max(0, min(255, int(r)))
    g = max(0, min(255, int(g)))
    b = max(0, min(255, int(b)))
    brightness = max(0, min(100, int(brightness)))
    
    device = find_hidraw_device()
    if not device:
        return False, "Keyboard device not found. Is it connected?"
    
    try:
        # Apply brightness scaling
        factor = brightness / 100.0
        scaled_r = int(r * factor)
        scaled_g = int(g * factor)
        scaled_b = int(b * factor)
        
        with open(device, 'r+b', buffering=0) as fd:
            # Set all zones (0-9) and all zones marker (0xFF)
            zones = list(range(10)) + [0xFF]
            
            for zone in zones:
                # Build HID Feature Report packet
                # Format: [ReportID, Command, Zone, R, G, B, Brightness, Reserved, Checksum]
                cmd = 0x08  # SetZoneColors command
                packet = [0x00, cmd, zone, scaled_r, scaled_g, scaled_b, 100, 0x00]
                
                # Calculate checksum: 255 - sum of bytes 1-7
                checksum = (255 - sum(packet[1:8])) & 0xFF
                packet.append(checksum)
                
                # Create buffer and send
                buf = bytearray(9)
                buf[:len(packet)] = bytes(packet)
                
                fcntl.ioctl(fd.fileno(), HIDIOCSFEATURE, bytes(buf))
        
        return True, f"Set RGB({scaled_r}, {scaled_g}, {scaled_b}) at {brightness}%"
    
    except PermissionError:
        return False, "Permission denied. Run with pkexec or install udev rules."
    except OSError as e:
        return False, f"OS Error: {e}"
    except Exception as e:
        return False, f"Error: {e}"


def turn_off():
    """Turn off keyboard backlight"""
    return set_keyboard_color(0, 0, 0, 0)


# ============== MOUSE (Logitech G Pro) via HID++ Protocol ==============
# Direct communication without OpenRGB for instant response

import hid
from typing import Optional

# Logitech HID++ Constants
LOGITECH_VID = 0x046D
LIGHTSPEED_RECEIVER_PID = 0xC539
G_PRO_VIRTUAL_PID = 0x4079
G_PRO_WIRED_PID = 0xC088  # Wired mode USB

# HID++ Message Types
HIDPP_SHORT = 0x10  # 7 bytes
HIDPP_LONG = 0x11   # 20 bytes

# Feature Pages
FEATURE_RGB_EFFECTS = 0x8070


class LogitechMouse:
    """Direct HID++ control for Logitech mice"""
    
    def __init__(self):
        self.device = None
        self.device_index = 0x01  # First paired device on receiver
        self.rgb_feature_idx = None
    
    def open(self) -> bool:
        """Open HID device"""
        # Strategy: Try wired first, then receiver, then virtual
        # Wired mode (0xC088): device_index = 0xFF (direct)
        # Receiver (0xC539): device_index = 0x01 (first paired device)
        # Virtual (0x4079): device_index = 0xFF
        pids_to_try = [
            (G_PRO_WIRED_PID, 0xFF),      # Wired USB
            (LIGHTSPEED_RECEIVER_PID, 0x01),  # Wireless receiver
            (G_PRO_VIRTUAL_PID, 0xFF),    # Virtual device
        ]
        
        for pid, dev_idx in pids_to_try:
            devices = hid.enumerate(LOGITECH_VID, pid)
            for dev_info in devices:
                if dev_info.get('usage_page') == 0xFF00 and dev_info.get('usage') == 2:
                    try:
                        self.device = hid.device()
                        self.device.open_path(dev_info['path'])
                        self.device.set_nonblocking(False)
                        self.device_index = dev_idx
                        return True
                    except:
                        continue
        return False
    
    def close(self):
        if self.device:
            self.device.close()
            self.device = None
    
    def _send_long(self, feature_idx: int, func_id: int, data: bytes = b'') -> Optional[bytes]:
        """Send long HID++ message (20 bytes)"""
        if not self.device:
            return None
        
        packet = bytearray(20)
        packet[0] = HIDPP_LONG
        packet[1] = self.device_index
        packet[2] = feature_idx
        packet[3] = func_id
        for i, b in enumerate(data[:16]):
            packet[4 + i] = b
        
        try:
            self.device.write(bytes(packet))
            response = self.device.read(20, timeout_ms=300)
            return bytes(response) if response else None
        except:
            return None
    
    def _get_rgb_feature(self) -> Optional[int]:
        """Get RGB feature index"""
        if self.rgb_feature_idx is not None:
            return self.rgb_feature_idx
        
        # Query root feature for 0x8070
        data = bytes([(FEATURE_RGB_EFFECTS >> 8) & 0xFF, FEATURE_RGB_EFFECTS & 0xFF, 0])
        response = self._send_long(0x00, 0x00, data)
        
        if response and len(response) > 4 and response[4] != 0:
            self.rgb_feature_idx = response[4]
            return self.rgb_feature_idx
        return None
    
    def set_color(self, r: int, g: int, b: int, brightness: int = 100) -> bool:
        """Set static RGB color on all zones"""
        feature_idx = self._get_rgb_feature()
        if not feature_idx:
            return False
        
        # Enable software control
        # FP8071_CONTROL: function 0x10 for FP8070
        self.device.write(bytes([
            0x10, self.device_index, feature_idx, 0x10,
            0x01, 0x03, 0x05
        ]))
        self.device.read(20, timeout_ms=100)
        
        # Set color on both zones:
        # Zone 0 = Battery indicator / Primary
        # Zone 1 = Logo
        success = True
        for zone in [0, 1]:
            data = bytearray(16)
            data[0] = zone        # Zone
            data[1] = 0x01        # Mode: Static
            data[2] = r           # Red
            data[3] = g           # Green
            data[4] = b           # Blue
            data[8] = brightness  # Brightness
            data[12] = 0x01       # Persistence
            
            response = self._send_long(feature_idx, 0x30, bytes(data))
            if response is None:
                success = False
        
        return success
    
    def turn_off(self) -> bool:
        """Turn off RGB"""
        return self.set_color(0, 0, 0, 0)


def set_mouse_color(r: int, g: int, b: int, brightness: int = 100):
    """Set mouse RGB color via direct HID++"""
    mouse = LogitechMouse()
    
    if not mouse.open():
        return False, "Logitech mouse not found"
    
    try:
        if mouse.set_color(r, g, b, brightness):
            return True, f"Mouse set to RGB({r}, {g}, {b})"
        else:
            return False, "Failed to set mouse color"
    finally:
        mouse.close()


# ============== COMBINED CONTROL ==============

def set_all_rgb(r, g, b, brightness=100):
    """Set RGB for both keyboard and mouse"""
    results = []
    
    # Set keyboard
    kb_success, kb_msg = set_keyboard_color(r, g, b, brightness)
    results.append(f"Keyboard: {kb_msg}")
    
    # Set mouse (pass brightness directly)
    mouse_success, mouse_msg = set_mouse_color(r, g, b, brightness)
    results.append(f"Mouse: {mouse_msg}")
    
    overall_success = kb_success  # Keyboard is primary
    return overall_success, "; ".join(results)


# ============== CURRENT COLOR DETECTION ==============

def detect_current_keyboard_color():
    """
    Detect current keyboard color by reading from stored config
    If config doesn't exist, return default or try to detect from device
    
    Returns:
        tuple: (r, g, b) or None if cannot detect
    """
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                color = config.get('last_color', {})
                r = color.get('r', 0)
                g = color.get('g', 214)
                b = color.get('b', 255)
                return r, g, b
    except:
        pass
    
    # Default: cyan
    return 0, 214, 255


def detect_current_mouse_color():
    """
    Detect current mouse color
    
    Returns:
        tuple: (r, g, b) or None if cannot detect
    """
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                mouse_color = config.get('mouse_color', {})
                r = mouse_color.get('r', 255)
                g = mouse_color.get('g', 0)
                b = mouse_color.get('b', 0)
                return r, g, b
    except:
        pass
    
    # Default: red
    return 255, 0, 0


def sync_colors():
    """
    Sync colors: detect keyboard color and apply to mouse
    This handles the case where colors don't match on startup
    """
    kb_color = detect_current_keyboard_color()
    mouse_color = detect_current_mouse_color()
    
    if kb_color and mouse_color:
        r, g, b = kb_color
        
        # Update mouse to match keyboard
        mouse_success, mouse_msg = set_mouse_color(r, g, b, 100)
        
        return True, f"Synced mouse to keyboard color RGB({r}, {g}, {b}): {mouse_msg}"
    
    return False, "Could not sync colors"


def get_current_colors():
    """Get current colors for both keyboard and mouse"""
    kb_color = detect_current_keyboard_color()
    mouse_color = detect_current_mouse_color()
    
    result = {
        "keyboard": {"r": kb_color[0], "g": kb_color[1], "b": kb_color[2]},
        "mouse": {"r": mouse_color[0], "g": mouse_color[1], "b": mouse_color[2]}
    }
    
    return True, json.dumps(result)


def get_status():
    """Check if devices are available"""
    status = []
    
    # Check keyboard
    device = find_hidraw_device()
    if device:
        status.append(f"Keyboard: found at {device}")
    else:
        status.append("Keyboard: not found")
    
    # Check mouse via HID
    mouse = LogitechMouse()
    if mouse.open():
        status.append("Mouse: found (G Pro Wireless)")
        mouse.close()
    else:
        status.append("Mouse: not found")
    
    return True, "; ".join(status)


def load_config():
    """Load configuration from file"""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {}


def save_config(config):
    """Save configuration to file"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description='Controlus RGB Helper - Control AORUS keyboard & G Pro mouse RGB'
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # set-color command (sets BOTH keyboard and mouse)
    set_parser = subparsers.add_parser('set-color', help='Set RGB color for keyboard AND mouse')
    set_parser.add_argument('r', type=int, help='Red (0-255)')
    set_parser.add_argument('g', type=int, help='Green (0-255)')
    set_parser.add_argument('b', type=int, help='Blue (0-255)')
    set_parser.add_argument('brightness', type=int, nargs='?', default=100,
                           help='Brightness (0-100, default: 100)')
    
    # keyboard-only command
    kb_parser = subparsers.add_parser('set-keyboard', help='Set keyboard color only')
    kb_parser.add_argument('r', type=int, help='Red (0-255)')
    kb_parser.add_argument('g', type=int, help='Green (0-255)')
    kb_parser.add_argument('b', type=int, help='Blue (0-255)')
    kb_parser.add_argument('brightness', type=int, nargs='?', default=100,
                           help='Brightness (0-100, default: 100)')
    
    # mouse-only command
    mouse_parser = subparsers.add_parser('set-mouse', help='Set mouse color only')
    mouse_parser.add_argument('r', type=int, help='Red (0-255)')
    mouse_parser.add_argument('g', type=int, help='Green (0-255)')
    mouse_parser.add_argument('b', type=int, help='Blue (0-255)')
    
    # off command
    subparsers.add_parser('off', help='Turn off keyboard backlight')
    
    # status command
    subparsers.add_parser('status', help='Check device status')
    
    # get-current-colors command
    subparsers.add_parser('get-current-colors', help='Get current colors for both devices')
    
    # sync-colors command
    subparsers.add_parser('sync-colors', help='Sync mouse color to keyboard color')
    
    args = parser.parse_args()
    
    if args.command == 'set-color':
        # Set BOTH keyboard and mouse together
        success, msg = set_all_rgb(args.r, args.g, args.b, args.brightness)
        print(msg)
        sys.exit(0 if success else 1)
    
    elif args.command == 'set-keyboard':
        success, msg = set_keyboard_color(args.r, args.g, args.b, args.brightness)
        print(msg)
        sys.exit(0 if success else 1)
    
    elif args.command == 'set-mouse':
        success, msg = set_mouse_color(args.r, args.g, args.b)
        print(msg)
        sys.exit(0 if success else 1)
    
    elif args.command == 'off':
        success, msg = turn_off()
        print(msg)
        sys.exit(0 if success else 1)
    
    elif args.command == 'status':
        success, msg = get_status()
        print(msg)
        sys.exit(0 if success else 1)
    
    elif args.command == 'get-current-colors':
        success, msg = get_current_colors()
        print(msg)
        sys.exit(0 if success else 1)
    
    elif args.command == 'sync-colors':
        success, msg = sync_colors()
        print(msg)
        sys.exit(0 if success else 1)
    
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
