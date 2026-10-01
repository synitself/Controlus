"""Interactive probe for the AORUS keyboard + light bar protocol.

The light bar and keyboard firmware can only be judged by eye, so this walks
through candidate commands one at a time, asks what changed, and finishes with
a white-balance calibration for the light bar. Answers go to
%APPDATA%\\Controlus\\light_probe.json.

Run from windows/:  python tools/light_probe.py
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hid  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget,
)

from controlus import backend as b  # noqa: E402

OUT = Path(os.environ.get("APPDATA", ".")) / "Controlus" / "light_probe.json"


def send(pid, *body):
    dev = b._open_giga_control(hid, pid)
    if dev is None:
        return False
    try:
        dev.send_feature_report(b._giga_packet(*body))
        time.sleep(b.GIGA_DELAY_S)
    finally:
        dev.close()
    return True


KBD, BAR = b.GIGABYTE_PRODUCT_ID, b.GIGABYTE_LIGHTBAR_PID


def kbd_custom(r, g, bb, zone_level=50, effect_level=50):
    send(KBD, 0x08, 0x00, 9, 1, effect_level, 0, 0)
    for z in (3, 4, 5):
        send(KBD, 0x08, z, r, g, bb, zone_level, 0)


def bar_rgb(r, g, bb):
    send(BAR, 0x08, 0x01, 0x09, r, g, bb)


def bar_static(color_index, level):
    # SetLightEffect_LightBar: [08, synch, type=Static(1), speed, brightness 0..50, color, dir]
    send(BAR, 0x08, 0x00, 1, 1, level, color_index, 0)


YES_NO = ["Yes, clearly", "A little", "No change"]

# (title, what to watch, action, answer options)
STEPS = [
    ("Baseline", "Keyboard and light bar should both turn WHITE now.\nWhat colour is the light bar?",
     lambda: (kbd_custom(255, 255, 255), bar_rgb(255, 255, 255)),
     ["White", "Pinkish", "Other"]),
    ("Keyboard brightness - zone byte", "Watch the KEYBOARD. Did it get dimmer?",
     lambda: kbd_custom(255, 255, 255, zone_level=8, effect_level=50), YES_NO),
    ("Keyboard brightness - effect byte", "Back to full, then only the effect byte is lowered.\n"
     "Watch the KEYBOARD. Did it get dimmer?",
     lambda: (kbd_custom(255, 255, 255), time.sleep(0.8), kbd_custom(255, 255, 255, 50, 8)), YES_NO),
    ("Keyboard brightness - RGB scale", "Back to full, then the colour values are lowered (255 -> 40).\n"
     "Watch the KEYBOARD. Did it get dimmer?",
     lambda: (kbd_custom(255, 255, 255), time.sleep(0.8), kbd_custom(40, 40, 40)), YES_NO),
    ("Light bar brightness - RGB scale", "Light bar goes white, then 255 -> 40.\n"
     "Watch the LIGHT BAR. Did it get dimmer?",
     lambda: (bar_rgb(255, 255, 255), time.sleep(0.8), bar_rgb(40, 40, 40)), YES_NO),
    ("Light bar - preset white, full", "Light bar uses the firmware's own WHITE preset now.\n"
     "Is it a proper white (matching the keyboard)?",
     lambda: bar_static(7, 50), ["Proper white", "Pinkish", "Off / other"]),
    ("Light bar - preset white, dim", "Same preset, brightness byte lowered.\nDid the LIGHT BAR get dimmer?",
     lambda: bar_static(7, 8), YES_NO),
    ("Light bar - pure red", "What colour is the LIGHT BAR?", lambda: bar_rgb(255, 0, 0),
     ["Red", "Green", "Blue", "Other / off"]),
    ("Light bar - pure green", "What colour is the LIGHT BAR?", lambda: bar_rgb(0, 255, 0),
     ["Red", "Green", "Blue", "Other / off"]),
    ("Light bar - pure blue", "What colour is the LIGHT BAR?", lambda: bar_rgb(0, 0, 255),
     ["Red", "Green", "Blue", "Other / off"]),
    ("Light bar - RGB after preset", "Custom RGB again (purple). Is the LIGHT BAR purple-ish?",
     lambda: (kbd_custom(128, 0, 255), bar_rgb(128, 0, 255)), ["Purple", "Red", "Other"]),
]


class Probe(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Controlus light probe")
        self.setFixedWidth(460)
        self.setWindowFlag(Qt.WindowStaysOnTopHint)
        self.answers = {}
        self.i = 0
        lay = QVBoxLayout(self)
        self.title = QLabel()
        self.title.setStyleSheet("font-size:18px;font-weight:600")
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setStyleSheet("font-size:14px")
        self.buttons = QHBoxLayout()
        lay.addWidget(self.title)
        lay.addWidget(self.text)
        lay.addLayout(self.buttons)
        self.extra = QVBoxLayout()
        lay.addLayout(self.extra)
        self.show_step()

    def clear_buttons(self):
        while self.buttons.count():
            w = self.buttons.takeAt(0).widget()
            if w:
                w.deleteLater()

    def show_step(self):
        self.clear_buttons()
        if self.i >= len(STEPS):
            return self.calibrate()
        title, text, action, options = STEPS[self.i]
        self.title.setText(f"{self.i + 1}/{len(STEPS) + 1}  {title}")
        self.text.setText(text)
        QApplication.processEvents()
        action()
        for opt in options:
            btn = QPushButton(opt)
            btn.setMinimumHeight(34)
            btn.clicked.connect(lambda checked=False, o=opt: self.answer(o))
            self.buttons.addWidget(btn)

    def answer(self, option):
        self.answers[STEPS[self.i][0]] = option
        self.save()
        self.i += 1
        self.show_step()

    def calibrate(self):
        self.title.setText(f"{len(STEPS) + 1}/{len(STEPS) + 1}  Light bar white balance")
        self.text.setText("Keyboard is white. Move the sliders until the LIGHT BAR looks the same white "
                          "as the keys, then press Done.")
        kbd_custom(255, 255, 255)
        self.gains = {"r": 100, "g": 100, "b": 100}
        for ch, name in (("r", "Red"), ("g", "Green"), ("b", "Blue")):
            row = QHBoxLayout()
            label = QLabel(f"{name}: 100%")
            label.setFixedWidth(90)
            s = QSlider(Qt.Horizontal)
            s.setRange(0, 100)
            s.setValue(100)
            s.valueChanged.connect(lambda v, c=ch, l=label, n=name: self.set_gain(c, v, l, n))
            row.addWidget(label)
            row.addWidget(s)
            self.extra.addLayout(row)
        done = QPushButton("Done")
        done.setMinimumHeight(34)
        done.clicked.connect(self.finish)
        self.buttons.addWidget(done)
        self.push_gains()

    def set_gain(self, ch, v, label, name):
        self.gains[ch] = v
        label.setText(f"{name}: {v}%")
        self.push_gains()

    def push_gains(self):
        g = self.gains
        bar_rgb(round(255 * g["r"] / 100), round(255 * g["g"] / 100), round(255 * g["b"] / 100))

    def finish(self):
        self.answers["white_balance"] = self.gains
        self.save()
        self.close()

    def save(self):
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(self.answers, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    app = QApplication([])
    w = Probe()
    w.show()
    app.exec()
