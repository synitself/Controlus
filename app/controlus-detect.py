#!/usr/bin/env python3
"""Controlus Color Detection Tool

Attempts to detect current RGB color from connected devices.
Useful for syncing config with actual device state.
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from controlus.backend import (
        get_available_devices,
        get_current_color,
        detect_current_settings
    )
except ImportError:
    print("Error: Could not import controlus.backend")
    print("Make sure you're running from the controlus directory")
    sys.exit(1)


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Detect RGB devices and colors')
    parser.add_argument('--json', action='store_true', help='Output in JSON format')
    parser.add_argument('--devices', action='store_true', help='List only devices')
    parser.add_argument('--color', action='store_true', help='Show only detected color')
    args = parser.parse_args()
    
    settings = detect_current_settings()
    
    if args.json:
        print(json.dumps({
            'color': settings['color'],
            'brightness': settings['brightness'],
            'devices': settings['devices']
        }, indent=2))
        return
    
    if args.devices:
        for dev in settings['devices']:
            print(f"{dev['name']} ({dev['type']}) via {dev['backend']}")
        return
    
    if args.color:
        if settings['color']:
            r, g, b = settings['color']
            print(f"RGB({r}, {g}, {b})")
        else:
            print("No color detected")
        return
    
    # Default: show all info
    print("=== Controlus Device Detection ===\n")
    
    print("Detected Devices:")
    if settings['devices']:
        for dev in settings['devices']:
            print(f"  • {dev['name']}")
            print(f"    Type: {dev['type']}, Backend: {dev['backend']}")
    else:
        print("  No devices found")
    
    print()
    
    print("Current Color:")
    if settings['color']:
        r, g, b = settings['color']
        print(f"  RGB({r}, {g}, {b})")
        # Show hex too
        print(f"  Hex: #{r:02X}{g:02X}{b:02X}")
        # Estimate brightness
        if settings['brightness'] is not None:
            print(f"  Estimated brightness: {settings['brightness']}%")
    else:
        print("  Unable to detect (most devices are write-only)")
        print("  Tip: Use OpenRGB or ratbagctl for devices that support reading")
    
    print()
    print("Note: If you set a color with Controlus, it will be saved in config.")


if __name__ == "__main__":
    main()
