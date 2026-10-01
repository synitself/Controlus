#!/usr/bin/env python3
"""Controlus for Windows - Keyboard & Mouse RGB Control (PySide6 GUI).

Windows port of the original GTK4/libadwaita app.

Supports:
  - Gigabyte/AORUS keyboards
  - Logitech G Pro Wireless mouse
  - Any OpenRGB-supported device

Colour changes apply live: every edit restarts a short debounce timer, and the
actual HID writes run on a worker thread so the window never stalls on a slow
device.

The app lives in the system tray: closing the window only hides it, so the
reconnect watcher keeps restoring the mouse colour. While hidden it stops
polling for devices and trims its working set.
"""

from __future__ import annotations

import colorsys
import ctypes
import getpass
import json
import math
import os
import re
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QConicalGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QSlider,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

try:
    from controlus.backend import set_color as backend_set_color
    from controlus.backend import get_available_devices, LogitechReconnectWatcher
    from controlus.logo import mark_icon, paint_mark
except Exception:  # pragma: no cover - import fallback for frozen/standalone runs
    try:
        from .backend import set_color as backend_set_color  # type: ignore
        from .backend import get_available_devices, LogitechReconnectWatcher  # type: ignore
        from .logo import mark_icon, paint_mark  # type: ignore
    except Exception:
        backend_set_color = None  # type: ignore
        get_available_devices = None  # type: ignore
        LogitechReconnectWatcher = None  # type: ignore
        from controlus.logo import mark_icon, paint_mark  # type: ignore

# Config lives in %APPDATA%\Controlus on Windows.
CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "Controlus"
CONFIG_FILE = CONFIG_DIR / "config.json"

RING_SIZE = 300
APPLY_DEBOUNCE_MS = 250
DEVICE_POLL_MS = 4000
INSTANCE_KEY = f"Controlus-{getpass.getuser()}"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

PRESETS = [
    (255, 0, 0), (255, 96, 0), (255, 200, 0), (0, 255, 64), (0, 255, 255),
    (0, 96, 255), (128, 0, 255), (255, 0, 160), (255, 255, 255),
]

# Palette
BG = "#0e1014"
CARD = "rgba(24, 27, 35, 235)"
CARD_BORDER = "#242833"
TEXT = "#e8eaf0"
MUTED = "#8a90a0"
FAINT = "#4a5060"
OK = "#5ee39a"
ERR = "#ff6b81"


def _hex(rgb) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


# ---------------------------------------------------------------------------
# Windows integration: autostart, rounded corners, working-set trim
# ---------------------------------------------------------------------------

def _launch_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --tray'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{pythonw}" "{main_py}" --tray'


def set_autostart(enabled: bool) -> None:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, "Controlus", 0, winreg.REG_SZ, _launch_command())
            else:
                try:
                    winreg.DeleteValue(key, "Controlus")
                except FileNotFoundError:
                    pass
    except Exception:
        pass


def round_window_corners(widget: QWidget) -> None:
    """Ask DWM for Windows 11 rounded corners and a subtle border on a frameless window."""
    try:
        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
        border = ctypes.c_uint(0x00332824)  # COLORREF 0x00BBGGRR of CARD_BORDER
        dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), 4)
    except Exception:
        pass


def trim_working_set() -> None:
    """Hand idle pages back to Windows once the window is hidden."""
    try:
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = wintypes.HANDLE  # pseudo-handle -1, must stay 64-bit
        k32.SetProcessWorkingSetSize.argtypes = (wintypes.HANDLE, ctypes.c_size_t, ctypes.c_size_t)
        k32.SetProcessWorkingSetSize(k32.GetCurrentProcess(), ctypes.c_size_t(-1), ctypes.c_size_t(-1))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------

