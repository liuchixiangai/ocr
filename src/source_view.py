import math
from dataclasses import dataclass

from PySide6.QtCore import (
    QCoreApplication,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSizeF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QContextMenuEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QTransform,
)
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QLabel,
)

from camera_info import CameraInfo
from defaults import is_chinese_text_name
from camera_view import (
    OSD_MARGIN_PX,
    OSD_STYLE,
    CameraView,
    compute_four_corner_homography,
)
from storage import (
    TextDetectionTargetMemoryStorage,
    fetch_data,
    remove_data,
    store_data,
    subscribe_to_data,
    unsubscribe_from_data,
)
from text_detection_target import TextDetectionTarget, TextDetectionTargetWithResult
from sc_logging import logger
from resizable_rect import (
    BOX_HALO,
    BOX_HALO_PX,
    BOX_STROKE,
    ResizableRectWithNameTypeAndResult,
    place_label,
)

# Zoom steps (Ctrl+scroll, Ctrl+= and Ctrl+-) and limits, in screen pixels per
# picture pixel; a picture fitted to the viewer may be outside the limits.
WHEEL_ZOOM_FACTOR = 1.05
KEY_ZOOM_FACTOR = 1.25
MIN_ZOOM = 0.33
MAX_ZOOM = 4.0
# The arrow keys move the selected box by this many picture pixels (with
# Shift: NUDGE_LARGE_PX).
NUDGE_PX = 1
NUDGE_LARGE_PX = 10
# A four-corner point on screen, in pixels
CORNER_MARKER_PX = 10
CORNER_COLOR = QColor("#FF3B30")

# A drag at least this many screen pixels wide and tall draws a box (or a
# crop); a smaller one is a click.
DRAW_MIN_PX = 4
# What a drag on the picture draws
DRAW_PLACE = "place"  # the box of the selected field that has none
DRAW_REDRAW = "redraw"  # the selected box again (Alt+drag)
DRAW_CROP = "crop"  # the part of the picture to keep (Drag to crop)
# Around the crop being dragged: the part of the picture that is cut off
CROP_SHADE = QColor(0, 0, 0, 140)

_NUDGE_KEYS = {
    Qt.Key.Key_Left: (-1, 0),
    Qt.Key.Key_Right: (1, 0),
    Qt.Key.Key_Up: (0, -1),
    Qt.Key.Key_Down: (0, 1),
}


def sort_points_clockwise(points: list[QGraphicsItem]) -> list[QGraphicsItem]:
    # Calculate the center point
    center_x = sum(point.x() for point in points) / len(points)
    center_y = sum(point.y() for point in points) / len(points)
    center = QPointF(center_x, center_y)

    # Define a function to calculate the angle between a point and the center
    def angle_from_center(point):
        return math.atan2(point.y() - center.y(), point.x() - center.x())

    # Sort the points based on their angles
    sorted_points = sorted(points, key=angle_from_center)

    # Rotate the list so that the top-left point comes first
    top_left = min(sorted_points, key=lambda p: p.x() + p.y())
    while sorted_points[0] != top_left:
        sorted_points = sorted_points[1:] + [sorted_points[0]]

    return sorted_points


def clamped_point(point: QPointF, bounds: QRectF) -> QPointF:
    """point, moved into bounds (when bounds is valid)."""
    if not bounds.isValid():
        return QPointF(point)
    return QPointF(
        min(max(point.x(), bounds.left()), bounds.right()),
        min(max(point.y(), bounds.top()), bounds.bottom()),
    )


def drawn_rect(start: QPointF, end: QPointF, bounds: QRectF) -> QRectF:
    """The rectangle dragged from start to end (in picture pixels), within
    bounds, its corners on whole pixels."""
    start = clamped_point(start, bounds)
    end = clamped_point(end, bounds)
    return QRectF(
        QPointF(round(start.x()), round(start.y())),
        QPointF(round(end.x()), round(end.y())),
    ).normalized()


def centered_rect(
    center: QPointF, width: float, height: float, bounds: QRectF
) -> QRectF:
    """A width x height rectangle centred on center, moved into bounds (and
    made smaller to fit it, if needed), on whole pixels."""
    if bounds.isValid():
        width = min(width, bounds.width())
        height = min(height, bounds.height())
    rect = QRectF(
        round(center.x() - width / 2), round(center.y() - height / 2), width, height
    )
    return nudged_rect(rect, 0, 0, bounds)


