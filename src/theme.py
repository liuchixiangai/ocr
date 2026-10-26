"""Semantic colours of the main window (reading states, the paused state, the
outputs' states), with a light and a dark variant, and the state glyphs.

The variant follows the palette, so the colours change with File > Appearance
and with a dark system theme alike. Every colour is at least 4.5:1 against the
palette's base colour of its variant, and the states are also told apart by a
glyph or a word: colour is never the only cue.
"""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QIcon,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)

# name: (light, dark)
_COLORS = {
    "new": ("#1A7F37", "#3FB950"),  # a new reading (Success)
    "same": ("#57606A", "#8B949E"),  # the same as the last good one (SameNoChange)
    "rejected": ("#9A6700", "#D29922"),  # rejected by the filters (FailedFilter)
    "empty": ("#CF222E", "#F85149"),  # nothing read (Empty)
    "paused": ("#BC4C00", "#F0883E"),  # outputs paused
}

# (light, dark) colour of the AI features (Auto crop, Auto layout): their
# buttons carry a sparkle in it, so it's clear the model suggests, not knows
_AI_COLOR = ("#8250DF", "#A371F7")
_AI_TINT = ("#FBEFFF", "#2B1D45")

# (light, dark) background tint of the paused banner
_PAUSED_TINT = ("#FFF1E5", "#3D2410")
# (light, dark) background tints of messages about a problem, by colour name
_TINTS = {
    "rejected": ("#FFF8E5", "#3A2E12"),
    "empty": ("#FFEBE9", "#3D1D1D"),
    "paused": _PAUSED_TINT,
}


def _palette(palette: QPalette | None) -> QPalette:
    return palette if palette is not None else QGuiApplication.palette()


def is_dark(palette: QPalette | None = None) -> bool:
    """Whether the palette is a dark one (light text on a dark window)."""
    palette = _palette(palette)
    return palette.color(QPalette.ColorRole.Window).lightness() < 128


def color(name: str, palette: QPalette | None = None) -> QColor:
    """The colour for name ("new", "same", "rejected", "empty", "paused")."""
    light, dark = _COLORS[name]
    return QColor(dark if is_dark(palette) else light)


def on_color(palette: QPalette | None = None) -> QColor:
    """The colour of text and glyphs drawn on a filled color() background."""
    return QColor("#1B1F24" if is_dark(palette) else "#FFFFFF")


def paused_tint(palette: QPalette | None = None) -> QColor:
    """A light (or, in a dark palette, dark) amber background for paused UI."""
    light, dark = _PAUSED_TINT
    return QColor(dark if is_dark(palette) else light)


def tint(name: str, palette: QPalette | None = None) -> QColor:
    """A light (or, in a dark palette, dark) background for a message in the
    colour name ("rejected": amber, "empty": red)."""
    light, dark = _TINTS[name]
    return QColor(dark if is_dark(palette) else light)


def muted_color(palette: QPalette | None = None) -> QColor:
    """The colour of secondary text."""
    return _palette(palette).color(QPalette.ColorRole.PlaceholderText)