class Applier(QObject):
    """Runs backend_set_color off the GUI thread, coalescing bursts of requests.

    Only the newest request matters: while a write is in flight, later requests
    overwrite each other and the worker picks up the last one when it is done.
    """

    finished = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._pending = None
        self._busy = False

    def request(self, rgb, brightness):
        with self._lock:
            self._pending = (rgb, brightness)
            if self._busy:
                return
            self._busy = True
        threading.Thread(target=self._run, name="apply-worker", daemon=True).start()

    def _run(self):
        while True:
            with self._lock:
                job, self._pending = self._pending, None
                if job is None:
                    self._busy = False
                    return
            (r, g, b), brightness = job
            try:
                ok, msg = backend_set_color(r, g, b, brightness)
            except Exception as e:  # pragma: no cover - hardware errors
                ok, msg = False, str(e)
            self.finished.emit(ok, msg)


class DeviceProbe(QObject):
    """Polls get_available_devices() on a worker thread."""

    found = Signal(list)

    def __init__(self):
        super().__init__()
        self._running = False

    def poll(self):
        if self._running or get_available_devices is None:
            return
        self._running = True
        threading.Thread(target=self._run, name="device-probe", daemon=True).start()

    def _run(self):
        try:
            devices = get_available_devices()
        except Exception:
            devices = []
        self._running = False
        self.found.emit(devices)


# ---------------------------------------------------------------------------
# Colour ring with the power button in the middle
# ---------------------------------------------------------------------------

class ColorRing(QWidget):
    """Solid hue/saturation wheel.

    Angle = hue (counter-clockwise from 3 o'clock), distance from the centre =
    saturation (white in the middle). Dimmed while the lights are off.
    """

    color_changed = Signal(tuple)  # (r, g, b) at full value

    OUTER = 138

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(RING_SIZE, RING_SIZE)
        self.setMouseTracking(True)
        self.hue = 0.5
        self.saturation = 1.0
        self.value = 1.0
        self.power = True
        self._drag = False

    def hs_rgb(self) -> tuple[int, int, int]:
        r, g, b = colorsys.hsv_to_rgb(self.hue, self.saturation, 1.0)
        return (round(r * 255), round(g * 255), round(b * 255))

    def output_color(self) -> QColor:
        r, g, b = colorsys.hsv_to_rgb(self.hue, self.saturation, self.value)
        return QColor.fromRgbF(r, g, b)

    def set_rgb(self, r: int, g: int, b: int):
        h, s, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s > 0:  # grey carries no hue; keep the old one instead of snapping to red
            self.hue = h
        self.saturation = s
        self.update()

    def set_value(self, value: float):
        self.value = max(0.0, min(1.0, value))
        self.update()

    def set_power(self, on: bool):
        self.power = on
        self.update()

    def _center(self) -> QPointF:
        return QPointF(self.width() / 2, self.height() / 2)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = self._center()
        disc = QPainterPath()
        disc.addEllipse(c, self.OUTER, self.OUTER)

        hue = QConicalGradient(c, 0)
        for i in range(13):
            r, g, b = colorsys.hsv_to_rgb((i % 12) / 12, 1, 1)
            hue.setColorAt(i / 12, QColor.fromRgbF(r, g, b))
        p.fillPath(disc, QBrush(hue))
        sat = QRadialGradient(c, self.OUTER)
        sat.setColorAt(0.0, QColor(255, 255, 255, 255))
        sat.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillPath(disc, QBrush(sat))
        dim = 1 - self.value if self.power else 0.7
        if dim > 0:
            p.fillPath(disc, QColor(0, 0, 0, round(255 * dim * 0.8)))
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(disc)

        angle = self.hue * 2 * math.pi
        dist = self.saturation * self.OUTER
        t = QPointF(c.x() + dist * math.cos(angle), c.y() - dist * math.sin(angle))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 100))
        p.drawEllipse(QPointF(t.x(), t.y() + 1.5), 13, 13)
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(t, 12, 12)
        p.setBrush(QColor(*self.hs_rgb()))
        p.drawEllipse(t, 8.5, 8.5)

    def _polar(self, pos):
        c = self._center()
        dx, dy = pos.x() - c.x(), c.y() - pos.y()
        return math.hypot(dx, dy), math.atan2(dy, dx)

    def _pick(self, pos):
        dist, angle = self._polar(pos)
        self.hue = (angle / (2 * math.pi)) % 1.0
        self.saturation = min(1.0, dist / self.OUTER)
        self.update()
        self.color_changed.emit(self.hs_rgb())

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._polar(event.position())[0] <= self.OUTER + 14:
            self._drag = True
            self._pick(event.position())

    def mouseMoveEvent(self, event):
        inside = self._polar(event.position())[0] <= self.OUTER + 14
        self.setCursor(Qt.CrossCursor if inside or self._drag else Qt.ArrowCursor)
        if self._drag and event.buttons() & Qt.LeftButton:
            self._pick(event.position())

    def mouseReleaseEvent(self, event):
        self._drag = False


