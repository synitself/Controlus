"""Render the Controlus mark into assets/controlus.ico (+ a 512 px PNG).

Run from windows/:  python tools/make_icon.py
The .ico holds PNG-compressed entries, which Windows Vista+ reads natively, so
no imaging library is needed.
"""

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from controlus.logo import mark_pixmap  # noqa: E402

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def png_bytes(pixmap) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    pixmap.save(buf, "PNG")
    return bytes(data)


def main():
    app = QGuiApplication([])  # noqa: F841 - QPixmap needs a GUI app
    out = ROOT / "assets"
    out.mkdir(exist_ok=True)

    images = [png_bytes(mark_pixmap(s, tile=True)) for s in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, img in zip(SIZES, images):
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(img), offset)
        offset += len(img)
        blobs += img
    (out / "controlus.ico").write_bytes(header + entries + blobs)
    mark_pixmap(512, tile=True).save(str(out / "controlus.png"))
    print("wrote", out / "controlus.ico")


if __name__ == "__main__":
    main()
