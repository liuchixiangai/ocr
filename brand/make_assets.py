"""Renders every logo file from scoresight_brand.py:

- icons/scoresight.ico: the Windows app, its installer and window icon,
  16 to 256 px; the smallest sizes are drawn bolder, not shrunk
- icons/scoresight-macos.png: the macOS app icon (1024 px, on Apple's icon
  grid: an 824 px tile with a shadow); PyInstaller turns it into an .icns
- icons/splash.png: the Windows build's splash screen
- docs/logo.png and docs/logo-dark.png: the logo with the wordmark, for the
  documentation site and the README (light and dark backgrounds)
- docs/favicon.ico: the documentation site's icon
- brand/social-preview.png: GitHub's social preview (Settings > General)
- brand/logo/: the masters (SVG, and PNG from 16 to 1024 px)

    python brand/make_assets.py        (PySide6 and Pillow; any venv of the app)
"""

from __future__ import annotations

import os
import sys
from io import BytesIO
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def qimage_to_pil(image):
    from PIL import Image
    from PySide6.QtCore import QBuffer, QIODevice

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return Image.open(BytesIO(bytes(buffer.data()))).convert("RGBA")


def mark_image(size: int):
    import scoresight_brand as brand

    return brand.render_png(None, size, size, lambda p: brand.draw_mark(p, 0, 0, size))


def write_ico(path: Path, sizes=ICO_SIZES):
    images = [qimage_to_pil(mark_image(size)) for size in sizes]
    largest = images[-1]
    largest.save(
        path, format="ICO", sizes=[(s, s) for s in sizes], append_images=images[:-1]
    )


def macos_icon(path: Path):
    """Apple's grid: the tile is 824 of 1024 px, with a soft shadow."""
    import scoresight_brand as brand
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QPainterPath

    def draw(p):
        for i in range(28, 0, -2):
            shadow = QPainterPath()
            shadow.addRoundedRect(
                QRectF(100 - i / 2, 100 - i / 2 + 12, 824 + i, 824 + i),
                185 + i / 2,
                185 + i / 2,
            )
            p.fillPath(shadow, brand.color("#000000", 0.012))
        brand.draw_mark(p, 100 - 824 * 8 / 496, 100 - 824 * 8 / 496, 824 * 512 / 496)

    brand.render_png(path, 1024, 1024, draw)


def splash(path: Path):
    """The Windows build's splash: the logo on the dark background. The
    bootloader writes its loading messages at the bottom left (the spec's
    text_pos), in the muted colour."""
    import scoresight_brand as brand
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QBrush, QLinearGradient, QRadialGradient

    width, height = 640, 360

    def draw(p):
        gradient = QLinearGradient(0, 0, width, height)
        gradient.setColorAt(0, brand.color(brand.INK_3))
        gradient.setColorAt(1, brand.color(brand.INK))
        p.fillRect(QRectF(0, 0, width, height), QBrush(gradient))
        glow = QRadialGradient(QPointF(width * 0.3, height * 0.45), width * 0.5)
        glow.setColorAt(0, brand.color(brand.AMBER, 0.10))
        glow.setColorAt(1, brand.color(brand.AMBER, 0.0))
        p.fillRect(QRectF(0, 0, width, height), QBrush(glow))
        p.setPen(Qt.PenStyle.NoPen)
        for y in range(10, height, 16):
            for x in range(width // 2, width, 16):
                p.setBrush(
                    brand.color(
                        brand.AMBER, 0.02 + 0.05 * (x - width / 2) / (width / 2)
                    )
                )
                p.drawEllipse(QPointF(x, y), 2.0, 2.0)
        brand.draw_lockup(p, 70, 110, 120)
        p.setPen(brand.color(brand.MUTED))
        p.setFont(brand.font(17, "medium", brand.TEXT_FONT))
        p.drawText(
            QRectF(73, 246, 520, 30),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "Scoreboard OCR for live sports production",
        )

    brand.render_png(path, width, height, draw)


def lockup_png(path: Path, on_dark: bool, height=160):
    import scoresight_brand as brand

    width = int(height * 4.25)
    brand.render_png(
        path,
        width,
        height + 16,
        lambda p: brand.draw_lockup(p, 8, 8, height, on_dark=on_dark),
    )


def social_preview(path: Path):
    import scoresight_brand as brand
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QBrush, QLinearGradient, QRadialGradient

    width, height = 1280, 640

    def draw(p):
        gradient = QLinearGradient(0, 0, width, height)
        gradient.setColorAt(0, brand.color(brand.INK_3))
        gradient.setColorAt(1, brand.color(brand.INK))
        p.fillRect(QRectF(0, 0, width, height), QBrush(gradient))
        glow = QRadialGradient(QPointF(width * 0.75, height * 0.4), width * 0.5)
        glow.setColorAt(0, brand.color(brand.BLUE, 0.16))
        glow.setColorAt(1, brand.color(brand.BLUE, 0.0))
        p.fillRect(QRectF(0, 0, width, height), QBrush(glow))
        p.setPen(Qt.PenStyle.NoPen)
        for y in range(12, height, 22):
            for x in range(width // 2, width, 22):
                p.setBrush(
                    brand.color(
                        brand.AMBER, 0.03 + 0.06 * (x - width / 2) / (width / 2)
                    )
                )
                p.drawEllipse(QPointF(x, y), 3.0, 3.0)
        brand.draw_lockup(p, 110, 170, 170)
        p.setPen(brand.color(brand.WHITE))
        p.setFont(brand.font(46, "bold"))
        p.drawText(
            QRectF(116, 380, 1100, 64),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "Read any scoreboard. Put it on your stream.",
        )
        p.setPen(brand.color(brand.MUTED))
        p.setFont(brand.font(30, "medium", brand.TEXT_FONT))
        p.drawText(
            QRectF(118, 446, 1100, 50),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "Free and open source · OBS · vMix · browser overlays · CasparCG · and more",
        )

    brand.render_png(path, width, height, draw)


def main():
    from PySide6.QtGui import QGuiApplication

    _app = QGuiApplication.instance() or QGuiApplication(["make_assets"])  # noqa: F841
    import scoresight_brand as brand

    brand.load_fonts()
    logo = HERE / "logo"
    logo.mkdir(exist_ok=True)
    brand.render_svg(
        logo / "scoresight-mark.svg", 512, 512, lambda p: brand.draw_mark(p, 0, 0, 512)
    )
    for size in (1024, 512, 256, 128, 64, 32, 16):
        brand.render_png(
            logo / f"scoresight-mark-{size}.png",
            size,
            size,
            lambda p, s=size: brand.draw_mark(p, 0, 0, s),
        )
    brand.render_svg(
        logo / "scoresight-logo-on-dark.svg",
        1400,
        360,
        lambda p: brand.draw_lockup(p, 40, 40, 280, on_dark=True),
    )
    brand.render_svg(
        logo / "scoresight-logo-on-light.svg",
        1400,
        360,
        lambda p: brand.draw_lockup(p, 40, 40, 280, on_dark=False),
    )
    write_ico(ROOT / "icons" / "scoresight.ico")
    macos_icon(ROOT / "icons" / "scoresight-macos.png")
    splash(ROOT / "icons" / "splash.png")
    lockup_png(ROOT / "docs" / "logo.png", on_dark=False)
    lockup_png(ROOT / "docs" / "logo-dark.png", on_dark=True)
    write_ico(ROOT / "docs" / "favicon.ico", sizes=(16, 32, 48))
    social_preview(HERE / "social-preview.png")
    print("written")


if __name__ == "__main__":
    main()