# ---------------------------------------------------------------------------
# Small widgets
# ---------------------------------------------------------------------------

class IconButton(QAbstractButton):
    """Flat round title-bar button that draws its own glyph (only 'close' for now)."""

    def __init__(self, glyph, tooltip, parent=None):
        super().__init__(parent)
        self.glyph = glyph
        self.setFixedSize(36, 36)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tooltip)

    def enterEvent(self, event):
        self.update()

    def leaveEvent(self, event):
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        if self.underMouse():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 18))
            p.drawEllipse(c, 17, 17)
        col = QColor(TEXT)
        if self.glyph == "close":
            p.setPen(QPen(col, 2.2, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(c.x() - 6.5, c.y() - 6.5), QPointF(c.x() + 6.5, c.y() + 6.5))
            p.drawLine(QPointF(c.x() + 6.5, c.y() - 6.5), QPointF(c.x() - 6.5, c.y() + 6.5))


class TitleBar(QWidget):
    """Sota-style header: close on the left, the mark in the middle.

    Dragging anywhere on it moves the frameless window.
    """

    close_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(52)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 0)
        self.close_btn = IconButton("close", "Hide to tray")
        self.close_btn.clicked.connect(self.close_clicked)
        mark = QLabel()
        mark.setPixmap(_mark_pixmap(24, self.devicePixelRatioF()))
        word = QLabel("Controlus")
        word.setObjectName("wordmark")
        row.addWidget(self.close_btn)
        row.addStretch()
        row.addWidget(mark)
        row.addSpacing(6)
        row.addWidget(word)
        row.addStretch()
        row.addSpacing(self.close_btn.width())

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemMove()


def _mark_pixmap(size, dpr):
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    paint_mark(p, QRectF(0, 0, size, size))
    p.end()
    return pm


