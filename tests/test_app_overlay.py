"""The boxes drawn over the picture (src/resizable_rect.py): the label pills,
their placement, the state glyphs and the box display styles."""

import pytest
from PySide6.QtCore import QRectF, QSizeF

from resizable_rect import (
    BOX_STYLE_ALL,
    BOX_STYLE_NAMES,
    BOX_STYLE_NONE,
    BOX_STYLE_OUTLINE,
    LABEL_ABOVE_LEFT,
    LABEL_ABOVE_RIGHT,
    LABEL_BELOW_LEFT,
    LABEL_BELOW_RIGHT,
    LABEL_INSIDE,
    BoxLabel,
    ResizableRectWithNameTypeAndResult,
    label_rect,
    place_label,
)
from text_detection_target import TextDetectionTargetWithResult

pytestmark = pytest.mark.qt

ResultState = TextDetectionTargetWithResult.ResultState

PICTURE = QRectF(0, 0, 1280, 720)
LABEL = QSizeF(120, 20)


# --- placement ------------------------------------------------------------------------


def test_a_label_goes_below_its_box_at_the_left():
    box = QRectF(200, 200, 210, 175)
    assert place_label(box, LABEL, PICTURE, [], 3) == LABEL_BELOW_LEFT
    rect = label_rect(LABEL_BELOW_LEFT, box, LABEL, 3)
    # outside the box: it never covers the digits
    assert rect.top() == box.bottom() + 3
    assert rect.left() == box.left()
    assert not rect.intersects(box)


def test_a_label_avoids_other_boxes_and_labels():
    box = QRectF(200, 200, 100, 100)
    # a box under it at its right: the label (wider than the box) is aligned
    # to the box's right edge instead
    below_right = QRectF(305, 305, 100, 50)
    assert place_label(box, LABEL, PICTURE, [below_right], 3) == LABEL_BELOW_RIGHT
    # boxes under it on both sides: above it
    below = QRectF(100, 305, 400, 50)
    assert place_label(box, LABEL, PICTURE, [below], 3) == LABEL_ABOVE_LEFT
    above_right = QRectF(305, 150, 60, 45)
    assert (
        place_label(box, LABEL, PICTURE, [below, above_right], 3) == LABEL_ABOVE_RIGHT
    )


def test_without_room_outside_a_label_goes_inside_its_box():
    # at the bottom of the picture, with a box right above it
    box = QRectF(600, 650, 100, 70)
    above = QRectF(500, 600, 300, 45)
    assert place_label(box, LABEL, PICTURE, [above], 3) == LABEL_INSIDE
    rect = label_rect(LABEL_INSIDE, box, LABEL, 3)
    assert box.contains(rect.topLeft())


def test_a_label_at_the_bottom_of_the_picture_goes_above():
    box = QRectF(605, 690, 80, 25)
    assert place_label(box, LABEL, PICTURE, [], 3) == LABEL_ABOVE_LEFT


def test_a_label_wider_than_its_box_near_the_right_edge():
    # left-aligned it would leave the picture: aligned to the box's right edge
    box = QRectF(1200, 300, 70, 100)
    assert place_label(box, LABEL, PICTURE, [], 3) == LABEL_BELOW_RIGHT


# --- the pill -------------------------------------------------------------------------------


@pytest.fixture
def box(qapp, make_target):
    return ResizableRectWithNameTypeAndResult(
        make_target("Home Score", rect=(200, 200, 210, 175)),
        1280,
        boxDisplayStyle=BOX_STYLE_NAMES,
    )


def reading(make_result, state, text):
    return make_result("Home Score", text, state=state)


@pytest.mark.parametrize(
    "state, text, shown",
    [
        ("Success", "21", "✓ Home Score 21"),
        ("SameNoChange", "21", "= Home Score 21"),
        ("FailedFilter", "2l", "! Home Score ‘2l’"),
        # "EMP" was jargon: the glyph says it
        ("Empty", "", "∅ Home Score"),
    ],
)
def test_the_label_shows_the_state_glyph(box, make_result, state, text, shown):
    box.updateResult(reading(make_result, state, text))
    assert box.label.text() == shown


def test_the_label_size_changes_only_with_its_text(box, make_result):
    assert box.updateResult(reading(make_result, "Success", "9")) is True
    # the same again: nothing to place again
    assert box.updateResult(reading(make_result, "Success", "9")) is False
    # unchanged size: the same width digits ("tnum")
    box.updateResult(reading(make_result, "Success", "8"))
    assert box.updateResult(reading(make_result, "Success", "10")) is True


def test_the_label_is_fixed_size_on_screen(box):
    from PySide6.QtWidgets import QGraphicsItem

    flags = box.label.flags()
    assert flags & QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
    size = box.label.size()
    assert 14 <= size.height() <= 24  # about 11 px text


def test_the_label_is_placed_relative_to_the_box(box):
    rect = box.rect()
    box.setLabelPlacement(LABEL_BELOW_LEFT)
    assert box.label.pos() == rect.bottomLeft()
    assert box.label.boundingRect().top() > 0  # under the anchor
    box.setLabelPlacement(LABEL_ABOVE_RIGHT)
    assert box.label.pos() == rect.topRight()
    assert box.label.boundingRect().bottom() < 0  # over it
    assert box.label.boundingRect().right() <= 1