class RubberBand(QGraphicsRectItem):
    """The rectangle being dragged: an azure dashed outline with a dark halo
    (visible on any picture), lightly filled. For a crop, the part of the
    picture outside it is shaded: that part is cut off."""

    def __init__(self, picture: QRectF, shade: bool):
        super().__init__()
        self.picture = QRectF(picture)
        self.shade = shade
        self.setZValue(30)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def boundingRect(self) -> QRectF:
        if self.shade:
            return self.picture.united(super().boundingRect())
        return super().boundingRect()

    def paint(self, painter: QPainter, option, widget=None):
        rect = self.rect()
        painter.save()
        if self.shade:
            outside = QPainterPath()
            outside.addRect(self.picture)
            inside = QPainterPath()
            inside.addRect(rect)
            painter.fillPath(outside.subtracted(inside), CROP_SHADE)
        halo = QPen(BOX_HALO, BOX_HALO_PX)
        halo.setCosmetic(True)
        painter.setPen(halo)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)
        fill = QColor(BOX_STROKE)
        fill.setAlpha(0 if self.shade else 45)
        stroke = QPen(BOX_STROKE, 2, Qt.PenStyle.DashLine)
        stroke.setCosmetic(True)
        painter.setPen(stroke)
        painter.setBrush(fill)
        painter.drawRect(rect)
        painter.restore()


@dataclass
class Drawing:
    """A drag on the picture that draws a rectangle (DRAW_PLACE, ...)."""

    mode: str
    # the field whose box it draws (None for a crop)
    name: str | None
    # where the drag started: in the viewport and on the picture
    view_origin: QPoint
    scene_origin: QPointF
    band: RubberBand
    # started by the second press of a double-click: without a drag, it is a
    # double-click (DRAW_PLACE: the box there)
    double_click: bool = False


def nudged_rect(rect: QRectF, dx: float, dy: float, bounds: QRectF) -> QRectF:
    """rect moved by (dx, dy), kept inside bounds when it fits there (a box
    larger than the picture stays where it is on that axis)."""
    moved = rect.translated(dx, dy)
    if bounds.isValid():
        if rect.width() <= bounds.width():
            moved.moveLeft(
                min(max(moved.left(), bounds.left()), bounds.right() - rect.width())
            )
        else:
            moved.moveLeft(rect.left())
        if rect.height() <= bounds.height():
            moved.moveTop(
                min(max(moved.top(), bounds.top()), bounds.bottom() - rect.height())
            )
        else:
            moved.moveTop(rect.top())
    return moved


