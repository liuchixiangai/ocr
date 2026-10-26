"""The boxes drawn over the picture in the viewer.

Each box is an azure outline with a dark halo, so it shows on any scoreboard
(red boxes vanished on red LED digits), and a label pill with the box's state
glyph, name and latest reading. The pill is drawn at a fixed size on screen
(ItemIgnoresTransformations) and sits outside the box, below it by default:
scoreboard captions (HOME, GUEST, PERIOD) are usually above the digits.
ImageViewer places the pills with place_label() so that they cover no box.
"""

from typing import Callable
from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QPainter,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsSimpleTextItem,
)

import theme
from readings_view import draw_glyph, held_text, state_glyph, state_look
from text_detection_target import TextDetectionTarget, TextDetectionTargetWithResult

ResultState = TextDetectionTargetWithResult.ResultState

# Box display styles (the "Boxes:" combo box and the box_display_style setting)
BOX_STYLE_NONE = 0
BOX_STYLE_OUTLINE = 1
BOX_STYLE_NAMES = 2
BOX_STYLE_ALL = 3

# Colours over the video: they don't follow the theme, the picture is the
# background in both
BOX_STROKE = QColor("#1EA7FF")  # azure: rare on LED scoreboards
BOX_HALO = QColor(0, 0, 0, 170)
AUTO_CROP_STROKE = QColor("#3FB950")  # the area the OCR read (Auto Crop)
CHARACTER_STROKE = QColor("#6E7BFF")  # each character the OCR found
LABEL_BACKGROUND = QColor(11, 11, 11, 218)
LABEL_BORDER = QColor(255, 255, 255, 50)
LABEL_NAME = QColor("#D6D6D6")
LABEL_VALUE = QColor("#FFFFFF")
HANDLE_FILL = QColor("#FFFFFF")
HANDLE_BORDER = QColor(0, 0, 0, 200)

# Sizes on screen, in pixels (cosmetic: the same at every zoom)
BOX_STROKE_PX = 2
BOX_HALO_PX = 4
LABEL_GAP_PX = 3  # between the box and its label
LABEL_NAME_PX = 11
LABEL_VALUE_PX = 12
LABEL_PADDING_PX = 5
LABEL_MAX_NAME_PX = 150
LABEL_MAX_VALUE_PX = 110
HANDLE_PX = 8
# a press this close to a box's edge resizes it
EDGE_GRAB_PX = 5

# Label placements, relative to the box
LABEL_BELOW_LEFT = "below-left"
LABEL_BELOW_RIGHT = "below-right"
LABEL_ABOVE_LEFT = "above-left"
LABEL_ABOVE_RIGHT = "above-right"
LABEL_INSIDE = "inside"
# outside the box, in order of preference
LABEL_OUTSIDE_PLACEMENTS = (
    LABEL_BELOW_LEFT,
    LABEL_BELOW_RIGHT,
    LABEL_ABOVE_LEFT,
    LABEL_ABOVE_RIGHT,
)


def label_rect(placement: str, box: QRectF, size: QSizeF, gap: float) -> QRectF:
    """Where a label of size goes for a placement next to box (all in the same
    coordinates, e.g. the scene's)."""
    width, height = size.width(), size.height()
    if placement == LABEL_BELOW_LEFT:
        return QRectF(box.left(), box.bottom() + gap, width, height)
    if placement == LABEL_BELOW_RIGHT:
        return QRectF(box.right() - width, box.bottom() + gap, width, height)
    if placement == LABEL_ABOVE_LEFT:
        return QRectF(box.left(), box.top() - gap - height, width, height)
    if placement == LABEL_ABOVE_RIGHT:
        return QRectF(box.right() - width, box.top() - gap - height, width, height)
    # inside, at the bottom left
    return QRectF(box.left() + gap, box.bottom() - gap - height, width, height)