def draw_state_shape(painter: QPainter, rect: QRectF, shape: str, color: QColor):
    """Draw a state glyph in rect: "disc" (●), "ring" (○), "half" (◐),
    "triangle" (▲) or "square" (■). Each state has its own shape, so it never
    depends on colour alone; drawn, so it looks the same with every font."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    side = min(rect.width(), rect.height())
    box = QRectF(rect.center().x() - side / 2, rect.center().y() - side / 2, side, side)
    pen_width = max(1.2, side / 7.0)
    inner = box.adjusted(pen_width / 2, pen_width / 2, -pen_width / 2, -pen_width / 2)
    if shape == "disc":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(
            box.adjusted(side * 0.08, side * 0.08, -side * 0.08, -side * 0.08)
        )
    elif shape == "ring":
        painter.setPen(QPen(color, pen_width))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(
            inner.adjusted(side * 0.06, side * 0.06, -side * 0.06, -side * 0.06)
        )
    elif shape == "half":
        circle = inner.adjusted(side * 0.06, side * 0.06, -side * 0.06, -side * 0.06)
        painter.setPen(QPen(color, pen_width))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(circle)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPie(circle, 90 * 16, 180 * 16)  # the left half
    elif shape == "triangle":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        path = QPainterPath(QPointF(box.center().x(), box.top() + side * 0.05))
        path.lineTo(QPointF(box.right() - side * 0.02, box.bottom() - side * 0.1))
        path.lineTo(QPointF(box.left() + side * 0.02, box.bottom() - side * 0.1))
        path.closeSubpath()
        painter.drawPath(path)
    elif shape == "square":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRect(
            box.adjusted(side * 0.14, side * 0.14, -side * 0.14, -side * 0.14)
        )
    painter.restore()


def state_icon(shape: str, color: QColor, size: int = 12) -> QIcon:
    """An icon of draw_state_shape()."""
    ratio = 2.0  # sharp on high-DPI screens
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    draw_state_shape(painter, QRectF(0, 0, size, size), shape, color)
    painter.end()
    return QIcon(pixmap)


def media_icon(kind: str, color: QColor, size: int = 16) -> QIcon:
    """A "play", "pause" or "stop" icon drawn in color, so it stays visible on
    light and dark buttons (the style's media icons are dark)."""
    ratio = 2.0  # sharp on high-DPI screens
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    unit = size / 16.0
    if kind == "play":
        path = QPainterPath(QPointF(4.5 * unit, 3 * unit))
        path.lineTo(QPointF(13 * unit, 8 * unit))
        path.lineTo(QPointF(4.5 * unit, 13 * unit))
        path.closeSubpath()
        painter.drawPath(path)
    elif kind == "pause":
        painter.drawRect(QRectF(4 * unit, 3 * unit, 3 * unit, 10 * unit))
        painter.drawRect(QRectF(9 * unit, 3 * unit, 3 * unit, 10 * unit))
    elif kind == "stop":
        painter.drawRect(QRectF(4 * unit, 4 * unit, 8 * unit, 8 * unit))
    painter.end()
    return QIcon(pixmap)


def button_text_color(palette: QPalette | None = None) -> QColor:
    return _palette(palette).color(QPalette.ColorRole.ButtonText)


def overlay_color(name: str) -> QColor:
    """A state colour (as color()) for drawing over the video: the bright
    (dark theme) variant whatever the theme, as the picture is dark or
    colourful in both."""
    return QColor(_COLORS[name][1])


def tool_icon(kind: str, color: QColor, size: int = 16) -> QIcon:
    """An icon drawn in color, so it stays visible on light and dark buttons:
    "reload" (a circular arrow), "settings" (a gear), "binary" (a square half
    filled), "stats" (bars), "fit" (four corners), "chat" (a speech bubble) or
    "sparkle" (a big and a small four-pointed star: the AI features)."""
    ratio = 2.0  # sharp on high-DPI screens
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    unit = size / 16.0
    if kind == "reload":
        pen = QPen(color, 1.6 * unit)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # three quarters of a circle, open at the top right
        painter.drawArc(
            QRectF(3 * unit, 3 * unit, 10 * unit, 10 * unit), 75 * 16, 285 * 16
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        head = QPainterPath(QPointF(9.2 * unit, 0.6 * unit))
        head.lineTo(QPointF(13.6 * unit, 3.4 * unit))
        head.lineTo(QPointF(8.8 * unit, 6.2 * unit))
        head.closeSubpath()
        painter.drawPath(head)
    elif kind == "settings":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        center = QPointF(8 * unit, 8 * unit)
        for step in range(8):
            painter.save()
            painter.translate(center)
            painter.rotate(step * 45)
            painter.drawRoundedRect(
                QRectF(-1.4 * unit, -7.2 * unit, 2.8 * unit, 3.2 * unit),
                0.6 * unit,
                0.6 * unit,
            )
            painter.restore()
        ring = QPainterPath()
        ring.addEllipse(center, 5.2 * unit, 5.2 * unit)
        hole = QPainterPath()
        hole.addEllipse(center, 2.2 * unit, 2.2 * unit)
        painter.drawPath(ring.subtracted(hole))
    elif kind == "binary":
        square = QRectF(2.5 * unit, 2.5 * unit, 11 * unit, 11 * unit)
        painter.setPen(QPen(color, 1.2 * unit))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(square)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        half = QPainterPath(square.topLeft())
        half.lineTo(square.topRight())
        half.lineTo(square.bottomLeft())
        half.closeSubpath()
        painter.drawPath(half)
    elif kind == "stats":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        for left, top in ((2.5, 8.5), (6.75, 3.5), (11, 6.5)):
            painter.drawRect(
                QRectF(left * unit, top * unit, 2.5 * unit, (13.5 - top) * unit)
            )
    elif kind == "fit":
        pen = QPen(color, 1.5 * unit)
        pen.setCapStyle(Qt.PenCapStyle.SquareCap)
        painter.setPen(pen)
        for x, y, dx, dy in (
            (2.5, 2.5, 1, 1),
            (13.5, 2.5, -1, 1),
            (2.5, 13.5, 1, -1),
            (13.5, 13.5, -1, -1),
        ):
            corner = QPointF(x * unit, y * unit)
            painter.drawLine(corner, corner + QPointF(3.5 * dx * unit, 0))
            painter.drawLine(corner, corner + QPointF(0, 3.5 * dy * unit))
    elif kind == "chat":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        bubble = QPainterPath()
        bubble.addRoundedRect(
            QRectF(1.5 * unit, 2 * unit, 13 * unit, 9.5 * unit), 2.5 * unit, 2.5 * unit
        )
        # the tail, at the bottom left
        tail = QPainterPath(QPointF(4 * unit, 10.5 * unit))
        tail.lineTo(QPointF(4 * unit, 14.5 * unit))
        tail.lineTo(QPointF(8.5 * unit, 10.5 * unit))
        tail.closeSubpath()
        painter.drawPath(bubble.united(tail))
    elif kind == "sparkle":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        for cx, cy, r in ((6.5, 9.0, 6.0), (12.8, 3.6, 2.9)):
            painter.drawPath(_star(QPointF(cx * unit, cy * unit), r * unit))
    painter.end()
    return QIcon(pixmap)


def ai_color(palette: QPalette | None = None) -> QColor:
    """The colour of the AI features' buttons (violet)."""
    light, dark = _AI_COLOR
    return QColor(dark if is_dark(palette) else light)


def ai_button_style(names, palette: QPalette | None = None) -> str:
    """A style sheet for the AI features' tool buttons (by object name): the
    AI colour's text and border on its light (or dark) tint, filled while
    busy (checked). A pixel less padding than tool_button_style's on each
    side, so that the viewer column keeps its minimum width."""
    palette = _palette(palette)
    role = QPalette.ColorRole
    disabled = QPalette.ColorGroup.Disabled
    color = ai_color(palette).name()
    light, dark = _AI_TINT
    background = dark if is_dark(palette) else light
    return "".join(
        f"QToolButton#{name} {{"
        f" background-color: {background}; color: {color};"
        f" border: 1px solid {color}; border-radius: 3px; padding: 2px 4px; }}"
        f" QToolButton#{name}:hover {{ background-color: {palette.color(role.Base).name()}; }}"
        f" QToolButton#{name}:checked {{ background-color: {color};"
        f" color: {on_color(palette).name()}; }}"
        f" QToolButton#{name}:disabled {{"
        f" color: {palette.color(disabled, role.ButtonText).name()};"
        f" border-color: {palette.color(disabled, role.Mid).name()}; }}"
        for name in names
    )


def _star(center: QPointF, radius: float) -> QPainterPath:
    """A four-pointed star with concave sides (a sparkle)."""
    path = QPainterPath(center + QPointF(0, -radius))
    waist = 0.18 * radius
    for dx, dy, wx, wy in (
        (1, 0, 1, -1),
        (0, 1, 1, 1),
        (-1, 0, -1, 1),
        (0, -1, -1, -1),
    ):
        path.quadTo(
            center + QPointF(wx * waist, wy * waist),
            center + QPointF(dx * radius, dy * radius),
        )
    path.closeSubpath()
    return path


def tool_button_style(palette: QPalette | None = None) -> str:
    """A style sheet for rows of tool buttons: a checked one is filled with
    the highlight colour, so on and off are told apart at a glance (the
    native look of a checked tool button is a subtle shade in some styles).
    Every state has the same border and padding, so toggling a button never
    moves its neighbours."""
    palette = _palette(palette)
    role = QPalette.ColorRole
    disabled = QPalette.ColorGroup.Disabled
    highlight = palette.color(role.Highlight).name()
    return (
        "QToolButton {"
        f" background-color: {palette.color(role.Button).name()};"
        f" color: {palette.color(role.ButtonText).name()};"
        f" border: 1px solid {palette.color(role.Mid).name()};"
        " border-radius: 3px; padding: 2px 5px; }"
        f" QToolButton:hover {{ border-color: {highlight}; }}"
        f" QToolButton:pressed {{ background-color: {palette.color(role.Midlight).name()}; }}"
        " QToolButton:checked {"
        f" background-color: {highlight};"
        f" color: {palette.color(role.HighlightedText).name()};"
        f" border-color: {highlight}; }}"
        " QToolButton:disabled {"
        f" color: {palette.color(disabled, role.ButtonText).name()};"
        f" border-color: {palette.color(disabled, role.Mid).name()}; }}"
        " QToolButton:checked:disabled {"
        f" background-color: {palette.color(disabled, role.Highlight).name()}; }}"
    )


def step_pixmap(
    number: int, done: bool, palette: QPalette | None = None, size: int = 22
) -> QPixmap:
    """A numbered step of a list of steps: the number in a ring, or a check
    mark on a filled disc ("new" green) once the step is done."""
    palette = _palette(palette)
    ratio = 2.0
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    box = QRectF(1, 1, size - 2, size - 2)
    if done:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color("new", palette))
        painter.drawEllipse(box)
        pen = QPen(on_color(palette), max(1.6, size / 10.0))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        check = QPainterPath(QPointF(size * 0.29, size * 0.52))
        check.lineTo(QPointF(size * 0.44, size * 0.67))
        check.lineTo(QPointF(size * 0.72, size * 0.36))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(check)
    else:
        muted = muted_color(palette)
        painter.setPen(QPen(muted, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(box.adjusted(0.5, 0.5, -0.5, -0.5))
        font = painter.font()
        font.setBold(True)
        font.setPixelSize(round(size * 0.55))
        painter.setFont(font)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, str(number))
    painter.end()
    return pixmap
