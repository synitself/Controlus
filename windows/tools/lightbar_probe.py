"""Second probe: which command gives the AORUS light bar an arbitrary RGB colour.

Each step tries one candidate and asks for BLUE, which none of the red-ish
fallbacks can fake. Answers go to %APPDATA%\\Controlus\\lightbar_probe.json.

Run from windows/:  python tools/lightbar_probe.py
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hid  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget  # noqa: E402

from controlus import backend as b  # noqa: E402

OUT = Path(os.environ.get("APPDATA", ".")) / "Controlus" / "lightbar_probe.json"
BAR = b.GIGABYTE_LIGHTBAR_PID
LEDS = 16  # the picture matrix is 64 bytes = 16 LEDs x [0, r, g, b]


def with_bar(fn):
    dev = b._open_giga_control(hid, BAR)
    if dev is None:
        return
    try:
        fn(dev)
    finally:
        dev.close()


def feat(dev, *body):
    dev.send_feature_report(b._giga_packet(*body))
    time.sleep(0.07)


def reset_red(dev):
    # Known-good state between steps: firmware Static, red preset.
    feat(dev, 0x08, 0x00, 1, 1, 50, 1, 0)


def matrix(dev, r, g, bb, slot=0, effect=51):
    # SetPictureMatrix2DeviceLightBar: announce, then a 65-byte output report.
    feat(dev, 0x12, 0x00, slot, 0x08, 0x00)
    dev.write(bytes([0x00] + [0, r, g, bb] * LEDS))
    time.sleep(0.07)
    feat(dev, 0x08, 0x00, effect, 1, 50, 0, 0)


def custom_color(dev, r, g, bb):
    # SetLightbarCustomColor (cmd 0x14), then Static with colour index 0.
    feat(dev, 0x14, 0x00, r, g, bb, 0, 0)
    feat(dev, 0x08, 0x00, 1, 1, 50, 0, 0)


def zone_colors(dev, r, g, bb):
    # SetZoneColorsLightBar2 on zones 1..5 in the Custom effect.
    feat(dev, 0x08, 0x00, 9, 1, 50, 0, 0)
    for z in range(1, 6):
        feat(dev, 0x08, z, r, g, bb, 50, 0)


STEPS = [
    ("Picture matrix + Custom1", lambda d: matrix(d, 0, 0, 255)),
    ("Picture matrix slot 1 + Custom2", lambda d: matrix(d, 0, 0, 255, slot=1, effect=52)),
    ("Custom colour command", lambda d: custom_color(d, 0, 0, 255)),
    ("Zone colours in Custom effect", lambda d: zone_colors(d, 0, 0, 255)),
]


class Probe(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Controlus light bar probe")
        self.setFixedWidth(440)
        self.setWindowFlag(Qt.WindowStaysOnTopHint)
        self.i = 0
        self.answers = {}
        lay = QVBoxLayout(self)
        self.title = QLabel()
        self.title.setStyleSheet("font-size:18px;font-weight:600")
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setStyleSheet("font-size:14px")
        self.row = QHBoxLayout()
        lay.addWidget(self.title)
        lay.addWidget(self.text)
        lay.addLayout(self.row)
        self.step()

    def step(self):
        while self.row.count():
            w = self.row.takeAt(0).widget()
            if w:
                w.deleteLater()
        if self.i >= len(STEPS):
            self.close()
            return
        name, action = STEPS[self.i]
        self.title.setText(f"{self.i + 1}/{len(STEPS)}  {name}")
        self.text.setText("The light bar flashes RED for a moment, then this method tries to make it BLUE.\n"
                          "What colour is the LIGHT BAR now?")
        QApplication.processEvents()
        with_bar(reset_red)
        time.sleep(0.8)
        with_bar(action)
        for opt in ("Blue", "Still red", "Off / other"):
            btn = QPushButton(opt)
            btn.setMinimumHeight(34)
            btn.clicked.connect(lambda checked=False, o=opt: self.answer(o))
            self.row.addWidget(btn)

    def answer(self, option):
        self.answers[STEPS[self.i][0]] = option
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(self.answers, indent=2), encoding="utf-8")
        self.i += 1
        self.step()


if __name__ == "__main__":
    app = QApplication([])
    w = Probe()
    w.show()
    app.exec()
