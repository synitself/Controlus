#!/usr/bin/env python3
"""Controlus for Windows - Keyboard & Mouse RGB Control (PySide6 GUI).

Windows port of the original GTK4/libadwaita app.

Supports:
  - Gigabyte/AORUS keyboards
  - Logitech G Pro Wireless mouse
  - Any OpenRGB-supported device
"""

from __future__ import annotations

import colorsys
import json
import os
from pathlib import Path

from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

try:
    from controlus.backend import set_color as backend_set_color
except Exception:  # pragma: no cover - import fallback for frozen/standalone runs
    try:
        from .backend import set_color as backend_set_color  # type: ignore
    except Exception:
        backend_set_color = None  # type: ignore

# Config lives in %APPDATA%\Controlus on Windows.
CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "Controlus"
CONFIG_FILE = CONFIG_DIR / "config.json"

WHEEL_SIZE = 280


# ---------------------------------------------------------------------------
# Color wheel
# ---------------------------------------------------------------------------

class ColorWheel(QWidget):
    """HSV color wheel: angle = hue, radius = saturation, value from brightness."""

    color_changed = Signal(tuple)  # (r, g, b)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(WHEEL_SIZE, WHEEL_SIZE)
        self.setCursor(Qt.CrossCursor)

        self.hue = 0.0
        self.saturation = 1.0
        self.value = 1.0

        self._wheel_cache: QImage | None = None
        self._cache_value = -1.0

    # -- color conversions ----------------------------------------------------
    def get_rgb(self) -> tuple[int, int, int]:
        r, g, b = colorsys.hsv_to_rgb(self.hue, self.saturation, self.value)
        return (round(r * 255), round(g * 255), round(b * 255))

    def set_rgb(self, r: int, g: int, b: int):
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        self.hue, self.saturation, self.value = h, s, v
        self.update()

    def set_value(self, value: float):
        self.value = max(0.0, min(1.0, value))
        self.update()

    # -- rendering ------------------------------------------------------------
    def _build_wheel(self, radius: float) -> QImage:
        size = int(radius * 2)
        img = QImage(size, size, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        cx = cy = radius
        v = self.value
        for y in range(size):
            for x in range(size):
                dx = x - cx
                dy = y - cy
                dist = (dx * dx + dy * dy) ** 0.5
                if dist > radius:
                    continue
                import math
                angle = math.atan2(dy, dx)
                h = (angle / (2 * math.pi)) % 1.0
                s = min(1.0, dist / radius)
                r, g, b = colorsys.hsv_to_rgb(h, s, v)
                img.setPixelColor(x, y, QColor(round(r * 255), round(g * 255), round(b * 255)))
        return img

    def paintEvent(self, event):
        import math
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        radius = min(w, h) / 2 - 10

        if self._wheel_cache is None or self._cache_value != self.value:
            self._wheel_cache = self._build_wheel(radius)
            self._cache_value = self.value

        painter.drawImage(QPointF(cx - radius, cy - radius), self._wheel_cache)

        # Selector dot
        sel_angle = self.hue * 2 * math.pi
        sel_radius = self.saturation * radius
        sx = cx + sel_radius * math.cos(sel_angle)
        sy = cy + sel_radius * math.sin(sel_angle)

        r, g, b = self.get_rgb()
        painter.setBrush(QColor(r, g, b))
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.drawEllipse(QPointF(sx, sy), 10, 10)
        painter.setPen(QPen(QColor(0, 0, 0, 120), 1))
        painter.drawEllipse(QPointF(sx, sy), 11, 11)

    # -- interaction ----------------------------------------------------------
    def _update_from_pos(self, pos: QPointF):
        import math
        cx, cy = self.width() / 2, self.height() / 2
        radius = min(self.width(), self.height()) / 2 - 10
        dx = pos.x() - cx
        dy = pos.y() - cy
        angle = math.atan2(dy, dx)
        self.hue = (angle / (2 * math.pi)) % 1.0
        dist = (dx * dx + dy * dy) ** 0.5
        self.saturation = min(1.0, dist / radius)
        self.update()
        self.color_changed.emit(self.get_rgb())

    def mousePressEvent(self, event):
        self._update_from_pos(event.position())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self._update_from_pos(event.position())


# ---------------------------------------------------------------------------
# Small widgets
# ---------------------------------------------------------------------------

class Swatch(QFrame):
    """A rounded color preview swatch."""

    def __init__(self, rgb=(255, 0, 0), size=(60, 40), parent=None):
        super().__init__(parent)
        self._rgb = rgb
        self.setFixedSize(*size)

    def set_rgb(self, r, g, b):
        self._rgb = (r, g, b)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(*self._rgb))
        painter.setPen(QPen(QColor(255, 255, 255, 40), 1))
        painter.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 8, 8)


