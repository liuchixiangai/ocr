"""ScoreSight's brand: colours, fonts and the logo, drawn with QPainter, so
that the app's icons and splash screen, the documentation's logo, the
tutorial videos and every exported file (SVG, PNG, ICO) come from the same
geometry. `make_assets.py` renders the files.

The mark: an amber seven-segment "S" (the 5 of a scoreboard) inside the blue
corner brackets of a viewfinder, the box ScoreSight draws around the digits
it reads, on a dark rounded tile.
"""

from __future__ import annotations

import math
from pathlib import Path

FONTS = Path(__file__).resolve().parent / "fonts"

INK = "#0B1220"
INK_2 = "#15213A"
INK_3 = "#1E2C4A"
AMBER = "#FFB020"
AMBER_HOT = "#FFD27A"
BLUE = "#3DA5FF"
WHITE = "#F5F7FA"
MUTED = "#9AA7BD"
RED = "#FF4B3E"

DISPLAY_FONT = "Barlow Semi Condensed"
TEXT_FONT = "Barlow"

_fonts_loaded = False


def load_fonts():
    global _fonts_loaded
    if _fonts_loaded:
        return
    from PySide6.QtGui import QFontDatabase

    for font in FONTS.glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(font))
    _fonts_loaded = True


def font(
    size_px: float,
    weight: str = "bold",
    family: str = DISPLAY_FONT,
    italic=False,
    spacing: float = 0,
):
    from PySide6.QtGui import QFont

    load_fonts()
    f = QFont(family)
    f.setPixelSize(max(1, int(round(size_px))))
    f.setWeight(
        {
            "regular": QFont.Weight.Normal,
            "medium": QFont.Weight.Medium,
            "semibold": QFont.Weight.DemiBold,
            "bold": QFont.Weight.Bold,
            "extrabold": QFont.Weight.ExtraBold,
            "black": QFont.Weight.Black,
        }[weight]
    )
    f.setItalic(italic)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def color(value: str, alpha: float = 1.0):
    from PySide6.QtGui import QColor

    c = QColor(value)
    c.setAlphaF(alpha)
    return c


# -- the mark ------------------------------------------------------------------

SLANT = math.tan(math.radians(8))


def _s_polygons(x0, y0, h):
    """The segments of a seven-segment "S" (a, f, g, c, d) whose box's top
    left is (x0, y0), h high, leaning right."""
    w = h * 0.56
    t = h * 0.165
    g = h * 0.02
    half = t / 2

    def horizontal(yc):
        return [
            (half + g, yc),
            (t + g, yc - half),
            (w - t - g, yc - half),
            (w - half - g, yc),
            (w - t - g, yc + half),
            (t + g, yc + half),
        ]

    def vertical(xc, ya, yb):
        return [
            (xc, ya + half + g),
            (xc + half, ya + t + g),
            (xc + half, yb - t - g),
            (xc, yb - half - g),
            (xc - half, yb - t - g),
            (xc - half, ya + t + g),
        ]

    segments = [
        horizontal(half),
        vertical(half, 0, h / 2),
        horizontal(h / 2),
        vertical(w - half, h / 2, h),
        horizontal(h - half),
    ]
    lean = h * SLANT
    return [
        [(x0 + x + (h - y) * SLANT - lean / 2, y0 + y) for x, y in seg]
        for seg in segments
    ], w