class ImageViewer(CameraView):
    # the four-corner picking started, got a corner or ended, or the
    # correction changed
    four_corner_changed = Signal()
    # a box was drawn for a field: (name, rectangle in picture pixels). A
    # field without a box gets one; the selected box (Alt+drag) is redrawn.
    box_drawn = Signal(str, QRectF)
    # a double-click on the picture: put the field's box there (its centre)
    place_requested = Signal(str, QPointF)
    # a right-click on a box (or the menu key): its name and where to show
    # its menu (global position)
    box_menu_requested = Signal(str, QPoint)
    # Drag to crop: the part of the picture to keep, in pixels of the whole
    # (rotated, uncropped) frame, and that frame's size
    crop_drawn = Signal(QRectF, QSizeF)
    # the crop picking (Drag to crop) started or ended
    crop_selection_changed = Signal()

    def __init__(
        self,
        camera_index: CameraInfo,
        fourCornersAppliedCallback: callable,
        detectionTargetsStorage: TextDetectionTargetMemoryStorage | None,
        itemSelectedCallback: callable,
        start: bool = True,
    ):
        # the capture starts once this viewer is connected to the thread (and
        # with start=False, when the caller calls startCapture())
        super().__init__(camera_index, detectionTargetsStorage, start=False)
        self.setMouseTracking(True)
        # the arrow keys move the selected box, Esc deselects it
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.fourCornerSelectionMode = False
        # Drag to crop: a drag on the whole picture sets the crop
        self.cropSelectionMode = False
        # the selected field when it has no box (a drag on the picture draws
        # its box), else None
        self.placementField: str | None = None
        # the drag that draws a rectangle, None when none is
        self.drawing: Drawing | None = None
        # where the mouse was last in the viewport (for the cursor)
        self.lastMousePosition: QPoint | None = None
        # "Drag over the numbers to place Home Fouls", over the picture: the
        # same size at every zoom, at the top
        self.placementHint = QLabel(self)
        self.placementHint.setObjectName("label_placementHint")
        self.placementHint.setStyleSheet(OSD_STYLE)
        self.placementHint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.placementHint.setVisible(False)
        self.fourCorners = []
        self.fourCornerPolygon = None
        self.fourCornersAppliedCallback = fourCornersAppliedCallback
        self.itemSelectedCallback = itemSelectedCallback
        # the name of the selected box (None: none is)
        self.selectedBoxName = None
        # {field name: value} of the fields whose value is held (Hold value):
        # their labels say HELD and show that value
        self.heldValues: dict[str, str] = {}
        self.first_frame_received_signal.connect(self.detectionTargetsChanged)
        self.first_frame_received_signal.connect(self.updatePlacementHint)
        self.detectionTargetsStorage.data_changed.connect(self.detectionTargetsChanged)
        self.timerThread.ocr_result_signal.connect(self.ocrResult)
        self.zoom_changed.connect(self.layoutLabels)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, False)
        stored_corners = fetch_data("scoresight.json", "four_corners")
        if stored_corners:
            if self.setFourCorners(stored_corners):
                self.fourCornersAppliedCallback(self.fourCorners)
            else:
                # e.g. saved by an older version that didn't validate them
                remove_data("scoresight.json", "four_corners")
        self._isScaling = False
        self._isPanning = False
        self._lastMousePosition = QPointF()

        self.boxDisplayStyleSetting: int = fetch_data(
            "scoresight.json", "box_display_style", 3
        )
        subscribe_to_data("scoresight.json", "box_display_style", self.boxDisplayStyle)
        if start:
            self.startCapture()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.placePlacementHint()
        if self._isScaling or not self.fitted:
            # scrollbars appearing while zooming, or a banner over the viewer
            # while zoomed in: keep the zoom
            return
        self.fitPicture()
        self.detectionTargetsChanged()

    def boxItems(self) -> list[ResizableRectWithNameTypeAndResult]:
        return [
            item
            for item in self.scene.items(Qt.SortOrder.AscendingOrder)
            if isinstance(item, ResizableRectWithNameTypeAndResult)
        ]

    def boxDisplayStyle(self, state: int):
        self.boxDisplayStyleSetting = state
        for item in self.boxItems():
            item.setBoxDisplayStyle(state)
        self.layoutLabels()

    def toggleStabilization(self, state):
        if self.firstFrameReceived and self.timerThread:
            self.timerThread.toggleStabilization(state)

    def toggleBinary(self):
        if self.firstFrameReceived and self.timerThread:
            self.timerThread.show_binary = not self.timerThread.show_binary

    # --- four-corner correction -----------------------------------------------------

    def hasFourCornerCorrection(self) -> bool:
        """Whether the picture is corrected with four corners."""
        return self.timerThread is not None and self.timerThread.homography is not None

    def fourCornersPicked(self) -> int:
        """How many corners were clicked while picking."""
        return len(self.fourCorners) if self.fourCornerSelectionMode else 0

    def startFourCornerSelection(self):
        """Pick new corners. The picture is shown without the current
        correction (the corners are clicked on it), but the correction stays in
        effect, also for the OCR, until the 4th valid click replaces it.
        cancelFourCornerSelection() (Esc) keeps it."""
        self.cancelCropSelection()
        self.cancelDrawing()
        self.clearFourCornerItems()
        self.show_error_text(None)
        self.fourCornerSelectionMode = True
        # the boxes sit on the corrected picture: hidden while it isn't shown
        self.setBoxesSuppressed(True)
        if self.timerThread is not None:
            self.timerThread.preview_uncorrected = True
            self.timerThread.requestPreview()
        self.updatePlacementHint()
        self.updateCursor()
        self.four_corner_changed.emit()

    def cancelFourCornerSelection(self):
        """Stop picking corners; the correction (if any) stays as it was."""
        if not self.fourCornerSelectionMode:
            return
        self.fourCornerSelectionMode = False
        self.clearFourCornerItems()
        self.show_error_text(None)
        self.endUncorrectedPreview()
        self.updatePlacementHint()
        self.updateCursor()
        self.four_corner_changed.emit()

    def removeFourCornerCorrection(self):
        """Show the picture without perspective correction."""
        self.cancelFourCornerSelection()
        self.setFourCorners(None)
        remove_data("scoresight.json", "four_corners")
        if self.timerThread is not None:
            self.timerThread.requestPreview()
        self.four_corner_changed.emit()

    def endUncorrectedPreview(self):
        self.setBoxesSuppressed(False)
        if self.timerThread is not None:
            self.timerThread.preview_uncorrected = False
            self.timerThread.requestPreview()

    def setFourCorners(self, corners) -> bool:
        self.clearFourCornerItems()
        return self.setFourCornersForHomography(corners)

    def clearFourCornerItems(self):
        for corner in self.fourCorners:
            self.scene.removeItem(corner)
        if self.fourCornerPolygon is not None:
            self.scene.removeItem(self.fourCornerPolygon)
        self.fourCorners = []
        self.fourCornerPolygon = None

    def addFourCornerPoint(self, scene_pos: QPointF):
        """A click while picking: add a corner, and with the 4th one replace the
        correction (or, if the 4 corners don't make a usable quad, start the
        picking over and keep the correction)."""
        half = CORNER_MARKER_PX / 2
        point = QGraphicsEllipseItem(-half, -half, CORNER_MARKER_PX, CORNER_MARKER_PX)
        point.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        point.setPos(scene_pos)
        point.setBrush(QBrush(CORNER_COLOR))
        point.setPen(QPen(QColor("white"), 1.5))
        point.setZValue(10)
        self.scene.addItem(point)
        self.fourCorners.append(point)
        self.fourCorners = sort_points_clockwise(self.fourCorners)
        if len(self.fourCorners) >= 2:
            if not self.fourCornerPolygon:
                self.fourCornerPolygon = QGraphicsPolygonItem()
                pen = QPen(CORNER_COLOR, 2)
                pen.setCosmetic(True)
                self.fourCornerPolygon.setPen(pen)
                self.fourCornerPolygon.setZValue(9)
                self.scene.addItem(self.fourCornerPolygon)
            self.fourCornerPolygon.setPolygon(
                QPolygonF([corner.pos() for corner in self.fourCorners])
            )
        if len(self.fourCorners) == 4:
            corners = [(corner.x(), corner.y()) for corner in self.fourCorners]
            if compute_four_corner_homography(corners) is None:
                # e.g. the same point clicked twice: start the picking over,
                # the previous correction stays
                self.clearFourCornerItems()
                # a message only: the source is fine, so the view tools stay
                # enabled (error_event would disable them)
                self.show_error_text(
                    QCoreApplication.translate(
                        "ImageViewer",
                        "Invalid four-corner selection: click the four corners "
                        "of the scoreboard",
                    )
                )
            else:
                self.show_error_text(None)
                self.fourCornerSelectionMode = False
                self.clearFourCornerItems()
                self.setFourCornersForHomography(corners)
                store_data("scoresight.json", "four_corners", corners)
                self.endUncorrectedPreview()
                self.updatePlacementHint()
                self.updateCursor()
                self.fourCornersAppliedCallback(corners)
        self.four_corner_changed.emit()

    # --- Drag to crop -----------------------------------------------------------------

    def startCropSelection(self):
        """Drag to crop: the preview shows the whole (rotated) frame, without
        the crop, the boxes are hidden, and a drag on it sets the crop. The
        current crop stays in effect, also for the OCR, until then;
        cancelCropSelection() (Esc) keeps it."""
        self.cancelFourCornerSelection()
        self.cancelDrawing()
        self.cropSelectionMode = True
        # the boxes sit on the cropped picture
        self.setBoxesSuppressed(True)
        if self.timerThread is not None:
            self.timerThread.preview_uncropped = True
            self.timerThread.requestPreview()
        self.updatePlacementHint()
        self.updateCursor()
        self.crop_selection_changed.emit()

    def cancelCropSelection(self):
        """Stop the crop picking; the crop stays as it was."""
        if not self.cropSelectionMode:
            return
        self.cancelDrawing()
        self.cropSelectionMode = False
        self.setBoxesSuppressed(False)
        if self.timerThread is not None:
            self.timerThread.preview_uncropped = False
            self.timerThread.requestPreview()
        self.updatePlacementHint()
        self.updateCursor()
        self.crop_selection_changed.emit()

    def uncroppedFrameSize(self) -> QSizeF | None:
        """The size of the whole frame after the rotation (the crop picking's
        picture), or None before a frame was read."""
        thread = self.timerThread
        if thread is None:
            return None
        width, height = thread.frame_size
        if width <= 0 or height <= 0:
            return None
        if thread.crop.rotation in (90, 270):
            width, height = height, width
        return QSizeF(width, height)

    def uncroppedPictureShown(self) -> bool:
        """Whether the picture shown is the whole (uncropped) frame: right
        after the crop picking started, the cropped one may still be shown."""
        size = self.uncroppedFrameSize()
        return (
            size is not None
            and self.hasPicture()
            and self.scene.sceneRect().size() == size
        )

    # --- boxes ------------------------------------------------------------------------

    def hasPicture(self) -> bool:
        return self.firstFrameReceived and self.scenePixmapItem is not None

    def pictureRect(self) -> QRectF:
        """The picture, in its own pixels (the scene's coordinates)."""
        return self.scene.sceneRect() if self.scenePixmapItem is not None else QRectF()

    def visiblePictureRect(self) -> QRectF:
        """The part of the picture shown in the viewer (zoomed in, a part of
        it), in picture pixels."""
        shown = self.mapToScene(self.viewport().rect()).boundingRect()
        picture = self.pictureRect()
        return shown.intersected(picture) if picture.isValid() else shown

    def setBoxesSuppressed(self, suppressed: bool):
        for item in self.boxItems():
            item.setSuppressed(suppressed)

    def detectionTargetsChanged(self):
        if not self.firstFrameReceived:
            return

        # get the detection targets from the storage
        detectionTargets: list[TextDetectionTarget] = (
            self.detectionTargetsStorage.get_data()
        )
        done_targets = []
        # add the boxes to the scene
        for detectionTarget in detectionTargets:
            done_targets.append(detectionTarget.name)
            if detectionTarget.settings["templatefield"]:
                # do not show the template fields
                continue
            boxFound = self.findBox(detectionTarget.name)
            if boxFound is None:
                boxFound = ResizableRectWithNameTypeAndResult(
                    detectionTarget,
                    # image size
                    self.scene.sceneRect().width(),
                    onCenter=False,
                    boxChangedCallback=self.boxChanged,
                    itemSelectedCallback=self.itemSelectedCallback,
                    boxDisplayStyle=self.boxDisplayStyleSetting,
                )
                boxFound.setSuppressed(
                    self.fourCornerSelectionMode or self.cropSelectionMode
                )
                boxFound.setSelected(detectionTarget.name == self.selectedBoxName)
                boxFound.setHeldValue(self.heldValues.get(detectionTarget.name))
                self.scene.addItem(boxFound)
            else:
                boxFound.setRect(
                    detectionTarget.x() - boxFound.x(),
                    detectionTarget.y() - boxFound.y(),
                    detectionTarget.width(),
                    detectionTarget.height(),
                )
            boxFound.setMiniRectMode(detectionTarget.settings["composite_box"])

        # remove the boxes that are not in the storage
        for item in self.boxItems():
            if item.name not in done_targets:
                self.scene.removeItem(item)
        self.layoutLabels()

    def layoutLabels(self, *_):
        """Place each shown box's label pill: below the box (else above it,
        else inside it), where it covers no other box and no other label. The
        labels have a fixed size on screen, so this depends on the zoom."""
        items = [item for item in self.boxItems() if item.isVisible()]
        if not items:
            return
        scale = self.zoomLevel() or 1.0
        picture = (
            self.scene.sceneRect() if self.scenePixmapItem is not None else QRectF()
        )
        boxes = {id(item): item.sceneBoxRect() for item in items}
        placed = []
        # top to bottom, left to right: the same order every time
        for item in sorted(
            items, key=lambda i: (boxes[id(i)].top(), boxes[id(i)].left())
        ):
            if not item.label.isVisibleTo(item):
                continue
            others = [rect for key, rect in boxes.items() if key != id(item)]
            size = item.label.size()
            placement = place_label(
                boxes[id(item)],
                QSizeF(size.width() / scale, size.height() / scale),
                picture,
                others + placed,
                3.0 / scale,
            )
            item.setLabelPlacement(placement)
            placed.append(item.labelRectInScene(placement, scale))

    def boxChanged(self, name: str, rect: QRectF, mini_rects: list[QRectF]):
        # update the detection target in the storage
        detectionTargets: list[TextDetectionTarget] = (
            self.detectionTargetsStorage.get_data()
        )
        for detectionTarget in detectionTargets:
            if detectionTarget.name == name:
                detectionTarget.setX(rect.x())
                detectionTarget.setY(rect.y())
                detectionTarget.setWidth(rect.width())
                detectionTarget.setHeight(rect.height())
                self.detectionTargetsStorage.edit_item(
                    detectionTarget.name, detectionTarget
                )
                detectionTarget.mini_rects = mini_rects
                break

    def nudgeSelectedBox(self, dx: float, dy: float) -> bool:
        """Move the selected box by (dx, dy) picture pixels, within the
        picture. Returns False if no box is selected."""
        if self.selectedBoxName is None or self.detectionTargetsStorage is None:
            return False
        target = self.detectionTargetsStorage.find_item_by_name(self.selectedBoxName)
        if target is None or target.settings.get("templatefield"):
            return False
        bounds = (
            self.scene.sceneRect() if self.scenePixmapItem is not None else QRectF()
        )
        moved = nudged_rect(QRectF(target), dx, dy, bounds)
        target.moveTo(moved.topLeft())
        self.detectionTargetsStorage.edit_item(target.name, target)
        return True

    def findBox(self, name):
        # find the box with the name
        for item in self.boxItems():
            if item.name == name:
                return item
        return None

    def removeBox(self, name):
        # find the box with the name
        item = self.findBox(name)
        if item:
            self.scene.removeItem(item)

    def boxAt(self, view_position: QPoint) -> ResizableRectWithNameTypeAndResult | None:
        """The box a click at this point of the viewport goes to: the top one
        there (its label and handles let clicks through)."""
        for item in self.items(view_position):
            if item.acceptedMouseButtons() == Qt.MouseButton.NoButton:
                continue
            box = item
            while box is not None and not isinstance(
                box, ResizableRectWithNameTypeAndResult
            ):
                box = box.parentItem()
            if box is not None and box.isVisible():
                return box
        return None

    def selectBox(self, name):
        # deselect all the boxes and select the one with the name
        self.selectedBoxName = name
        for item in self.boxItems():
            item.setSelected(item.name == name)
        self.layoutLabels()
        drawing = self.drawing
        if drawing is not None and drawing.mode == DRAW_REDRAW and drawing.name != name:
            self.cancelDrawing()

    def selectedBoxTarget(self) -> TextDetectionTarget | None:
        """The selected field's box, if it has one (not a template field)."""
        if self.selectedBoxName is None or self.detectionTargetsStorage is None:
            return None
        target = self.detectionTargetsStorage.find_item_by_name(self.selectedBoxName)
        if target is None or (target.settings or {}).get("templatefield"):
            return None
        return target

    # --- drawing a box on the picture ---------------------------------------------

    def setPlacementField(self, name: str | None):
        """The selected field when it has no box (None otherwise): a drag on
        the picture draws its box, a double-click puts it there. The cursor is
        a crosshair and a hint says so."""
        if name == self.placementField:
            return
        if self.drawing is not None and self.drawing.mode == DRAW_PLACE:
            self.cancelDrawing()
        self.placementField = name
        self.updatePlacementHint()
        self.updateCursor()

    def updatePlacementHint(self, *_):
        """Show the hint over the picture while a drag would place a box (and
        set its text again after a language change)."""
        hint = self.placementHint
        show = (
            self.placementField is not None
            and self.hasPicture()
            and not self.fourCornerSelectionMode
            and not self.cropSelectionMode
        )
        if show:
            if is_chinese_text_name(self.placementField):
                hint.setText(f"拖选汉字以放置 {self.placementField}")
            else:
                hint.setText(
                    QCoreApplication.translate(
                        "ImageViewer", "Drag over the numbers to place {name}"
                    ).format(name=self.placementField)
                )
            self.placePlacementHint()
        hint.setVisible(show)

    def placePlacementHint(self):
        """At the top of the viewer, in the middle (under the statistics
        overlay when they would overlap)."""
        hint = self.placementHint
        hint.adjustSize()
        viewport = self.viewport().geometry()
        x = viewport.left() + max(OSD_MARGIN_PX, (viewport.width() - hint.width()) // 2)
        y = viewport.top() + OSD_MARGIN_PX
        if self.osd.isVisible() and self.osd.geometry().intersects(
            QRect(x, y, hint.width(), hint.height())
        ):
            y = self.osd.geometry().bottom() + OSD_MARGIN_PX
        hint.move(x, y)
        hint.raise_()

    def drawModeFor(self, event: QMouseEvent) -> tuple[str, str | None] | None:
        """What a left-button press starts drawing, (mode, field name), or None
        for nothing: the crop while it is picked, the box of the selected
        field that has none (a press on a box selects that box instead, unless
        Alt is held), or with Alt the selected box again."""
        if event.button() != Qt.MouseButton.LeftButton or not self.hasPicture():
            return None
        if self.fourCornerSelectionMode:
            return None
        if self.cropSelectionMode:
            return (DRAW_CROP, None) if self.uncroppedPictureShown() else None
        alt = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        if self.placementField is not None and (
            alt or self.boxAt(event.position().toPoint()) is None
        ):
            return DRAW_PLACE, self.placementField
        if alt and self.selectedBoxTarget() is not None:
            return DRAW_REDRAW, self.selectedBoxName
        return None

    def startDrawing(
        self,
        mode: str,
        name: str | None,
        view_position: QPoint,
        double_click: bool = False,
    ):
        self.cancelDrawing()
        picture = self.pictureRect()
        band = RubberBand(picture, shade=mode == DRAW_CROP)
        band.setVisible(False)
        self.scene.addItem(band)
        self.drawing = Drawing(
            mode,
            name,
            QPoint(view_position),
            clamped_point(self.mapToScene(view_position), picture),
            band,
            double_click,
        )
        self.updateCursor()

    @staticmethod
    def isClick(moved: QPoint) -> bool:
        """A drag this small (less than DRAW_MIN_PX wide or tall) is a click:
        it draws nothing."""
        return abs(moved.x()) < DRAW_MIN_PX or abs(moved.y()) < DRAW_MIN_PX

    def isDrawing(self) -> bool:
        return self.drawing is not None

    def updateDrawing(self, view_position: QPoint):
        drawing = self.drawing
        drawing.band.setRect(
            drawn_rect(
                drawing.scene_origin, self.mapToScene(view_position), self.pictureRect()
            )
        )
        # (shown when its release would draw it)
        drawing.band.setVisible(not self.isClick(view_position - drawing.view_origin))

    def cancelDrawing(self) -> bool:
        """Stop drawing (Esc); returns False if nothing was being drawn."""
        drawing = self.drawing
        if drawing is None:
            return False
        self.drawing = None
        self.scene.removeItem(drawing.band)
        self.updateCursor()
        return True

    def finishDrawing(self, view_position: QPoint):
        """The drag ended: a box (or the crop) with the rectangle dragged. A
        drag less than DRAW_MIN_PX wide or tall is a click: nothing is
        drawn (the second click of a double-click places the field's box
        there)."""
        drawing = self.drawing
        picture = self.pictureRect()
        self.cancelDrawing()
        if self.isClick(view_position - drawing.view_origin):
            if drawing.double_click and drawing.mode == DRAW_PLACE:
                # the field's box, centred where the double-click was
                self.place_requested.emit(drawing.name, drawing.scene_origin)
            return
        rect = drawn_rect(drawing.scene_origin, self.mapToScene(view_position), picture)
        if rect.width() < 1 or rect.height() < 1:
            return
        if drawing.mode == DRAW_CROP:
            if not self.cropSelectionMode or not self.uncroppedPictureShown():
                return
            self.cancelCropSelection()
            self.crop_drawn.emit(rect, QSizeF(picture.size()))
        else:
            self.box_drawn.emit(drawing.name, rect)

    def updateCursor(self, modifiers=None):
        """A crosshair over the picture while a drag (or a click) draws a box
        or the crop, or picks a corner; else the arrow. Over a box, the box's
        own cursor (move or resize), unless a drag there draws."""
        if self._isPanning:
            return
        if modifiers is None:
            modifiers = Qt.KeyboardModifier.NoModifier
        picking = self.fourCornerSelectionMode or self.cropSelectionMode
        alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)
        if self.drawing is None and not picking and not alt:
            position = self.lastMousePosition
            if position is not None and self.boxAt(position) is not None:
                # the box's cursor (Qt sets it while the mouse is over it)
                return
        crosshair = self.hasPicture() and (
            self.drawing is not None
            or picking
            or self.placementField is not None
            or (alt and self.selectedBoxTarget() is not None)
        )
        self.viewport().setCursor(
            Qt.CursorShape.CrossCursor if crosshair else Qt.CursorShape.ArrowCursor
        )

    # --- mouse and keyboard ----------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        button = event.button()
        position = event.position().toPoint()
        self.lastMousePosition = position
        if button == Qt.MouseButton.MiddleButton:
            self._isPanning = True
            self._lastMousePosition = event.position()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if self.drawing is not None:
            if button != Qt.MouseButton.LeftButton:
                # another button while drawing
                event.accept()
                return
            # the drag's release went elsewhere (e.g. a dialog opened meanwhile)
            self.cancelDrawing()
        if self.fourCornerSelectionMode and button == Qt.MouseButton.LeftButton:
            self.addFourCornerPoint(self.mapToScene(position))
            return
        draw = self.drawModeFor(event)
        if draw is not None:
            self.startDrawing(*draw, position)
            event.accept()
            return
        if self.cropSelectionMode:
            # (the whole frame isn't shown yet)
            event.accept()
            return
        if button == Qt.MouseButton.RightButton:
            box = self.boxAt(position)
            if box is not None:
                # its menu follows (contextMenuEvent)
                if box.name != self.selectedBoxName:
                    self.itemSelectedCallback(box.name)
                event.accept()
                return
            if self.placementField is not None:
                # the field stays selected, to be placed
                event.accept()
                return
        # deselect all the boxes
        self.selectBox(None)
        self.itemSelectedCallback(None)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:
        position = event.position().toPoint()
        if event.button() == Qt.MouseButton.MiddleButton:
            self._isPanning = False
            self.updateCursor(event.modifiers())
        elif self.drawing is not None:
            if event.button() == Qt.MouseButton.LeftButton:
                self.finishDrawing(position)
            event.accept()
        else:
            super().mouseReleaseEvent(event)
            self.detectionTargetsStorage.saveBoxesToStorage()

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:
        position = event.position().toPoint()
        self.lastMousePosition = position
        if self._isPanning:
            delta = event.position() - self._lastMousePosition
            self._lastMousePosition = event.position()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x()
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y()
            )
        elif self.drawing is not None:
            if event.buttons() & Qt.MouseButton.LeftButton:
                self.updateDrawing(position)
            else:
                # the drag's release went elsewhere
                self.cancelDrawing()
        else:
            super().mouseMoveEvent(event)
            self.updateCursor(event.modifiers())

    def mouseDoubleClickEvent(self, event: QMouseEvent | None) -> None:
        """The second press of a quick pair of clicks. It starts drawing as a
        press does: a drag right after a click still draws. Released without
        a drag, it is a double-click: with a field without a box selected, its
        box goes there (finishDrawing)."""
        self.lastMousePosition = event.position().toPoint()
        if self.drawing is None:
            draw = self.drawModeFor(event)
            if draw is not None:
                self.startDrawing(*draw, self.lastMousePosition, double_click=True)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def focusOutEvent(self, event):
        if event.reason() in (
            Qt.FocusReason.ActiveWindowFocusReason,
            Qt.FocusReason.PopupFocusReason,
        ):
            # e.g. a dialog opened during a drag: its release goes elsewhere
            self.cancelDrawing()
        super().focusOutEvent(event)

    def hideEvent(self, event):
        self.cancelDrawing()
        super().hideEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent | None) -> None:
        """A right-click on a box (or the menu key with a box selected): the
        box's menu, which the main window shows."""
        if self.drawing is not None or self.fourCornerSelectionMode:
            event.accept()
            return
        if event.reason() == QContextMenuEvent.Reason.Mouse:
            box = self.boxAt(event.pos())
            if box is not None:
                self.box_menu_requested.emit(box.name, event.globalPos())
                event.accept()
                return
        elif self.selectedBoxName is not None:
            box = self.findBox(self.selectedBoxName)
            if box is not None and box.isVisible():
                corner = self.mapFromScene(box.sceneBoxRect().bottomLeft())
                self.box_menu_requested.emit(
                    box.name, self.viewport().mapToGlobal(corner)
                )
                event.accept()
                return
        super().contextMenuEvent(event)

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            if self.cancelDrawing():
                # the field stays selected
                pass
            elif self.cropSelectionMode:
                # the crop stays
                self.cancelCropSelection()
            elif self.fourCornerSelectionMode:
                # the previous correction stays
                self.cancelFourCornerSelection()
            else:
                self.selectBox(None)
                self.itemSelectedCallback(None)
            event.accept()
            return
        if key == Qt.Key.Key_Alt:
            # Alt+drag redraws the selected box: the crosshair says so
            self.updateCursor(event.modifiers())
        modifiers = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        if (
            key in _NUDGE_KEYS
            and self.drawing is None
            and not self.fourCornerSelectionMode
            and not self.cropSelectionMode
            and modifiers
            in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ShiftModifier)
        ):
            step = (
                NUDGE_LARGE_PX
                if modifiers == Qt.KeyboardModifier.ShiftModifier
                else NUDGE_PX
            )
            dx, dy = _NUDGE_KEYS[key]
            if self.nudgeSelectedBox(dx * step, dy * step):
                event.accept()
                return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent | None) -> None:
        if event.key() == Qt.Key.Key_Alt:
            # (on X11 the event still has the modifier it releases)
            self.updateCursor(event.modifiers() & ~Qt.KeyboardModifier.AltModifier)
        super().keyReleaseEvent(event)

    def leaveEvent(self, event):
        self.lastMousePosition = None
        super().leaveEvent(event)

    # --- zoom --------------------------------------------------------------------------

    def zoomBy(self, factor: float) -> bool:
        """Zoom in (factor > 1) or out, within MIN_ZOOM and MAX_ZOOM (or the
        fitted zoom, if it is outside them)."""
        zoom = self.zoomLevel()
        if factor > 1:
            target = min(zoom * factor, max(MAX_ZOOM, zoom))
        else:
            target = max(zoom * factor, min(MIN_ZOOM, zoom))
        if abs(target - zoom) < 1e-9:
            return False
        self._isScaling = True
        self.fitted = False
        self.scale(target / zoom, target / zoom)
        # the resize events of the scrollbars appearing come first
        QTimer.singleShot(0, self.resetScalingFlag)
        self.emitZoom()
        return True

    def zoomToRect(self, rect: QRectF):
        """Show rect (e.g. a box with some of the picture around it) as large
        as it fits, but no closer than MAX_ZOOM. Fit shows the whole picture
        again."""
        # zooming in makes the scroll bars appear: that resize keeps the zoom
        self._isScaling = True
        self.fitted = False
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        if self.zoomLevel() > MAX_ZOOM:
            self.setTransform(QTransform.fromScale(MAX_ZOOM, MAX_ZOOM))
            self.centerOn(rect.center())
        QTimer.singleShot(0, self.resetScalingFlag)
        self.emitZoom()

    def zoomIn(self):
        return self.zoomBy(KEY_ZOOM_FACTOR)

    def zoomOut(self):
        return self.zoomBy(1 / KEY_ZOOM_FACTOR)

    def wheelEvent(self, event):
        # check for ctrl key
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            if event.angleDelta().y() > 0:
                self.zoomBy(WHEEL_ZOOM_FACTOR)
            else:
                self.zoomBy(1 / WHEEL_ZOOM_FACTOR)
        else:
            # scroll the scene
            super().wheelEvent(event)

    def resetZoom(self):
        self.fitPicture()

    def resetScalingFlag(self):
        self._isScaling = False  # Reset the flag after potential resizeEvent

    def setHeldValues(self, values: dict[str, str]):
        """The fields whose value is held ({name: value}): their labels say
        HELD and show the held value instead of the reading."""
        self.heldValues = dict(values)
        resized = False
        for item in self.boxItems():
            resized |= item.setHeldValue(self.heldValues.get(item.name))
        if resized:
            self.layoutLabels()

    def ocrResult(self, results: list[TextDetectionTargetWithResult]):
        if not self.firstFrameReceived:
            return
        resized = False
        # update the rect with the result
        for targetWithResult in results:
            if targetWithResult.settings["templatefield"]:
                # do not update template fields
                continue
            item = self.findBox(targetWithResult.name)
            if item:
                resized |= item.updateResult(targetWithResult)
            else:
                logger.debug(f"Could not find item with name {targetWithResult.name}")
        if resized:
            # a longer or shorter reading: its label may now cover something
            self.layoutLabels()

    def closeEvent(self, event):
        logger.debug("Close")
        # stop following the boxes and settings, so a closed viewer is neither
        # kept alive nor updated by them
        unsubscribe_from_data(
            "scoresight.json", "box_display_style", self.boxDisplayStyle
        )
        if self.detectionTargetsStorage is not None:
            try:
                self.detectionTargetsStorage.data_changed.disconnect(
                    self.detectionTargetsChanged
                )
            except (RuntimeError, TypeError):
                pass
        super().closeEvent(event)