def test_the_display_styles(box):
    box.setBoxDisplayStyle(BOX_STYLE_NONE)
    assert not box.isVisible()
    box.setBoxDisplayStyle(BOX_STYLE_OUTLINE)
    assert box.isVisible() and not box.label.isVisibleTo(box)
    box.setBoxDisplayStyle(BOX_STYLE_NAMES)
    assert box.label.isVisibleTo(box)
    box.setBoxDisplayStyle(BOX_STYLE_ALL)
    assert box.label.isVisibleTo(box)


def test_a_selected_box_shows_its_label_and_handles(box):
    box.setBoxDisplayStyle(BOX_STYLE_NONE)
    box.setSelected(True)
    assert box.isVisible() and box.label.isVisibleTo(box)
    assert all(handle.isVisibleTo(box) for handle in box.cornerBoxes)
    box.setSelected(False)
    assert not box.isVisible()


def test_a_suppressed_box_is_hidden_in_every_style(box):
    box.setSuppressed(True)
    for style in (BOX_STYLE_OUTLINE, BOX_STYLE_NAMES, BOX_STYLE_ALL):
        box.setBoxDisplayStyle(style)
        assert not box.isVisible()
    box.setSuppressed(False)
    assert box.isVisible()


def test_the_all_style_draws_what_the_ocr_read(box, make_result):
    from text_detection_target import TextDetectionTargetWithResult

    box.setBoxDisplayStyle(BOX_STYLE_ALL)
    target = reading(make_result, "Success", "21")
    result = TextDetectionTargetWithResult(
        target,
        "21",
        ResultState.Success,
        QRectF(5, 5, 100, 80),
        {"boxes": [{"x": 10, "y": 10, "w": 30, "h": 60}] * 2},
    )
    box.updateResult(result)
    assert box.effectiveRect.isVisibleTo(box)
    assert [extra.isVisibleTo(box) for extra in box.extraBoxes] == [True, True]
    # the items are reused, the ones not needed hidden
    result.extras = {"boxes": [{"x": 10, "y": 10, "w": 30, "h": 60}]}
    box.updateResult(result)
    assert len(box.extraBoxes) == 2
    assert [extra.isVisibleTo(box) for extra in box.extraBoxes] == [True, False]
    box.setBoxDisplayStyle(BOX_STYLE_NAMES)
    assert not box.effectiveRect.isVisibleTo(box)
    assert not any(extra.isVisibleTo(box) for extra in box.extraBoxes)
    # cosmetic: the same thickness at every zoom
    assert box.effectiveRect.pen().isCosmetic()
    assert box.pen().isCosmetic()


def test_the_ocr_details_follow_a_moved_box(box, make_result):
    # The OCR gives the area read and the characters relative to the box's
    # top-left corner: they were drawn where the box was first put once it was
    # moved other than by dragging it (arrow keys, drawn again, the crop).
    from text_detection_target import TextDetectionTargetWithResult

    box.setBoxDisplayStyle(BOX_STYLE_ALL)
    # (as ImageViewer moves a box to its target's new place)
    box.setRect(300 - box.x(), 250 - box.y(), 210, 175)
    result = TextDetectionTargetWithResult(
        reading(make_result, "Success", "21"),
        "21",
        ResultState.Success,
        QRectF(10, 20, 150, 80),
        {"boxes": [{"x": 12, "y": 22, "w": 40, "h": 70}]},
    )
    box.updateResult(result)
    assert box.mapRectToScene(box.effectiveRect.rect()) == QRectF(310, 270, 150, 80)
    assert box.mapRectToScene(box.extraBoxes[0].rect()) == QRectF(312, 272, 40, 70)


def test_a_label_alone(qapp):
    label = BoxLabel("Period", None)
    assert label.text() == "Period"
    assert label.setReading(ResultState.Success, "2nd") is True
    assert label.text() == "✓ Period 2nd"


def test_a_held_value_says_held(box, make_result):
    # Hold value: the label says HELD and shows the held value, whatever the
    # OCR reads meanwhile
    from PySide6.QtGui import QImage, QPainter

    box.updateResult(reading(make_result, "FailedFilter", "Z"))
    assert box.setHeldValue("3rd") is True
    assert box.label.text() == "HELD Home Score 3rd"
    box.updateResult(reading(make_result, "Success", "2nd"))
    assert box.label.text() == "HELD Home Score 3rd"
    image = QImage(300, 60, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    box.label.paint(painter, None)  # paints without errors
    painter.end()
    # released: the reading again
    assert box.setHeldValue(None) is True
    assert box.label.text() == "✓ Home Score 2nd"
    assert box.setHeldValue(None) is False


def test_the_viewer_marks_the_held_values(qapp, make_target, memory_storage):
    from camera_info import CameraInfo
    from source_view import ImageViewer

    memory_storage.add_item(make_target("Home Score", rect=(100, 100, 200, 100)))
    memory_storage.add_item(make_target("Time", rect=(400, 100, 200, 100)))
    viewer = ImageViewer(
        CameraInfo("stub", "stub", "stub", CameraInfo.CameraType.FILE),
        lambda corners: None,
        memory_storage,
        lambda name: None,
        start=False,
    )
    try:
        viewer.scene.setSceneRect(0, 0, 1280, 720)
        viewer.firstFrameReceived = True
        viewer.setHeldValues({"Time": "0:00"})
        viewer.detectionTargetsChanged()
        labels = {item.name: item.label for item in viewer.boxItems()}
        assert labels["Time"].held == "0:00"
        assert labels["Home Score"].held is None
        viewer.setHeldValues({})
        assert labels["Time"].held is None
    finally:
        viewer.close()
        viewer.deleteLater()