class ColorDot(QAbstractButton):
    """Round colour chip. Optionally removable via a right-click menu."""

    remove_requested = Signal()

    def __init__(self, rgb, removable=False, parent=None):
        super().__init__(parent)
        self.rgb = tuple(rgb)
        self.selected = False
        self.setFixedSize(34, 34)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(_hex(self.rgb) + ("\nRight-click to remove" if removable else ""))
        if removable:
            self.setContextMenuPolicy(Qt.CustomContextMenu)
            self.customContextMenuRequested.connect(self._menu)

    def _menu(self, pos):
        menu = QMenu(self)
        menu.addAction("Remove from favorites", self.remove_requested.emit)
        menu.exec(self.mapToGlobal(pos))

    def set_selected(self, selected):
        if selected != self.selected:
            self.selected = selected
            self.update()

    def enterEvent(self, event):
        self.update()

    def leaveEvent(self, event):
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        if self.selected:
            p.setPen(QPen(QColor(255, 255, 255), 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, 15.5, 15.5)
        r = 12.5 if (self.underMouse() or self.selected) else 11.5
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(QColor(*self.rgb))
        p.drawEllipse(c, r, r)


class AddDot(QAbstractButton):
    """Dashed '+' chip that saves the current colour as a favourite."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(34, 34)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Save current color")

    def enterEvent(self, event):
        self.update()

    def leaveEvent(self, event):
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        col = QColor(TEXT if self.underMouse() else MUTED)
        p.setPen(QPen(col, 1.4, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, 12, 12)
        p.setPen(QPen(col, 1.8, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(c.x() - 5, c.y()), QPointF(c.x() + 5, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - 5), QPointF(c.x(), c.y() + 5))


class DeviceChip(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chip")

    def set_state(self, name, detail, online):
        dot = OK if online else FAINT
        extra = f"<span style='color:{MUTED}'> · {detail}</span>" if detail else ""
        color = TEXT if online else MUTED
        self.setText(f"<span style='color:{dot}'>●</span>&nbsp; "
                     f"<span style='color:{color}'>{name}</span>{extra}")


def _section(text) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("section")
    return label


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class ControlusWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Controlus")
        self.setWindowIcon(mark_icon(tile=True))
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setFixedWidth(392)
        self._quitting = False
        self._placed = False
        self._pattern = None

        self.config = self.load_config()
        self.config.setdefault("favorites", [])
        self.config.setdefault("power", True)
        last = self.config.get("last_color") or {}
        self.base_rgb = (last.get("r", 0), last.get("g", 255), last.get("b", 255))
        if self.base_rgb == (0, 0, 0):  # old "Off" stored black as the colour
            self.base_rgb = (0, 255, 255)
            self.config["brightness"] = 100
        if "autostart" not in self.config:  # on by default; the menu can turn it off
            self.config["autostart"] = True
        set_autostart(self.config["autostart"])

        self.applier = Applier()
        self.applier.finished.connect(self.on_applied)
        self.apply_timer = QTimer(self, singleShot=True, interval=APPLY_DEBOUNCE_MS)
        self.apply_timer.timeout.connect(self.apply_now)

        # Tray menu.
        self.menu = QMenu(self)
        self.act_open = self.menu.addAction("Open Controlus", self.show_window)
        self.menu.addSeparator()
        self.act_power = QAction("Lighting", self, checkable=True)
        self.act_power.setChecked(bool(self.config["power"]))
        self.act_power.toggled.connect(self.set_power)
        self.menu.addAction(self.act_power)
        self.act_autostart = QAction("Start with Windows", self, checkable=True)
        self.act_autostart.setChecked(bool(self.config["autostart"]))
        self.act_autostart.toggled.connect(self.on_autostart)
        self.menu.addAction(self.act_autostart)
        self.menu.addSeparator()
        self.menu.addAction("Quit Controlus", self.quit)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.titlebar = TitleBar()
        self.titlebar.close_clicked.connect(self.hide_to_tray)
        root.addWidget(self.titlebar)

        body = QVBoxLayout()
        body.setContentsMargins(22, 6, 22, 16)
        body.setSpacing(14)
        root.addLayout(body)

        chips = QHBoxLayout()
        chips.setSpacing(8)
        chips.addStretch()
        self.kbd_chip = DeviceChip()
        self.mouse_chip = DeviceChip()
        self.kbd_chip.set_state("Keyboard", "", False)
        self.mouse_chip.set_state("G Pro", "", False)
        chips.addWidget(self.kbd_chip)
        chips.addWidget(self.mouse_chip)
        chips.addStretch()
        body.addLayout(chips)

        self.ring = ColorRing()
        self.ring.color_changed.connect(self.on_ring_changed)
        ring_row = QHBoxLayout()
        ring_row.addStretch()
        ring_row.addWidget(self.ring)
        ring_row.addStretch()
        body.addLayout(ring_row)

        self.state_label = QLabel()
        self.state_label.setObjectName("state")
        self.state_label.setAlignment(Qt.AlignCenter)
        body.addWidget(self.state_label)

        # Colour card ----------------------------------------------------------
        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(18, 14, 18, 14)
        cl.setSpacing(10)
        top = QHBoxLayout()
        info = QVBoxLayout()
        info.setSpacing(0)
        cap = QLabel("COLOR")
        cap.setObjectName("caption")
        self.hex_edit = QLineEdit()
        self.hex_edit.setObjectName("hex")
        self.hex_edit.setMaxLength(7)
        self.hex_edit.setToolTip("Type a hex color and press Enter")
        self.hex_edit.editingFinished.connect(self.on_hex_edited)
        info.addWidget(cap)
        info.addWidget(self.hex_edit)
        top.addLayout(info, 1)
        self.rgb_label = QLabel()
        self.rgb_label.setObjectName("mono")
        self.rgb_label.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        top.addWidget(self.rgb_label)
        cl.addLayout(top)

        bright = QHBoxLayout()
        bright.setSpacing(12)
        sun = QLabel("☀")
        sun.setObjectName("icon")
        bright.addWidget(sun)
        self.brightness = QSlider(Qt.Horizontal)
        self.brightness.setRange(0, 100)
        self.brightness.setFixedHeight(24)  # the 18 px handle got clipped at the default height
        self.brightness.setValue(int(self.config.get("brightness", 100)))
        self.brightness.valueChanged.connect(self.on_brightness_changed)
        bright.addWidget(self.brightness, 1)
        self.bright_label = QLabel()
        self.bright_label.setObjectName("mono")
        self.bright_label.setFixedWidth(40)
        self.bright_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bright.addWidget(self.bright_label)
        cl.addLayout(bright)
        body.addWidget(card)

        # Presets + favourites -------------------------------------------------
        body.addWidget(_section("Presets"))
        self.preset_dots = []
        preset_row = QHBoxLayout()
        preset_row.setSpacing(4)
        for rgb in PRESETS:
            dot = ColorDot(rgb)
            dot.clicked.connect(lambda checked=False, c=rgb: self.pick(c))
            self.preset_dots.append(dot)
            preset_row.addWidget(dot)
        preset_row.addStretch()
        body.addLayout(preset_row)

        body.addWidget(_section("Favorites"))
        self.fav_row = QHBoxLayout()
        self.fav_row.setSpacing(4)
        body.addLayout(self.fav_row)
        self.fav_dots = []

        self.status = QLabel("")
        self.status.setObjectName("status")
        self.status.setAlignment(Qt.AlignCenter)
        body.addWidget(self.status)

        # Tray -----------------------------------------------------------------
        self.tray = QSystemTrayIcon(self)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.on_tray_activated)
        self._tray_key = None

        # Initial state ---------------------------------------------------------
        self.devices = []
        self.ring.set_rgb(*self.base_rgb)
        self.ring.set_value(self.brightness.value() / 100)
        self.ring.set_power(bool(self.config["power"]))
        self.rebuild_favorites()
        self.refresh()
        self.update_tray()
        self.tray.show()

        self.probe = DeviceProbe()
        self.probe.found.connect(self.on_devices)
        self.probe.poll()
        self.poll_timer = QTimer(self, interval=DEVICE_POLL_MS)
        self.poll_timer.timeout.connect(self.probe.poll)

        # Devices forget the colour on reboot; restore it on launch.
        QTimer.singleShot(300, self.apply_now)

        # Re-apply the last colour when the mouse switches cable/radio or wakes up.
        self.watcher = None
        if LogitechReconnectWatcher is not None:
            self.watcher = LogitechReconnectWatcher(self._applied_color)
            self.watcher.start()

    # -- window / tray ------------------------------------------------------------
    def show_window(self):
        if not self._placed:
            # A frameless window gets no help from Windows placing it; centre it
            # in the work area so the bottom never ends up under the taskbar.
            self.adjustSize()
            screen = QApplication.primaryScreen().availableGeometry()
            x = screen.x() + (screen.width() - self.width()) // 2
            y = screen.y() + max(0, (screen.height() - self.height()) // 2)
            self.move(x, y)
            self._placed = True
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def showEvent(self, event):
        super().showEvent(event)
        round_window_corners(self)
        self.probe.poll()
        self.poll_timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.poll_timer.stop()
        QTimer.singleShot(1500, self._trim_if_hidden)

    def _trim_if_hidden(self):
        if not self.isVisible():
            trim_working_set()

    def hide_to_tray(self):
        self.hide()
        if not self.config.get("tray_hint_shown"):
            self.tray.showMessage("Controlus", "Still running in the tray — lights stay in sync.",
                                  mark_icon(tile=True), 4000)
            self.config["tray_hint_shown"] = True
            self.save_config()

    def closeEvent(self, event):
        if self._quitting:
            super().closeEvent(event)
            return
        event.ignore()
        self.hide_to_tray()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            if self.isVisible() and self.isActiveWindow():
                self.hide()
            else:
                self.show_window()

    def quit(self):
        self._quitting = True
        if self.watcher is not None:
            self.watcher.stop()
        self.tray.hide()
        QApplication.quit()

    def update_tray(self):
        on = bool(self.config["power"])
        key = (self.output_rgb(), on)
        if key == self._tray_key:
            return
        self._tray_key = key
        tint = QColor(*self.base_rgb) if on else QColor(FAINT)
        self.tray.setIcon(mark_icon(tint))
        state = _hex(self.base_rgb) + f" · {self.brightness.value()}%" if on else "off"
        self.tray.setToolTip(f"Controlus — {state}")

    def on_autostart(self, enabled):
        self.config["autostart"] = enabled
        set_autostart(enabled)
        self.save_config()

    # -- background ---------------------------------------------------------------
    def resizeEvent(self, event):
        self._pattern = None
        super().resizeEvent(event)

    def _hex_pattern(self) -> QPixmap:
        """Faint honeycomb that fades out away from the ring (cached per size)."""
        dpr = self.devicePixelRatioF()
        pm = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255, 13), 1.2))
        p.setBrush(QColor(255, 255, 255, 5))
        size = 30.0
        w, h = math.sqrt(3) * size, 1.5 * size
        rows = int(self.height() / h) + 2
        cols = int(self.width() / w) + 2
        for row in range(rows):
            for col in range(cols):
                cx = col * w + (w / 2 if row % 2 else 0)
                cy = row * h
                hexagon = QPolygonF([
                    QPointF(cx + (size - 3) * math.cos(math.radians(60 * k + 30)),
                            cy + (size - 3) * math.sin(math.radians(60 * k + 30)))
                    for k in range(6)])
                p.drawPolygon(hexagon)
        center = self._ring_center()
        mask = QRadialGradient(center, 250)
        mask.setColorAt(0.0, QColor(0, 0, 0, 255))
        mask.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        p.fillRect(QRectF(0, 0, self.width(), self.height()), QBrush(mask))
        p.end()
        return pm

    def _ring_center(self) -> QPointF:
        return QPointF(self.ring.mapTo(self, self.ring.rect().center()))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(BG))
        if self._pattern is None:
            self._pattern = self._hex_pattern()
        p.drawPixmap(0, 0, self._pattern)
        if self.config.get("power", True):
            c = self._ring_center()
            glow = QRadialGradient(c, 210)
            col = QColor(self.ring.output_color())
            col.setAlpha(70)
            glow.setColorAt(0.0, col)
            col.setAlpha(28)
            glow.setColorAt(0.55, col)
            col.setAlpha(0)
            glow.setColorAt(1.0, col)
            p.fillRect(self.rect(), QBrush(glow))

    # -- derived state ------------------------------------------------------------
    def output_rgb(self):
        """Colour as the devices show it: hue/saturation scaled by brightness."""
        v = self.brightness.value() / 100
        return tuple(round(c * v) for c in self.base_rgb)

    def _applied_color(self):
        """What the watcher should restore (called from the watcher thread)."""
        if not self.config.get("power", True):
            return (0, 0, 0), 0
        last = self.config.get("last_color") or {"r": 0, "g": 255, "b": 255}
        return (last["r"], last["g"], last["b"]), int(self.config.get("brightness", 100))

    def refresh(self):
        on = bool(self.config["power"])
        if not self.hex_edit.hasFocus():
            self.hex_edit.setText(_hex(self.base_rgb))
        self.rgb_label.setText("{}  {}  {}".format(*self.base_rgb))
        self.bright_label.setText(f"{self.brightness.value()}%")
        for dot in self.preset_dots + self.fav_dots:
            dot.set_selected(dot.rgb == self.base_rgb)
        accent = _hex(self.base_rgb)
        self.brightness.setStyleSheet(
            "QSlider::groove:horizontal { height: 6px; border-radius: 3px;"
            f" background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #000000, stop:1 {accent}); }}"
            "QSlider::sub-page:horizontal { background: transparent; }"
            "QSlider::handle:horizontal { background: #ffffff; width: 18px; height: 18px;"
            " margin: -6px 0; border-radius: 9px; }")
        n = len(self.devices)
        devices = f"{n} device{'s' if n != 1 else ''}" if n else "no devices"
        self.state_label.setText(("Lighting on" if on else "Lighting off") + f"  ·  {devices}")
        self.update()

    # -- input --------------------------------------------------------------------
    def on_ring_changed(self, rgb):
        self.base_rgb = rgb
        self._changed()

    def on_brightness_changed(self, value):
        self.ring.set_value(value / 100)
        self._changed()

    def on_hex_edited(self):
        text = self.hex_edit.text().strip().lstrip("#")
        if re.fullmatch(r"[0-9a-fA-F]{6}", text):
            rgb = tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))
            if rgb != self.base_rgb:
                self.pick(rgb)
        else:
            self.hex_edit.setText(_hex(self.base_rgb))

    def pick(self, rgb):
        self.base_rgb = tuple(rgb)
        self.ring.set_rgb(*rgb)
        self._changed(immediate=True)

    def set_power(self, on):
        if on == self.config["power"]:
            return
        self.config["power"] = on
        self.save_config()
        self.ring.set_power(on)
        self.act_power.blockSignals(True)
        self.act_power.setChecked(on)
        self.act_power.blockSignals(False)
        self.refresh()
        self.update_tray()
        self.apply_timer.stop()
        self.apply_now()

    def _changed(self, immediate=False):
        if not self.config["power"]:
            # Picking a colour while off means "turn it on with this".
            self.set_power(True)
        self.refresh()
        if immediate:
            self.apply_timer.stop()
            self.apply_now()
        else:
            self.apply_timer.start()

    # -- applying -----------------------------------------------------------------
    def apply_now(self):
        if backend_set_color is None:
            self.show_status("Backend unavailable — install hidapi", error=True)
            return
        if self.config["power"]:
            self.applier.request(self.base_rgb, self.brightness.value())
        else:
            self.applier.request((0, 0, 0), 0)

    def on_applied(self, ok, msg):
        if ok:
            if self.config["power"]:
                r, g, b = self.base_rgb
                self.config["last_color"] = {"r": r, "g": g, "b": b}
                self.config["brightness"] = self.brightness.value()
            self.save_config()
            self.update_tray()
            self.show_status("")
        else:
            self.show_status(msg, error=True)

    def show_status(self, message, error=False):
        self.status.setText(message)
        self.status.setVisible(bool(message))
        self.status.setProperty("error", "true" if error else "false")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def on_devices(self, devices):
        self.devices = devices
        kbd = next((d for d in devices if d.get("type") == "keyboard"), None)
        mouse = next((d for d in devices if d.get("type") == "mouse"), None)
        self.kbd_chip.set_state("Keyboard", "", kbd is not None)
        link = {"cable": "cable", "receiver": "wireless"}.get((mouse or {}).get("link"), "")
        self.mouse_chip.set_state("G Pro", link, mouse is not None)
        self.refresh()

    # -- favorites ----------------------------------------------------------------
    def add_favorite(self):
        favs = self.config["favorites"]
        r, g, b = self.base_rgb
        if any((f["r"], f["g"], f["b"]) == (r, g, b) for f in favs):
            return
        favs.append({"r": r, "g": g, "b": b})
        self.save_config()
        self.rebuild_favorites()

    def remove_favorite(self, index):
        favs = self.config["favorites"]
        if 0 <= index < len(favs):
            favs.pop(index)
            self.save_config()
            self.rebuild_favorites()

    def rebuild_favorites(self):
        while self.fav_row.count():
            item = self.fav_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.fav_dots = []
        favs = self.config["favorites"]
        first = max(0, len(favs) - 9)  # nine chips fit next to the add button
        for idx in range(first, len(favs)):
            rgb = (favs[idx]["r"], favs[idx]["g"], favs[idx]["b"])
            dot = ColorDot(rgb, removable=True)
            dot.clicked.connect(lambda checked=False, c=rgb: self.pick(c))
            dot.remove_requested.connect(lambda k=idx: self.remove_favorite(k))
            self.fav_dots.append(dot)
            self.fav_row.addWidget(dot)
        add = AddDot()
        add.clicked.connect(self.add_favorite)
        self.fav_row.addWidget(add)
        if not self.fav_dots:
            hint = QLabel("Save the current color")
            hint.setObjectName("hint")
            self.fav_row.addWidget(hint)
        self.fav_row.addStretch()
        for dot in self.fav_dots:
            dot.set_selected(dot.rgb == self.base_rgb)

    # -- config -------------------------------------------------------------------
    def load_config(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"favorites": [], "last_color": None, "brightness": 100}

    def save_config(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2)


STYLE = f"""
QWidget {{
    background: transparent;
    color: {TEXT};
    font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif;
    font-size: 13px;
}}
QLabel#wordmark {{ font-family: "Segoe UI Variable Display", "Segoe UI", sans-serif;
                  font-size: 18px; font-weight: 700; }}