def draw_mark(
    painter,
    x: float,
    y: float,
    size: float,
    tile=True,
    glow=True,
    on_dark=True,
    small=None,
):
    """The mark in a size×size square at (x, y). small (default: size <= 40)
    draws it for tiny icons: bolder brackets and digit, no glow."""
    if small is None:
        small = size <= 40
    if small:
        glow = False
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import (
        QBrush,
        QLinearGradient,
        QPainterPath,
        QPen,
        QPolygonF,
        QRadialGradient,
    )

    painter.save()
    painter.setRenderHint(painter.RenderHint.Antialiasing)
    s = size / 512.0
    if tile:
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(x + 8 * s, y + 8 * s, 496 * s, 496 * s), 116 * s, 116 * s
        )
        gradient = QLinearGradient(x, y, x, y + size)
        gradient.setColorAt(0, color(INK_3))
        gradient.setColorAt(1, color(INK))
        painter.fillPath(path, QBrush(gradient))
        # a soft amber light from behind the digit
        radial = QRadialGradient(QPointF(x + 256 * s, y + 262 * s), 230 * s)
        radial.setColorAt(0, color(AMBER, 0.20))
        radial.setColorAt(1, color(AMBER, 0.0))
        painter.fillPath(path, QBrush(radial))
        if not small:
            painter.setPen(QPen(color("#FFFFFF", 0.07), max(1.0, 3 * s)))
            painter.drawPath(path)
    # the viewfinder's corners
    inset, arm = (70 * s, 110 * s) if small else (92 * s, 82 * s)
    width = 52 * s if small else 30 * s
    pen = QPen(
        color(BLUE),
        width,
        Qt.PenStyle.SolidLine,
        Qt.PenCapStyle.RoundCap,
        Qt.PenJoinStyle.RoundJoin,
    )
    painter.setPen(pen)
    left, top, right, bottom = x + inset, y + inset, x + size - inset, y + size - inset
    r = 26 * s
    for cx, cy, dx, dy in (
        (left, top, 1, 1),
        (right, top, -1, 1),
        (right, bottom, -1, -1),
        (left, bottom, 1, -1),
    ):
        corner = QPainterPath()
        corner.moveTo(cx, cy + dy * arm)
        corner.lineTo(cx, cy + dy * r)
        corner.quadTo(cx, cy, cx + dx * r, cy)
        corner.lineTo(cx + dx * arm, cy)
        painter.drawPath(corner)
    # the digit
    height = (330 if small else 268) * s
    polygons, width = _s_polygons(
        x + 256 * s - 0.56 * height / 2, y + 256 * s - height / 2, height
    )
    painter.setPen(Qt.PenStyle.NoPen)
    if glow:
        for spread, alpha in ((10 * s, 0.10), (6 * s, 0.14), (3 * s, 0.18)):
            painter.setPen(
                QPen(
                    color(AMBER, alpha),
                    spread * 2,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            for poly in polygons:
                painter.drawPolygon(QPolygonF([QPointF(px, py) for px, py in poly]))
        painter.setPen(Qt.PenStyle.NoPen)
    gradient = QLinearGradient(x, y + 140 * s, x, y + 380 * s)
    gradient.setColorAt(0, color(AMBER_HOT))
    gradient.setColorAt(0.35, color(AMBER))
    gradient.setColorAt(1, color("#FF9A1A"))
    painter.setBrush(QBrush(gradient))
    for poly in polygons:
        painter.drawPolygon(QPolygonF([QPointF(px, py) for px, py in poly]))
    painter.restore()


def draw_wordmark(
    painter, x: float, y_baseline: float, height: float, on_dark=True, two_tone=True
):
    """The wordmark "ScoreSight", its cap height about height, from (x,
    baseline); returns its width."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QFontMetricsF, QPainterPath

    f = font(height * 1.42, "bold", DISPLAY_FONT, spacing=height * 0.02)
    metrics = QFontMetricsF(f)
    painter.save()
    painter.setRenderHint(painter.RenderHint.Antialiasing)
    first, second = "Score", "Sight"
    path1 = QPainterPath()
    path1.addText(QPointF(x, y_baseline), f, first)
    w1 = metrics.horizontalAdvance(first)
    path2 = QPainterPath()
    path2.addText(QPointF(x + w1, y_baseline), f, second)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.fillPath(path1, color(WHITE if on_dark else INK))
    painter.fillPath(path2, color(BLUE if two_tone else (WHITE if on_dark else INK)))
    painter.restore()
    return w1 + metrics.horizontalAdvance(second)


def draw_lockup(painter, x, y, height, on_dark=True):
    """Mark and wordmark side by side, height tall; returns the width."""
    draw_mark(painter, x, y, height)
    cap = height * 0.42
    w = draw_wordmark(
        painter, x + height * 1.16, y + height / 2 + cap / 2, cap, on_dark=on_dark
    )
    return height * 1.16 + w


# -- exports -------------------------------------------------------------------


def render_png(
    path: Path, width: int, height: int, draw, background: str | None = None
):
    from PySide6.QtGui import QImage, QPainter

    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(color(background) if background else color("#000000", 0.0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    draw(painter)
    painter.end()
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(str(path))
    return image


def render_svg(path: Path, width: int, height: int, draw, title="ScoreSight"):
    from PySide6.QtCore import QRect, QSize
    from PySide6.QtGui import QPainter
    from PySide6.QtSvg import QSvgGenerator

    generator = QSvgGenerator()
    generator.setFileName(str(path))
    generator.setSize(QSize(width, height))
    generator.setViewBox(QRect(0, 0, width, height))
    generator.setTitle(title)
    painter = QPainter(generator)
    draw(painter)
    painter.end()
