"""The Controlus mark: an open "C" ring with an RGB sweep and a lit LED dot.

Shared by the window, the tray icon (tinted with the current colour) and the
build-time icon generator (tools/make_icon.py).
"""

from __future__ import annotations

import colorsys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QConicalGradient, QIcon, QPainter, QPen, QPixmap

# The gap of the "C" is centred on 3 o'clock; the ring spans the rest.
GAP_DEG = 64


def paint_mark(p: QPainter, rect: QRectF, tint: QColor | None = None, tile: bool = False):
    """Draw the mark into `rect`.

    tint=None paints the full RGB sweep; a colour paints it in that colour
    (the tray uses this to show what the lights are set to). tile=True puts it
    on a dark rounded square, for the app/exe icon.
    """
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    side = min(rect.width(), rect.height())
    c = rect.center()

    if tile:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#14171e"))
        r = side * 0.22
        p.drawRoundedRect(QRectF(c.x() - side / 2, c.y() - side / 2, side, side), r, r)
        side *= 0.74

    width = max(1.6, side * 0.17)
    radius = side / 2 - width / 2 - side * 0.02
    arc = QRectF(c.x() - radius, c.y() - radius, 2 * radius, 2 * radius)
    start = GAP_DEG / 2
    span = 360 - GAP_DEG

    if tint is None:
        grad = QConicalGradient(c, start)
        for i in range(7):
            r_, g_, b_ = colorsys.hsv_to_rgb(i / 6, 0.95, 1.0)
            grad.setColorAt(i / 6 * (span / 360), QColor.fromRgbF(r_, g_, b_))
        grad.setColorAt(1.0, QColor.fromRgbF(*colorsys.hsv_to_rgb(1.0, 0.95, 1.0)))
        pen = QPen(grad, width, Qt.SolidLine, Qt.RoundCap)
        dot = QColor("#ffffff")
    else:
        pen = QPen(tint, width, Qt.SolidLine, Qt.RoundCap)
        dot = tint.lighter(135) if tint.lightness() < 200 else QColor("#ffffff")
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawArc(arc, int(start * 16), int(span * 16))

    # The LED sitting in the mouth of the C.
    p.setPen(Qt.NoPen)
    p.setBrush(dot)
    p.drawEllipse(QPointF(c.x() + radius, c.y()), width * 0.62, width * 0.62)
    p.restore()


def mark_pixmap(size: int, tint: QColor | None = None, tile: bool = False, dpr: float = 1.0) -> QPixmap:
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    paint_mark(p, QRectF(0, 0, size, size), tint, tile)
    p.end()
    return pm


def mark_icon(tint: QColor | None = None, tile: bool = False) -> QIcon:
    icon = QIcon()
    for s in (16, 20, 24, 32, 40, 48, 64, 256):
        icon.addPixmap(mark_pixmap(s, tint, tile))
    return icon