def place_label(
    box: QRectF,
    size: QSizeF,
    picture: QRectF,
    obstacles: list[QRectF],
    gap: float = 0.0,
) -> str:
    """The placement of a box's label: the first one outside the box (below
    it, then above it; aligned to its left, then to its right edge) that is
    within the picture and covers none of obstacles (the other boxes and the
    labels placed before). Without such room, inside the box, at its bottom
    left: the label then covers part of its own box, never another one."""
    for placement in LABEL_OUTSIDE_PLACEMENTS:
        rect = label_rect(placement, box, size, gap)
        if picture.isValid() and not picture.contains(rect):
            continue
        if any(rect.intersects(obstacle) for obstacle in obstacles):
            continue
        return placement
    return LABEL_INSIDE


def view_scale(item: QGraphicsItem) -> float:
    """The scale of the (first) view showing item: screen pixels per scene
    unit, 1.0 when it isn't shown."""
    scene = item.scene()
    if scene is not None:
        views = scene.views()
        if views:
            return max(1e-3, abs(views[0].transform().m11()))
    return 1.0


def _cosmetic_pen(color: QColor, width: float) -> QPen:
    pen = QPen(color, width)
    pen.setCosmetic(True)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    return pen


class ResizableRect(QGraphicsRectItem):
    selected_edge = None

    def __init__(self, x, y, width, height, onCenter=False):
        if onCenter:
            super().__init__(-width / 2, -height / 2, width, height)
        else:
            super().__init__(0, 0, width, height)
        self.setPos(x, y)
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setAcceptHoverEvents(True)
        self.setPen(_cosmetic_pen(BOX_STROKE, BOX_STROKE_PX))

    def getOriginalRect(self):
        """
        Retrieve the original rectangle adjusted by the pen width.

        Returns:
            QRectF: The adjusted rectangle.
        """
        # get the original rect adjusted by the pen width
        rect = self.rect()
        border = 0  # self.pen().width() / 2
        return QRectF(
            rect.x() + border,
            rect.y() + border,
            rect.width() - border * 2,
            rect.height() - border * 2,
        )

    def edgeMargin(self) -> float:
        """How close to an edge (in item units) a press grabs the edge: about
        EDGE_GRAB_PX on screen, at any zoom."""
        return EDGE_GRAB_PX / view_scale(self)

    def getEdges(self, pos):
        rect = self.rect()
        border = self.edgeMargin()

        edge = None
        if pos.x() < rect.x() + border:
            edge = edge | Qt.Edge.LeftEdge if edge else Qt.Edge.LeftEdge
        elif pos.x() > rect.right() - border:
            edge = edge | Qt.Edge.RightEdge if edge else Qt.Edge.RightEdge
        if pos.y() < rect.y() + border:
            edge = edge | Qt.Edge.TopEdge if edge else Qt.Edge.TopEdge
        elif pos.y() > rect.bottom() - border:
            edge = edge | Qt.Edge.BottomEdge if edge else Qt.Edge.BottomEdge

        return edge

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected_edge = self.getEdges(event.pos())
            self.offset = QPointF()
        else:
            self.selected_edge = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.selected_edge:
            mouse_delta = event.pos() - event.buttonDownPos(Qt.MouseButton.LeftButton)
            rect = self.rect()
            pos_delta = QPointF()
            # the smallest size
            border = max(1.0, self.pen().widthF())

            if self.selected_edge & Qt.Edge.LeftEdge:
                # ensure that the width is *always* positive, otherwise limit
                # both the delta position and width, based on the border size
                diff = min(mouse_delta.x() - self.offset.x(), rect.width() - border)
                if rect.x() < 0:
                    offset = diff / 2
                    self.offset.setX(self.offset.x() + offset)
                    pos_delta.setX(offset)
                    rect.adjust(offset, 0, -offset, 0)
                else:
                    pos_delta.setX(diff)
                    rect.setWidth(rect.width() - diff)
            elif self.selected_edge & Qt.Edge.RightEdge:
                if rect.x() < 0:
                    diff = max(mouse_delta.x() - self.offset.x(), border - rect.width())
                    offset = diff / 2
                    self.offset.setX(self.offset.x() + offset)
                    pos_delta.setX(offset)
                    rect.adjust(-offset, 0, offset, 0)
                else:
                    rect.setWidth(max(border, event.pos().x() - rect.x()))

            if self.selected_edge & Qt.Edge.TopEdge:
                # similarly to what done for LeftEdge, but for the height
                diff = min(mouse_delta.y() - self.offset.y(), rect.height() - border)
                if rect.y() < 0:
                    offset = diff / 2
                    self.offset.setY(self.offset.y() + offset)
                    pos_delta.setY(offset)
                    rect.adjust(0, offset, 0, -offset)
                else:
                    pos_delta.setY(diff)
                    rect.setHeight(rect.height() - diff)
            elif self.selected_edge & Qt.Edge.BottomEdge:
                if rect.y() < 0:
                    diff = max(
                        mouse_delta.y() - self.offset.y(), border - rect.height()
                    )
                    offset = diff / 2
                    self.offset.setY(self.offset.y() + offset)
                    pos_delta.setY(offset)
                    rect.adjust(0, -offset, 0, offset)
                else:
                    rect.setHeight(max(border, event.pos().y() - rect.y()))

            if rect != self.rect():
                self.setRect(rect)
                if pos_delta:
                    self.setPos(self.pos() + pos_delta)
        else:
            # use the default implementation for ItemIsMovable
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.selected_edge = None
        super().mouseReleaseEvent(event)

    def hoverMoveEvent(self, event):
        edges = self.getEdges(event.pos())
        if not edges:
            # self.unsetCursor()
            # show a moving cursor when the mouse is over the item
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        elif edges in (
            Qt.Edge.TopEdge | Qt.Edge.LeftEdge,
            Qt.Edge.BottomEdge | Qt.Edge.RightEdge,
        ):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif edges in (
            Qt.Edge.BottomEdge | Qt.Edge.LeftEdge,
            Qt.Edge.TopEdge | Qt.Edge.RightEdge,
        ):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif edges in (Qt.Edge.LeftEdge, Qt.Edge.RightEdge):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        super().hoverMoveEvent(event)


