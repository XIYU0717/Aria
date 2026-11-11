"""Build Aria into a single Windows executable."""

import os
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "app"
ASSETS = ROOT / "assets"

sys.path.insert(0, str(APP))


def build_icon() -> Path:
    """Render the app mark as PNGs and pack them into a Vista-style .ico."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    ASSETS.mkdir(parents=True, exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    png_blobs = []
    for size in sizes:
        img = QImage(size, size, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        grad = QLinearGradient(0, 0, size, size)
        grad.setColorAt(0, QColor("#5F86FF"))
        grad.setColorAt(1, QColor("#A071FF"))
        path = QPainterPath()
        path.addRoundedRect(QRectF(size * 0.06, size * 0.06, size * 0.88, size * 0.88),
                            size * 0.26, size * 0.26)
        p.fillPath(path, grad)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 235))
        bars = [0.28, 0.5, 0.72, 0.5, 0.3]
        bar_w = size * 0.075
        for i, ratio in enumerate(bars):
            h = size * 0.42 * ratio * 1.5
            x = size * 0.24 + i * size * 0.12
            p.drawRoundedRect(QRectF(x, size / 2 - h / 2, bar_w, h), bar_w / 2, bar_w / 2)
        p.end()
        png_path = ASSETS / f"aria-{size}.png"
        img.save(str(png_path), "PNG")
        png_blobs.append((size, png_path.read_bytes()))

    ico_path = ASSETS / "aria.ico"
    header = struct.pack("<HHH", 0, 1, len(png_blobs))
    directory = b""
    offset = 6 + 16 * len(png_blobs)
    payload = b""
    for size, blob in png_blobs:
        dim = 0 if size >= 256 else size
        directory += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
        payload += blob
    ico_path.write_bytes(header + directory + payload)
    return ico_path


def build_exe() -> int:
    icon = build_icon()
    version_file = None
    cmd = [
        sys.executable, "-m", "PyInstaller",
        # A one-file build has to unpack ~200 MB into %TEMP% before any Python
        # code runs, which is the main cause of the multi-second first-launch
        # freeze.  An on-disk folder build starts immediately.
        "--noconfirm", "--clean", "--onedir", "--windowed",
        "--name", "Aria",
        "--icon", str(icon),
        "--paths", str(APP),
        "--collect-submodules", "PySide6.QtMultimedia",
        "--add-data", f"{ROOT / 'folia_stage'};folia_stage",
        str(APP / "main.py"),
    ]
    print(" ".join(cmd))
    result = subprocess.run(cmd, cwd=str(ROOT))
    exe = ROOT / "dist" / "Aria" / "Aria.exe"
    print("exe exists:", exe.exists(), exe.stat().st_size if exe.exists() else "")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(build_exe())