class FavoriteButton(QPushButton):
    """A favorite color chip. Left-click applies, right-click removes."""

    remove_requested = Signal(int)

    def __init__(self, r, g, b, index, parent=None):
        super().__init__(parent)
        self.r, self.g, self.b = r, g, b
        self.index = index
        self.setFixedSize(44, 44)
        self.setToolTip(f"RGB({r}, {g}, {b})\nRight-click to remove")
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(self.r, self.g, self.b))
        painter.setPen(QPen(QColor(255, 255, 255, 50), 1))
        painter.drawRoundedRect(QRectF(2.5, 2.5, self.width() - 5, self.height() - 5), 10, 10)

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self.remove_requested.emit(self.index)
        else:
            super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class ControlusWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Controlus")
        self.setMinimumWidth(400)

        self.config = self.load_config()
        self.current_rgb = self._get_initial_color()

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 24)
        root.setSpacing(18)

        # Title
        title = QLabel("Controlus")
        title.setObjectName("title")
        subtitle = QLabel("RGB Control")
        subtitle.setObjectName("subtitle")
        head = QVBoxLayout()
        head.setSpacing(0)
        head.addWidget(title)
        head.addWidget(subtitle)
        root.addLayout(head)

        # Color wheel
        self.wheel = ColorWheel()
        self.wheel.color_changed.connect(self.on_wheel_changed)
        wheel_row = QHBoxLayout()
        wheel_row.addStretch()
        wheel_row.addWidget(self.wheel)
        wheel_row.addStretch()
        root.addLayout(wheel_row)

        # Preview + label
        self.swatch = Swatch(self.current_rgb)
        self.color_label = QLabel()
        self.color_label.setObjectName("mono")
        prev_row = QHBoxLayout()
        prev_row.addStretch()
        prev_row.addWidget(self.swatch)
        prev_row.addWidget(self.color_label)
        prev_row.addStretch()
        root.addLayout(prev_row)

        # Brightness
        bright_row = QHBoxLayout()
        bright_row.addWidget(QLabel("Brightness"))
        self.brightness = QSlider(Qt.Horizontal)
        self.brightness.setRange(0, 100)
        self.brightness.setValue(int(self.config.get("brightness", 100)))
        self.brightness.valueChanged.connect(self.on_brightness_changed)
        bright_row.addWidget(self.brightness, 1)
        self.bright_label = QLabel(f"{self.brightness.value()}%")
        self.bright_label.setObjectName("mono")
        self.bright_label.setFixedWidth(40)
        bright_row.addWidget(self.bright_label)
        root.addLayout(bright_row)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.clicked.connect(self.on_apply)
        self.save_btn = QPushButton("♥ Save")
        self.save_btn.clicked.connect(self.on_add_favorite)
        self.off_btn = QPushButton("Off")
        self.off_btn.setObjectName("destructive")
        self.off_btn.clicked.connect(self.on_off)
        for b in (self.apply_btn, self.save_btn, self.off_btn):
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumHeight(36)
        btn_row.addWidget(self.apply_btn, 2)
        btn_row.addWidget(self.save_btn, 1)
        btn_row.addWidget(self.off_btn, 1)
        root.addLayout(btn_row)

        # Favorites
        fav_title = QLabel("Favorites")
        fav_title.setObjectName("section")
        root.addWidget(fav_title)
        self.fav_hint = QLabel("Click ♥ Save to add the current color · right-click a chip to remove")
        self.fav_hint.setObjectName("subtitle")
        self.fav_hint.setWordWrap(True)
        root.addWidget(self.fav_hint)

        self.fav_container = QWidget()
        self.fav_grid = QGridLayout(self.fav_container)
        self.fav_grid.setContentsMargins(0, 4, 0, 0)
        self.fav_grid.setSpacing(8)
        root.addWidget(self.fav_container)

        # Status toast (inline label at the bottom)
        self.status = QLabel("")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        root.addStretch()

        self.wheel.set_rgb(*self.current_rgb)
        self.wheel.set_value(self.brightness.value() / 100)
        self.update_color_display()
        self.rebuild_favorites()

    # -- color flow -----------------------------------------------------------
    def on_wheel_changed(self, rgb):
        self.current_rgb = rgb
        self.update_color_display()

    def on_brightness_changed(self, value):
        self.bright_label.setText(f"{value}%")
        self.wheel.set_value(value / 100)
        self.current_rgb = self.wheel.get_rgb()
        self.update_color_display()

    def update_color_display(self):
        r, g, b = self.current_rgb
        self.color_label.setText(f"RGB({r}, {g}, {b})")
        self.swatch.set_rgb(r, g, b)

    # -- actions --------------------------------------------------------------
    def on_apply(self):
        r, g, b = self.current_rgb
        self.apply_color(r, g, b, int(self.brightness.value()))

    def on_off(self):
        self.apply_color(0, 0, 0, 0)

    def apply_color(self, r, g, b, brightness=100):
        if backend_set_color is None:
            self.show_status("Error: backend not available (install hidapi)", error=True)
            return
        try:
            success, msg = backend_set_color(r, g, b, brightness)
        except Exception as e:
            success, msg = False, str(e)

        if success:
            self.config["last_color"] = {"r": r, "g": g, "b": b}
            self.config["brightness"] = brightness
            self.save_config()
            self.show_status(f"Applied {msg}")
        else:
            self.show_status(f"Error: {msg}", error=True)

    def show_status(self, message, error=False):
        self.status.setText(message)
        self.status.setProperty("error", "true" if error else "false")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    # -- favorites ------------------------------------------------------------
    def on_add_favorite(self):
        r, g, b = self.current_rgb
        favs = self.config.setdefault("favorites", [])
        for fav in favs:
            if (fav["r"], fav["g"], fav["b"]) == (r, g, b):
                self.show_status("Color already in favorites")
                return
        favs.append({"r": r, "g": g, "b": b})
        self.save_config()
        self.rebuild_favorites()
        self.show_status("Added to favorites")

    def on_favorite_clicked(self, r, g, b):
        self.current_rgb = (r, g, b)
        self.wheel.set_rgb(r, g, b)
        self.update_color_display()
        self.apply_color(r, g, b, int(self.brightness.value()))

    def remove_favorite(self, index):
        favs = self.config.get("favorites", [])
        if 0 <= index < len(favs):
            removed = favs.pop(index)
            self.save_config()
            self.rebuild_favorites()
            self.show_status(f"Removed RGB({removed['r']}, {removed['g']}, {removed['b']})")

    def rebuild_favorites(self):
        while self.fav_grid.count():
            item = self.fav_grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        favs = self.config.get("favorites", [])
        self.fav_hint.setVisible(not favs)

        per_row = 7
        for i, fav in enumerate(favs):
            r, g, b = fav["r"], fav["g"], fav["b"]
            chip = FavoriteButton(r, g, b, i)
            chip.clicked.connect(lambda checked=False, rr=r, gg=g, bb=b: self.on_favorite_clicked(rr, gg, bb))
            chip.remove_requested.connect(self.remove_favorite)
            self.fav_grid.addWidget(chip, i // per_row, i % per_row)

    # -- config ---------------------------------------------------------------
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

    def _get_initial_color(self):
        last = self.config.get("last_color")
        if last and last.get("r") is not None:
            return (last["r"], last["g"], last["b"])
        return (0, 255, 255)  # default cyan


DARK_STYLE = """
QWidget {
    background-color: #1e1e2e;
    color: #cdd6f4;
    font-family: "Segoe UI", sans-serif;
    font-size: 14px;
}
QLabel#title { font-size: 22px; font-weight: 600; color: #ffffff; }
QLabel#subtitle { font-size: 12px; color: #9399b2; }
QLabel#section { font-size: 15px; font-weight: 600; margin-top: 6px; }
QLabel#mono { font-family: "Cascadia Code", "Consolas", monospace; color: #bac2de; }
QLabel#status { font-size: 12px; color: #a6e3a1; min-height: 16px; }
QLabel#status[error="true"] { color: #f38ba8; }
QPushButton {
    background-color: #313244;
    border: none;
    border-radius: 10px;
    padding: 8px 14px;
    color: #cdd6f4;
}
QPushButton:hover { background-color: #45475a; }
QPushButton:pressed { background-color: #585b70; }
QPushButton#primary { background-color: #89b4fa; color: #11111b; font-weight: 600; }
QPushButton#primary:hover { background-color: #a6c8ff; }
QPushButton#destructive { background-color: #f38ba8; color: #11111b; font-weight: 600; }
QPushButton#destructive:hover { background-color: #ff9eb9; }
QSlider::groove:horizontal { height: 6px; background: #313244; border-radius: 3px; }
QSlider::sub-page:horizontal { background: #89b4fa; border-radius: 3px; }
QSlider::handle:horizontal {
    background: #ffffff; width: 16px; height: 16px;
    margin: -6px 0; border-radius: 8px;
}
QToolTip { background-color: #313244; color: #cdd6f4; border: 1px solid #45475a; }
"""


def main():
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Controlus")
    app.setStyleSheet(DARK_STYLE)
    win = ControlusWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