class MiniRect(ResizableRect):
    def __init__(self, x, y, width, height, boxChangedCallback, parent=None):
        super().__init__(x, y, width, height)
        self.setPen(QPen(QColor(255, 0, 0)))
        self.setBrush(QBrush(QColor(255, 0, 0, 50)))
        self.setParentItem(parent)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
        )
        self.boxChangedCallback = boxChangedCallback

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.setCursor(Qt.SizeAllCursor)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.unsetCursor()
        self.boxChangedCallback()


class BoxLabel(QGraphicsItem):
    """A box's label pill: the state glyph of its latest reading (✓ new,
    = unchanged, ! rejected, ∅ empty) on a coloured disc, the box's name and
    the reading. While the field's value is held (Hold value), an amber HELD
    chip and the held value instead. Fixed size on screen; its parent (the
    box) positions it with setPlacement()."""

    def __init__(self, name: str, parent: QGraphicsItem):
        super().__init__(parent)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setAcceptHoverEvents(False)
        self.setZValue(5)
        self.name = name
        self.state: ResultState | None = None
        self.value = ""
        # the value held for the field, or None
        self.held: str | None = None
        self.chipFont = QFont()
        self.chipFont.setPixelSize(LABEL_NAME_PX - 1)
        self.chipFont.setWeight(QFont.Weight.Bold)
        self.placement = LABEL_BELOW_LEFT
        self.nameFont = QFont()
        self.nameFont.setPixelSize(LABEL_NAME_PX)
        self.valueFont = QFont()
        self.valueFont.setPixelSize(LABEL_VALUE_PX)
        self.valueFont.setWeight(QFont.Weight.Bold)
        # digits of equal width, so a running clock doesn't jitter
        self.valueFont.setFeature(QFont.Tag("tnum"), 1)
        self._size = QSizeF()
        self._local = QRectF()
        self._layout()

    # --- content ---------------------------------------------------------------

    def setName(self, name: str):
        if name != self.name:
            self.name = name
            self._layout()

    def setReading(self, state: ResultState | None, value: str) -> bool:
        """Show a reading; returns True if the label's size changed (its
        placement may have to change too)."""
        value = "" if value is None else str(value)
        if state == ResultState.Empty:
            value = ""  # the ∅ glyph says it
        if state == self.state and value == self.value:
            return False
        self.state = state
        self.value = value
        old_size = self._size
        self._layout()
        return self._size != old_size

    def setHeld(self, value: str | None) -> bool:
        """Show a held value (None: the readings again); returns True if the
        label's size changed."""
        if value == self.held:
            return False
        self.held = value
        old_size = self._size
        self._layout()
        return self._size != old_size

    def text(self) -> str:
        """The label as text: glyph (or HELD), name and reading."""
        mark = held_text() if self.held is not None else state_glyph(self.state)
        parts = [mark, self.name, self.displayedValue()]
        return " ".join(part for part in parts if part)

    def displayedValue(self) -> str:
        if self.held is not None:
            return self.held
        if self.state == ResultState.FailedFilter and self.value:
            return f"‘{self.value}’"
        return self.value

    def _layout(self):
        self.prepareGeometryChange()
        name_metrics = QFontMetricsF(self.nameFont)
        value_metrics = QFontMetricsF(self.valueFont)
        self._nameText = name_metrics.elidedText(
            self.name, Qt.TextElideMode.ElideRight, LABEL_MAX_NAME_PX
        )
        self._valueText = value_metrics.elidedText(
            self.displayedValue(), Qt.TextElideMode.ElideRight, LABEL_MAX_VALUE_PX
        )
        height = round(max(name_metrics.height(), value_metrics.height()) + 5)
        self._disc = height - 6 if state_look(self.state) and self.held is None else 0
        # the HELD chip, in the disc's place
        self._chipText = held_text() if self.held is not None else ""
        self._chipWidth = (
            QFontMetricsF(self.chipFont).horizontalAdvance(self._chipText) + 8
            if self._chipText
            else 0
        )
        # measured here, not in paint(): readings arrive several times a second
        self._nameWidth = name_metrics.horizontalAdvance(self._nameText)
        width = LABEL_PADDING_PX * 2 + self._nameWidth
        if self._disc:
            width += self._disc + 4
        if self._chipWidth:
            width += self._chipWidth + 4
        if self._valueText:
            width += 6 + value_metrics.horizontalAdvance(self._valueText)
        self._size = QSizeF(round(width), height)
        self._updateLocalRect()

    # --- placement -------------------------------------------------------------

    def size(self) -> QSizeF:
        """The label's size on screen, in pixels."""
        return QSizeF(self._size)

    def setPlacement(self, placement: str, box: QRectF):
        """Put the label next to box (in the parent's coordinates)."""
        if placement in (LABEL_BELOW_LEFT, LABEL_INSIDE):
            anchor = box.bottomLeft()
        elif placement == LABEL_BELOW_RIGHT:
            anchor = box.bottomRight()
        elif placement == LABEL_ABOVE_LEFT:
            anchor = box.topLeft()
        else:
            anchor = box.topRight()
        if placement != self.placement:
            self.placement = placement
            self._updateLocalRect()
        if self.pos() != anchor:
            self.setPos(anchor)

    def _updateLocalRect(self):
        # the label relative to its anchor, in screen pixels
        self.prepareGeometryChange()
        self._local = label_rect(
            self.placement,
            QRectF(0, 0, 0, 0),
            self._size,
            LABEL_GAP_PX,
        )
        self.update()

    def boundingRect(self) -> QRectF:
        return self._local.adjusted(-1, -1, 1, 1)

    def paint(self, painter: QPainter, option, widget=None):
        rect = self._local
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        look = state_look(self.state)
        held = self.held is not None
        border = LABEL_BORDER
        if held:
            border = theme.overlay_color("paused")
        elif self.state == ResultState.FailedFilter:
            border = theme.overlay_color("rejected")
        painter.setPen(QPen(border, 1))
        painter.setBrush(LABEL_BACKGROUND)
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
        x = rect.left() + LABEL_PADDING_PX
        if held and self._chipWidth:
            chip_height = rect.height() - 6
            chip = QRectF(
                x, rect.center().y() - chip_height / 2, self._chipWidth, chip_height
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.overlay_color("paused"))
            painter.drawRoundedRect(chip, 3, 3)
            painter.setFont(self.chipFont)
            painter.setPen(QColor("#1B1F24"))
            painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, self._chipText)
            x = chip.right() + 4
        elif look and self._disc:
            color_name, glyph = look
            disc = QRectF(x, rect.center().y() - self._disc / 2, self._disc, self._disc)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.overlay_color(color_name))
            painter.drawEllipse(disc)
            draw_glyph(painter, disc, glyph, QColor("#1B1F24"))
            x = disc.right() + 4
        painter.setFont(self.nameFont)
        painter.setPen(LABEL_NAME)
        name_width = self._nameWidth
        painter.drawText(
            QRectF(x, rect.top(), name_width + 1, rect.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._nameText,
        )
        if self._valueText:
            painter.setFont(self.valueFont)
            painter.setPen(
                theme.overlay_color("rejected")
                if self.state == ResultState.FailedFilter and not held
                else LABEL_VALUE
            )
            painter.drawText(
                QRectF(
                    x + name_width + 6,
                    rect.top(),
                    rect.right() - LABEL_PADDING_PX - (x + name_width + 6),
                    rect.height(),
                ),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self._valueText,
            )
        painter.restore()


class ResizableRectWithNameTypeAndResult(ResizableRect):
    def __init__(
        self,
        detectionTarget: TextDetectionTarget,
        image_size,
        result="",
        onCenter=False,
        boxChangedCallback: Callable[[str, QRectF, list[QRectF]], None] = None,
        itemSelectedCallback=None,
        boxDisplayStyle: int = BOX_STYLE_OUTLINE,
    ):
        super().__init__(
            detectionTarget.x(),
            detectionTarget.y(),
            detectionTarget.width(),
            detectionTarget.height(),
            onCenter,
        )
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setAcceptHoverEvents(True)
        self.name = detectionTarget.name
        self.result = result
        self.boxChangedCallback = boxChangedCallback
        self.itemSelectedCallback = itemSelectedCallback
        self.selected = False
        # hidden whatever the style (e.g. while four-corner points are picked)
        self.suppressed = False
        self.boxDisplayStyle = boxDisplayStyle
        # the area the OCR read and the characters it found ("All" style)
        self.effectiveRect = None
        self.effectiveRectUsed = False
        self.extraBoxes: list[QGraphicsRectItem] = []
        self.extraBoxesUsed = 0
        self.label = BoxLabel(self.name, self)

        # Mini-rect related attributes
        self.mini_rects = [
            MiniRect(
                r.x(),
                r.y(),
                r.width(),
                r.height(),
                self.sendBoxChangedCallback,
                parent=self,
            )
            for r in detectionTarget.mini_rects
        ]
        self.mini_rect_mode = False
        self.add_button = None
        self.setupAddButton()

        self.cornerBoxes: list[QGraphicsRectItem] = []
        self.setupCornerBoxes()
        self.updateCornerBoxes()
        self.label.setPlacement(LABEL_BELOW_LEFT, self.rect())
        self.updateVisibility()

    def setupAddButton(self):
        self.add_button = QGraphicsRectItem(0, 0, 25, 30, parent=self)
        self.add_button.setBrush(QBrush(QColor(0, 255, 0)))
        self.add_button.setPen(QPen(Qt.black))
        self.add_button.setPos(self.rect().topLeft() + QPointF(2, 2))
        self.add_button.setZValue(4)
        self.add_button.setVisible(False)

        # Add a "+" text to the button
        text = QGraphicsSimpleTextItem("+", self.add_button)
        text.setPos(5, 0)
        text.setFont(QFont("Arial", 20))

    def setMiniRectMode(self, enabled):
        self.mini_rect_mode = enabled
        self.add_button.setVisible(enabled)

    def setupCornerBoxes(self):
        # the selected box's handles: small white squares, the same size at
        # every zoom
        half = HANDLE_PX / 2
        for _ in range(4):
            cornerBox = QGraphicsRectItem(-half, -half, HANDLE_PX, HANDLE_PX, self)
            cornerBox.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            cornerBox.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            cornerBox.setBrush(QBrush(HANDLE_FILL))
            cornerBox.setPen(QPen(HANDLE_BORDER, 1))
            cornerBox.setZValue(3)
            cornerBox.setVisible(False)
            self.cornerBoxes.append(cornerBox)

    def updateCornerBoxes(self):
        rect = self.rect()
        self.cornerBoxes[0].setPos(rect.topLeft())
        self.cornerBoxes[1].setPos(rect.topRight())
        self.cornerBoxes[2].setPos(rect.bottomLeft())
        self.cornerBoxes[3].setPos(rect.bottomRight())

    # --- drawing -----------------------------------------------------------------

    def paint(self, painter: QPainter, option, widget=None):
        rect = self.rect()
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # a dark halo under the stroke: visible on light and dark pictures
        painter.setPen(_cosmetic_pen(BOX_HALO, BOX_HALO_PX))
        painter.drawRect(rect)
        if self.selected:
            highlight = QGuiApplication.palette().color(QPalette.ColorRole.Highlight)
            fill = QColor(highlight)
            fill.setAlpha(40)
            painter.setBrush(fill)
            painter.setPen(_cosmetic_pen(highlight, BOX_STROKE_PX))
        else:
            painter.setPen(_cosmetic_pen(BOX_STROKE, BOX_STROKE_PX))
        painter.drawRect(rect)
        painter.restore()

    def labelRectInScene(self, placement: str, scale: float) -> QRectF:
        """Where the label goes for a placement, in scene coordinates, at the
        view scale."""
        size = self.label.size()
        return label_rect(
            placement,
            self.sceneBoxRect(),
            QSizeF(size.width() / scale, size.height() / scale),
            LABEL_GAP_PX / scale,
        )

    def sceneBoxRect(self) -> QRectF:
        return self.mapRectToScene(self.rect())

    def setLabelPlacement(self, placement: str):
        self.label.setPlacement(placement, self.rect())

    def labelShown(self) -> bool:
        return self.label.isVisibleTo(self) and self.isVisible()

    # --- display style, selection --------------------------------------------------

    def updateVisibility(self):
        """Show the box, its label, its handles and the OCR's details as the
        display style, the selection and the suppression say."""
        style = self.boxDisplayStyle
        visible = not self.suppressed and (style != BOX_STYLE_NONE or self.selected)
        self.setVisible(visible)
        self.label.setVisible(style >= BOX_STYLE_NAMES or self.selected)
        for cornerBox in self.cornerBoxes:
            cornerBox.setVisible(self.selected)
        details = style == BOX_STYLE_ALL
        if self.effectiveRect is not None:
            self.effectiveRect.setVisible(details and self.effectiveRectUsed)
        for index, extraBox in enumerate(self.extraBoxes):
            extraBox.setVisible(details and index < self.extraBoxesUsed)

    def setBoxDisplayStyle(self, boxDisplayStyle: int):
        self.boxDisplayStyle = boxDisplayStyle
        self.updateVisibility()

    def setSuppressed(self, suppressed: bool):
        self.suppressed = suppressed
        self.updateVisibility()

    def setRect(self, *args, **kwargs):
        super().setRect(*args, **kwargs)
        self.updateCornerBoxes()
        self.label.setPlacement(self.label.placement, self.rect())

    def setSelected(self, selected):
        super().setSelected(selected)
        if selected != self.selected:
            self.selected = selected
            self.update()
        self.updateVisibility()

    def getRect(self):
        return self.getOriginalRect()

    def setHeldValue(self, value: str | None) -> bool:
        """The label shows the value held for the field (None: its readings).
        Returns True if the label's size changed."""
        return self.label.setHeld(value)

    def updateResult(self, targetWithResult: TextDetectionTargetWithResult) -> bool:
        """Show a reading: in the label, and with the "All" style the area the
        OCR read and the characters it found. Returns True if the label's size
        changed."""
        self.result = targetWithResult.result
        resized = self.label.setReading(
            targetWithResult.result_state, targetWithResult.result
        )
        if self.boxDisplayStyle == BOX_STYLE_ALL:
            self.updateDetails(targetWithResult)
        return resized

    def updateDetails(self, targetWithResult: TextDetectionTargetWithResult):
        """The area the OCR read (green) and each character it found (blue), as
        thin lines: the items are reused from reading to reading. The OCR
        gives them relative to the box's top-left corner, which isn't this
        item's origin once the box was moved other than by dragging it (e.g.
        with the arrow keys, drawn again, or with the crop)."""
        corner = self.rect().topLeft()
        self.effectiveRectUsed = targetWithResult.effectiveRect is not None
        if self.effectiveRectUsed:
            if self.effectiveRect is None:
                self.effectiveRect = self._detailRect(AUTO_CROP_STROKE, -1)
            self.effectiveRect.setRect(
                QRectF(targetWithResult.effectiveRect).translated(corner)
            )
        extras = targetWithResult.extras or {}
        rects = [
            QRectF(box["x"], box["y"], box["w"], box["h"]).translated(corner)
            for box in extras.get("boxes") or []
            if all(key in box for key in ("x", "y", "w", "h"))
        ]
        while len(self.extraBoxes) < len(rects):
            self.extraBoxes.append(self._detailRect(CHARACTER_STROKE, -2))
        for extraBox, rect in zip(self.extraBoxes, rects):
            extraBox.setRect(rect)
        self.extraBoxesUsed = len(rects)
        self.updateVisibility()

    def _detailRect(self, color: QColor, z: float) -> QGraphicsRectItem:
        item = QGraphicsRectItem(self)
        # ignore any mouse events on it
        item.setAcceptHoverEvents(False)
        item.setAcceptDrops(False)
        item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        item.setBrush(Qt.BrushStyle.NoBrush)
        item.setPen(_cosmetic_pen(color, 1))
        item.setZValue(z)
        return item

    def startCreateMiniRect(self, rect: QRectF):
        new_mini_rect = MiniRect(
            rect.x(),
            rect.y(),
            rect.width(),
            rect.height(),
            self.sendBoxChangedCallback,
            parent=self,
        )
        self.mini_rects.append(new_mini_rect)

    def clearMiniRects(self):
        for mini_rect in self.mini_rects:
            self.scene().removeItem(mini_rect)
        self.mini_rects.clear()

    def getMiniRects(self):
        return [rect.rect() for rect in self.mini_rects]

    def sendBoxChangedCallback(self):
        origRect = self.getRect()
        boxRect = QRectF(
            origRect.x() + self.x(),
            origRect.y() + self.y(),
            origRect.width(),
            origRect.height(),
        )
        self.boxChangedCallback(
            self.name,
            boxRect,
            [
                QRectF(r.x(), r.y(), r.rect().width(), r.rect().height())
                for r in self.mini_rects
            ],
        )

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.sendBoxChangedCallback()

    def mousePressEvent(self, event):
        if self.mini_rect_mode:
            if self.add_button.contains(event.pos()):
                self.startCreateMiniRect(
                    QRectF(
                        10,
                        10,
                        self.getRect().height() * 0.75,
                        self.getRect().width() / 3,
                    )
                )
            else:
                # deselect all mini rects
                for mini_rect in self.mini_rects:
                    mini_rect.setSelected(False)
                super().mousePressEvent(event)
        else:
            super().mousePressEvent(event)
        self.itemSelectedCallback(self.name)

    def mouseMoveEvent(self, event):
        return super().mouseMoveEvent(event)

    def hoverMoveEvent(self, event):
        super().hoverMoveEvent(event)
        if self.mini_rect_mode:
            if self.add_button.contains(event.pos()):
                self.add_button.setBrush(QBrush(QColor(0, 255, 0, 128)))
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.add_button.setBrush(QBrush(QColor(0, 255, 0)))