QLabel#state {{ font-size: 13px; color: {MUTED}; }}
QLabel#section {{ font-size: 11px; font-weight: 600; color: {MUTED};
                 letter-spacing: 1.5px; margin-top: 2px; }}
QLabel#caption {{ font-size: 10px; font-weight: 600; color: {MUTED}; letter-spacing: 1.5px; }}
QLabel#chip {{ background: {CARD}; border: 1px solid {CARD_BORDER}; border-radius: 12px;
              padding: 4px 11px; font-size: 12px; }}
QLabel#mono {{ font-family: "Cascadia Mono", "Consolas", monospace; font-size: 12px; color: {MUTED}; }}
QLabel#icon {{ font-size: 15px; color: {MUTED}; }}
QLabel#hint {{ font-size: 12px; color: {FAINT}; padding-left: 6px; }}
QLabel#status {{ font-size: 11px; color: {OK}; }}
QLabel#status[error="true"] {{ color: {ERR}; }}
QFrame#card {{ background: {CARD}; border: 1px solid {CARD_BORDER}; border-radius: 16px; }}
QLineEdit#hex {{
    border: none; border-bottom: 1px solid transparent;
    padding: 0; font-family: "Cascadia Mono", "Consolas", monospace;
    font-size: 22px; font-weight: 600; color: {TEXT};
}}
QLineEdit#hex:hover {{ border-bottom: 1px solid {CARD_BORDER}; }}
QLineEdit#hex:focus {{ border-bottom: 1px solid {MUTED}; }}
QMenu {{ background: #181b23; border: 1px solid {CARD_BORDER}; border-radius: 10px; padding: 5px; }}
QMenu::item {{ padding: 7px 18px 7px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {CARD_BORDER}; }}
QMenu::separator {{ height: 1px; background: {CARD_BORDER}; margin: 4px 8px; }}
QMenu::indicator {{ width: 14px; height: 14px; margin-left: 6px; }}
QToolTip {{ background-color: #181b23; color: {TEXT}; border: 1px solid {CARD_BORDER}; padding: 4px; }}
"""


def _forward_to_running_instance() -> bool:
    """If Controlus already runs, ask it to show its window and return True."""
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE_KEY)
    if not sock.waitForConnected(300):
        return False
    sock.write(b"show")
    sock.waitForBytesWritten(300)
    sock.disconnectFromServer()
    return True


def main():
    start_hidden = "--tray" in sys.argv
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Controlus")
    app.setQuitOnLastWindowClosed(False)
    if _forward_to_running_instance():
        return 0
    QLocalServer.removeServer(INSTANCE_KEY)  # stale socket after a crash
    server = QLocalServer(app)
    server.listen(INSTANCE_KEY)

    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    win = ControlusWindow()

    def on_connection():
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.deleteLater()
        win.show_window()

    server.newConnection.connect(on_connection)
    if start_hidden:
        QTimer.singleShot(1500, trim_working_set)
    else:
        win.show_window()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
