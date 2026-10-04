#!/usr/bin/env python3
"""Controlus helper - command-line front end used by the GNOME extension.

All device protocol code lives in controlus_backend.py (common/ in the repo),
installed next to this script and in /usr/local/lib/controlus. The commands
and their output stay what extension.js / prefs.js expect.
"""

import argparse
import json
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
for _p in (_here, "/usr/local/lib/controlus", os.path.join(_here, "..", "common")):
    if os.path.isfile(os.path.join(_p, "controlus_backend.py")):
        sys.path.insert(0, os.path.normpath(_p))
        break

import controlus_backend as backend  # noqa: E402


def _clamp(v, hi):
    return max(0, min(hi, int(v)))


def cmd_set_color(r, g, b, brightness):
    ok, msg = backend.set_color(r, g, b, brightness)
    if ok:
        backend.remember_color(r, g, b, brightness)
    return ok, msg


def cmd_set_keyboard(r, g, b, brightness):
    ok = backend._set_color_hidraw((r, g, b), brightness)
    return ok, "Keyboard + light bar set" if ok else "Keyboard not found"


def cmd_set_mouse(r, g, b, brightness):
    ok = backend._set_logitech_color_hidpp((r, g, b), brightness)
    return ok, "Mouse set" if ok else "Mouse not found"


def cmd_current_colors():
    (r, g, b), _ = backend.saved_color()
    # Both devices are write-only; the saved colour is what they were last set to.
    color = {"r": r, "g": g, "b": b}
    return True, json.dumps({"keyboard": color, "mouse": color})


def cmd_sync():
    rgb, brightness = backend.saved_color()
    return backend.set_color(*rgb, brightness)


def cmd_status():
    devices = backend.get_available_devices()
    kinds = {d["type"]: d for d in devices}
    parts = [
        "Keyboard: found" if "keyboard" in kinds else "Keyboard: not found",
        "Light bar: found" if "lightbar" in kinds else "Light bar: not found",
        f"Mouse: found ({kinds['mouse'].get('link', 'hid++')})" if "mouse" in kinds else "Mouse: not found",
    ]
    return True, "; ".join(parts)


def main():
    parser = argparse.ArgumentParser(description="Controlus RGB helper")
    sub = parser.add_subparsers(dest="command")
    for name, text in (("set-color", "keyboard, light bar AND mouse"),
                       ("set-keyboard", "keyboard + light bar only"),
                       ("set-mouse", "mouse only")):
        p = sub.add_parser(name, help=f"Set colour on {text}")
        p.add_argument("r", type=int)
        p.add_argument("g", type=int)
        p.add_argument("b", type=int)
        p.add_argument("brightness", type=int, nargs="?", default=100)
    sub.add_parser("off", help="Turn all lighting off")
    sub.add_parser("status", help="Check which devices are present")
    sub.add_parser("get-current-colors", help="Print the saved colour as JSON")
    sub.add_parser("sync-colors", help="Re-apply the saved colour to every device")
    args = parser.parse_args()

    if args.command in ("set-color", "set-keyboard", "set-mouse"):
        rgb = (_clamp(args.r, 255), _clamp(args.g, 255), _clamp(args.b, 255))
        brightness = _clamp(args.brightness, 100)
        fn = {"set-color": cmd_set_color, "set-keyboard": cmd_set_keyboard,
              "set-mouse": cmd_set_mouse}[args.command]
        ok, msg = fn(*rgb, brightness)
    elif args.command == "off":
        ok, msg = cmd_set_color(0, 0, 0, 0)
    elif args.command == "status":
        ok, msg = cmd_status()
    elif args.command == "get-current-colors":
        ok, msg = cmd_current_colors()
    elif args.command == "sync-colors":
        ok, msg = cmd_sync()
    else:
        parser.print_help()
        sys.exit(1)
    print(msg)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
