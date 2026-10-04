#!/usr/bin/env python3
"""Controlus background service for Linux.

Runs as a systemd *user* service (controlus.service):
  1. restores the saved colour at login - the keyboard, light bar and mouse
     all forget it on reboot;
  2. keeps the mouse colour: the G Pro drops back to its onboard profile on
     every link change (cable in/out, waking from sleep), so the shared
     LogitechReconnectWatcher re-applies it.

The colour comes from ~/.config/controlus/config.json, which the GUI and the
GNOME extension helper update on every apply, so a new colour is picked up
without restarting the service. Idle cost: one blocking read and a cheap
device enumeration per second.
"""

import signal
import threading
import time

from controlus import backend

RESTORE_ATTEMPTS = 10      # devices can still be coming up right after login
RESTORE_INTERVAL_S = 2


def restore() -> None:
    rgb, brightness = backend.saved_color()
    for _ in range(RESTORE_ATTEMPTS):
        ok, msg = backend.set_color(*rgb, brightness)
        if ok:
            print(f"restored: {msg}", flush=True)
            return
        time.sleep(RESTORE_INTERVAL_S)
    print("restore failed: no device answered", flush=True)


def main() -> None:
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    restore()
    watcher = backend.LogitechReconnectWatcher(backend.saved_color)
    watcher.start()
    stop.wait()
    watcher.stop()


if __name__ == "__main__":
    main()
