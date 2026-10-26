"""The boxes list's Value column: what record_reading() stores in a Value item
and what ReadingDelegate makes of it."""

import pytest

pytestmark = pytest.mark.qt

from PySide6.QtCore import QModelIndex, Qt  # noqa: E402
from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem  # noqa: E402

import theme  # noqa: E402
from readings_view import (  # noqa: E402
    DENSITY_LIVE,
    DENSITY_SETUP,
    HELD_VALUE_ROLE,
    LAST_GOOD_TIME_ROLE,
    MIN_STALE_AFTER_S,
    REJECTED_TEXT_ROLE,
    STATE_ROLE,
    ReadingDelegate,
    ResultState,
    clear_reading,
    format_age,
    reading_at,
    reading_note,
    reading_tooltip,
    record_reading,
    set_held,
    state_glyph,
)


@pytest.fixture
def table(qapp):
    table = QTableWidget(1, 2)
    table.setItem(0, 0, QTableWidgetItem("Home Score"))
    item = QTableWidgetItem()
    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
    table.setItem(0, 1, item)
    yield table
    table.deleteLater()


def index(table):
    return table.indexFromItem(table.item(0, 1))


def value(table):
    return table.item(0, 1)


def test_a_good_reading_is_the_value(table):
    record_reading(value(table), ResultState.Success, "21", now=100.0)
    reading = reading_at(index(table), now=103.0)
    assert (reading.state, reading.value, reading.age) == (
        ResultState.Success,
        "21",
        3.0,
    )
    assert reading_note(reading) == "new · 3 s"
    assert value(table).text() == "21"
    assert value(table).data(LAST_GOOD_TIME_ROLE) == 100.0
    assert value(table).data(Qt.ItemDataRole.AccessibleTextRole) == "21, new"

    # the same value again: still the last good reading, read just now
    record_reading(value(table), ResultState.SameNoChange, "21", now=110.0)
    reading = reading_at(index(table), now=111.0)
    assert (reading.state, reading.value, reading.age) == (
        ResultState.SameNoChange,
        "21",
        1.0,
    )
    assert reading_note(reading) == "same · 1 s"


def test_rejected_and_empty_readings_keep_the_value(table):
    record_reading(value(table), ResultState.Success, "21", now=100.0)
    record_reading(value(table), ResultState.FailedFilter, "2l", now=104.0)
    reading = reading_at(index(table), now=105.0)
    assert (reading.state, reading.value, reading.rejected, reading.age) == (
        ResultState.FailedFilter,
        "21",
        "2l",
        5.0,
    )
    assert value(table).data(STATE_ROLE) == "FailedFilter"
    assert value(table).data(REJECTED_TEXT_ROLE) == "2l"
    assert reading_note(reading) == "rejected ‘2l’ · 5 s"
    assert "Last good reading: 21 (5 s ago)" in reading_tooltip(reading)

    record_reading(value(table), ResultState.Empty, "", now=106.0)
    reading = reading_at(index(table), now=106.0)
    assert (reading.state, reading.value) == (ResultState.Empty, "21")
    assert reading_note(reading) == "no reading · 6 s"


def test_a_box_without_a_good_reading(table):
    record_reading(value(table), ResultState.FailedFilter, "Z", now=1.0)
    reading = reading_at(index(table), now=50.0)
    assert (reading.value, reading.age, reading.stale) == ("", None, False)
    assert reading_note(reading) == "rejected ‘Z’"


def test_an_old_good_reading_is_stale(table):
    record_reading(value(table), ResultState.Success, "3", now=0.0)
    at = index(table)
    assert not reading_at(at, now=MIN_STALE_AFTER_S).stale
    assert reading_at(at, now=MIN_STALE_AFTER_S + 1).stale
    assert not reading_at(at, now=MIN_STALE_AFTER_S + 1, stale_after=60).stale
    assert "No good reading for a while" in reading_tooltip(
        reading_at(at, now=MIN_STALE_AFTER_S + 1)
    )


def test_clear_reading(table):
    record_reading(value(table), ResultState.Success, "21", now=1.0)
    clear_reading(value(table))
    reading = reading_at(index(table), now=2.0)
    assert (reading.state, reading.value, reading.age) == (None, "", None)


def test_a_missing_text_is_empty(table):
    record_reading(value(table), ResultState.Success, None, now=1.0)
    assert value(table).text() == ""


def test_state_glyphs_differ():
    glyphs = [state_glyph(state) for state in ResultState]
    assert sorted(glyphs) == sorted({"✓", "=", "!", "∅"})
    assert state_glyph(None) == ""


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0, "0 s"), (59.9, "59 s"), (60, "1 min"), (3599, "59 min"), (7200, "2 h")],
)
def test_format_age(seconds, text):
    assert format_age(seconds) == text


def test_reading_at_an_empty_index():
    reading = reading_at(QModelIndex(), now=1.0)
    assert (reading.state, reading.value, reading.age) == (None, "", None)


def test_the_delegate_paints_every_state(table, qapp):
    delegate = ReadingDelegate(table, clock=lambda: 10.0)
    table.setItemDelegateForColumn(1, delegate)
    table.resize(400, 300)
    for state, text in (
        (ResultState.Success, "21"),
        (ResultState.SameNoChange, "21"),
        (ResultState.FailedFilter, "2l"),
        (ResultState.Empty, ""),
    ):
        record_reading(value(table), state, text, now=1.0)
        for paused in (False, True):
            delegate.setPaused(paused)
            table.grab()  # paints without errors
    assert delegate.rowHeight(table.font()) > table.fontMetrics().height() * 2
    delegate.setStaleAfter(1)
    assert delegate.stale_after == MIN_STALE_AFTER_S
    delegate.setStaleAfter(40)
    assert delegate.stale_after == 40


def test_the_live_density_has_large_values(table, qapp):
    delegate = ReadingDelegate(table)
    font = table.font()
    setup_height = delegate.rowHeight(font)
    setup_value = delegate.valueFont(font)
    delegate.setDensity(DENSITY_LIVE)
    # about 30 pixels (2.5 times the list's font)
    live_value = delegate.valueFont(font)
    assert live_value.pointSizeF() == pytest.approx(font.pointSizeF() * 2.5)
    assert live_value.pointSizeF() > setup_value.pointSizeF() * 1.7
    assert delegate.rowHeight(font) > setup_height * 1.4
    assert delegate.discSize(font) > ReadingDelegate(table).discSize(font)
    delegate.setDensity("nonsense")
    assert delegate.density == DENSITY_SETUP
    assert delegate.rowHeight(font) == setup_height


def test_a_held_value(table):
    record_reading(value(table), ResultState.Success, "2nd", now=1.0)
    record_reading(value(table), ResultState.FailedFilter, "Z", now=2.0)
    set_held(value(table), "3rd")
    reading = reading_at(index(table), now=100.0)
    # the held value is what the outputs get; the OCR goes on underneath
    assert (reading.held, reading.shown, reading.value) == ("3rd", "3rd", "2nd")
    assert reading_note(reading) == "held · OCR: ‘Z’"
    assert not reading.stale  # a held value doesn't get old
    assert value(table).data(HELD_VALUE_ROLE) == "3rd"
    assert value(table).data(Qt.ItemDataRole.AccessibleTextRole) == "3rd, held"
    assert reading_tooltip(reading).startswith("Held value: 3rd.")
    record_reading(value(table), ResultState.SameNoChange, "2nd", now=101.0)
    assert reading_note(reading_at(index(table), now=101.0)) == "held · OCR: 2nd"
    # a template field has no OCR of its own
    table.item(0, 0).setData(Qt.ItemDataRole.UserRole, "templatefield")
    assert reading_note(reading_at(index(table), now=101.0)) == "held"
    set_held(value(table), None)
    reading = reading_at(index(table), now=101.0)
    assert (reading.held, reading.shown) == (None, "2nd")
    assert value(table).data(Qt.ItemDataRole.AccessibleTextRole) == "2nd, same"


@pytest.mark.parametrize("density", [DENSITY_SETUP, DENSITY_LIVE])
def test_a_held_value_is_painted_with_its_release_button(table, qapp, density):
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QStyleOptionViewItem

    delegate = ReadingDelegate(table, clock=lambda: 10.0)
    delegate.setDensity(density)
    table.setItemDelegateForColumn(1, delegate)
    table.resize(700, 200)
    table.setColumnWidth(1, 450)
    record_reading(value(table), ResultState.Success, "2nd", now=1.0)
    set_held(value(table), "3rd")
    for paused in (False, True):
        delegate.setPaused(paused)
        table.grab()  # paints without errors

    option = QStyleOptionViewItem()
    option.rect = table.visualRect(index(table))
    option.font = table.font()
    reading = reading_at(index(table), now=10.0)
    assert delegate.releaseShown(option.rect, option.font, reading)
    # too narrow for it: it isn't drawn (Release is in the context menu)
    narrow = option.rect.adjusted(0, 0, -(option.rect.width() - 60), 0)
    assert not delegate.releaseShown(narrow, option.font, reading)

    released = []
    delegate.releaseRequested.connect(released.append)
    center = delegate.releaseRect(option.rect, option.font).center()

    def click(kind, at):
        event = QMouseEvent(
            kind,
            QPointF(at),
            QPointF(at),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        return delegate.editorEvent(event, table.model(), option, index(table))

    assert click(QEvent.Type.MouseButtonPress, center)
    assert click(QEvent.Type.MouseButtonRelease, center)
    assert released == ["Home Score"]
    # elsewhere in the cell: not the button
    assert not click(QEvent.Type.MouseButtonRelease, option.rect.topRight())
    assert released == ["Home Score"]
    # not held: no button
    set_held(value(table), None)
    assert not click(QEvent.Type.MouseButtonRelease, center)


def test_state_colours_follow_the_palette(qapp):
    light = QPalette()
    light.setColor(QPalette.ColorRole.Window, Qt.GlobalColor.white)
    dark = QPalette()
    dark.setColor(QPalette.ColorRole.Window, Qt.GlobalColor.black)
    assert not theme.is_dark(light) and theme.is_dark(dark)
    for name in ("new", "same", "rejected", "empty", "paused"):
        assert theme.color(name, light) != theme.color(name, dark)
    assert theme.on_color(light) != theme.on_color(dark)
    assert not theme.media_icon("pause", theme.on_color(dark)).isNull()
