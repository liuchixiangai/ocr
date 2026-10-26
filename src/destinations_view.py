"""The list of output destinations (listView_destinations): every output with
its on/off switch, its state glyph, a line about what is happening and how long
ago it last sent something.

DestinationsModel has one row per destination, in the order of
output_status.DESTINATIONS (the order of the output pages too). MainWindow
gives it every destination's DestinationStatus about once a second
(setStatuses).

The model never turns an output on or off by itself: a click on a switch, or
Space, asks for it (toggleRequested), MainWindow operates the output's own
control (Start vMix, the API checkbox, Connect OBS...), and the next status
shows the result. Those controls and their settings stay the only source of
truth.
"""

import time
from collections.abc import Iterable

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QFont,
    QFontMetrics,
    QPainter,
    QPalette,
    QPen,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolTip,
)

import theme
from output_status import (
    DESTINATIONS,
    STATE_COLORS,
    STATE_SHAPES,
    DestinationStatus,
    accessible_text,
    age_text,
    destination_description,
    destination_name,
    status_tooltip,
)

KEY_ROLE = Qt.ItemDataRole.UserRole + 1  # the destination's key ("vmix"...)
STATUS_ROLE = Qt.ItemDataRole.UserRole + 2  # its DestinationStatus

# geometry of a row, in pixels
_PAD_X = 6
_PAD_Y = 4
_SWITCH_W = 28
_SWITCH_H = 16
_GAP = 9


class DestinationsModel(QStandardItemModel):
    """The destinations and their statuses."""

    # (destination key, on): the user asked to turn the destination on or off
    toggleRequested = Signal(str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._statuses: dict[str, DestinationStatus] = {}
        for key in DESTINATIONS:
            item = QStandardItem()
            item.setData(key, KEY_ROLE)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.appendRow(item)
        self.retranslate()

    def retranslate(self):
        """Set the names (and the texts made from the statuses) again, in the
        current language."""
        for row, key in enumerate(DESTINATIONS):
            item = self.item(row)
            item.setText(destination_name(key))
            status = self._statuses.get(key)
            if status is not None:
                self._apply(item, status)
            else:
                item.setData(destination_description(key), Qt.ItemDataRole.ToolTipRole)

    def rowOf(self, key: str) -> int:
        return DESTINATIONS.index(key)

    def status(self, key: str) -> DestinationStatus | None:
        return self._statuses.get(key)

    def setStatuses(self, statuses: Iterable[DestinationStatus]):
        """Show new statuses (rows whose status didn't change are left alone)."""
        for status in statuses:
            if self._statuses.get(status.key) == status:
                continue
            self._statuses[status.key] = status
            self._apply(self.item(self.rowOf(status.key)), status)

    def _apply(self, item: QStandardItem, status: DestinationStatus):
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if status.switchable:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        item.setFlags(flags)
        if status.switchable:
            item.setCheckState(
                Qt.CheckState.Checked if status.enabled else Qt.CheckState.Unchecked
            )
        else:
            item.setData(None, Qt.ItemDataRole.CheckStateRole)
        item.setData(status, STATUS_ROLE)
        item.setData(accessible_text(status), Qt.ItemDataRole.AccessibleTextRole)
        item.setData(
            status.hint or destination_description(status.key),
            Qt.ItemDataRole.AccessibleDescriptionRole,
        )

    def requestToggle(self, index):
        """Ask to turn the destination at index on (if off) or off."""
        status = index.data(STATUS_ROLE)
        if status is None or not status.switchable:
            return
        self.setData(
            index,
            Qt.CheckState.Unchecked if status.enabled else Qt.CheckState.Checked,
            Qt.ItemDataRole.CheckStateRole,
        )

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        # A change of the check state from the view (a click, Space, an
        # accessibility action) is a request: the output's control decides
        if role == Qt.ItemDataRole.CheckStateRole and index.isValid():
            status = self._statuses.get(index.data(KEY_ROLE))
            if status is not None and status.switchable:
                if not isinstance(value, Qt.CheckState):
                    value = Qt.CheckState(int(value))
                on = value == Qt.CheckState.Checked
                if on != status.enabled:
                    self.toggleRequested.emit(status.key, on)
            return False
        return super().setData(index, value, role)


class DestinationDelegate(QStyledItemDelegate):
    """Paints a destination: the switch (a dashed one for the HTTP server,
    which is always on), the name and the time of the last activity, and the
    state glyph with the status line under them."""

    def __init__(self, parent=None, clock=time.monotonic):
        super().__init__(parent)
        self.clock = clock

    @staticmethod
    def nameFont(font: QFont) -> QFont:
        name_font = QFont(font)
        name_font.setWeight(QFont.Weight.DemiBold)
        return name_font

    @staticmethod
    def noteFont(font: QFont) -> QFont:
        note_font = QFont(font)
        size = font.pointSizeF()
        if size > 0:
            note_font.setPointSizeF(max(7.0, size * 0.92))
        return note_font

    def rowHeight(self, font: QFont) -> int:
        return (
            QFontMetrics(self.nameFont(font)).height()
            + QFontMetrics(self.noteFont(font)).height()
            + 2 * _PAD_Y
            + 2
        )

    def sizeHint(self, option, index):
        metrics = QFontMetrics(self.nameFont(option.font))
        name = index.data(Qt.ItemDataRole.DisplayRole) or ""
        return QSize(
            _PAD_X + _SWITCH_W + _GAP + metrics.horizontalAdvance(name) + 80,
            self.rowHeight(option.font),
        )

    @staticmethod
    def switchRect(rect) -> QRectF:
        rect = QRectF(rect)
        return QRectF(
            rect.left() + _PAD_X,
            rect.center().y() - _SWITCH_H / 2,
            _SWITCH_W,
            _SWITCH_H,
        )

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        # the background, the selection and the focus frame; the texts and the
        # switch are drawn below
        opt.text = ""
        opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

        status: DestinationStatus | None = index.data(STATUS_ROLE)
        palette = opt.palette
        enabled = bool(opt.state & QStyle.StateFlag.State_Enabled)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        group = QPalette.ColorGroup.Normal if enabled else QPalette.ColorGroup.Disabled
        text_color = palette.color(
            group,
            QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text,
        )
        muted = (
            text_color
            if selected
            else palette.color(group, QPalette.ColorRole.PlaceholderText)
        )

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(opt.rect)
        switch = self.switchRect(rect)
        if status is not None:
            self._paintSwitch(painter, switch, status, palette, selected, muted)

        left = switch.right() + _GAP
        right = rect.right() - _PAD_X
        name_font = self.nameFont(opt.font)
        name_metrics = QFontMetrics(name_font)
        note_font = self.noteFont(opt.font)
        note_metrics = QFontMetrics(note_font)
        top = rect.top() + _PAD_Y
        line1 = QRectF(left, top, right - left, name_metrics.height())
        line2 = QRectF(left, line1.bottom() + 2, right - left, note_metrics.height())

        # the time of the last activity, right-aligned on the first line
        age = ""
        if status is not None and status.last_activity_at is not None:
            age = age_text(self.clock() - status.last_activity_at)
        painter.setFont(note_font)
        age_width = note_metrics.horizontalAdvance(age) if age else 0
        if age:
            painter.setPen(muted)
            painter.drawText(
                line1,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                age,
            )

        name = index.data(Qt.ItemDataRole.DisplayRole) or ""
        painter.setFont(name_font)
        painter.setPen(text_color)
        name_room = max(0, int(line1.width() - age_width - (_GAP if age else 0)))
        painter.drawText(
            line1,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            name_metrics.elidedText(name, Qt.TextElideMode.ElideRight, name_room),
        )

        if status is not None:
            color_name = STATE_COLORS.get(status.state)
            if color_name and enabled and not selected:
                state_color = theme.color(color_name, palette)
            elif color_name and enabled:
                state_color = text_color
            else:
                state_color = muted
            glyph_size = max(8.0, note_metrics.ascent() * 0.85)
            glyph = QRectF(
                line2.left(),
                line2.center().y() - glyph_size / 2,
                glyph_size,
                glyph_size,
            )
            theme.draw_state_shape(
                painter, glyph, STATE_SHAPES[status.state], state_color
            )
            detail_rect = line2.adjusted(glyph_size + 5, 0, 0, 0)
            painter.setFont(note_font)
            painter.setPen(state_color if status.has_problem else muted)
            painter.drawText(
                detail_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                # in the middle: the start and the end ("· retrying",
                # "· 5 files") stay visible
                note_metrics.elidedText(
                    status.detail,
                    Qt.TextElideMode.ElideMiddle,
                    int(detail_rect.width()),
                ),
            )
        painter.restore()

    @staticmethod
    def _paintSwitch(painter, rect, status, palette, selected, muted):
        accent = palette.color(QPalette.ColorRole.Highlight)
        on_accent = palette.color(QPalette.ColorRole.HighlightedText)
        if selected:
            # on the selection's (accent) background
            accent, on_accent = on_accent, accent
        radius = rect.height() / 2
        knob_size = rect.height() - 6
        on = status.enabled
        knob_x = rect.right() - 3 - knob_size if on else rect.left() + 3
        knob = QRectF(knob_x, rect.top() + 3, knob_size, knob_size)
        painter.save()
        if not status.switchable:
            # always on: a dashed outline, the knob on the right
            pen = QPen(muted, 1.2, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(0.6, 0.6, -0.6, -0.6), radius, radius)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(muted)
            painter.drawEllipse(knob)
        elif on:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawRoundedRect(rect, radius, radius)
            painter.setBrush(on_accent)
            painter.drawEllipse(knob)
        else:
            painter.setPen(QPen(muted, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                rect.adjusted(0.75, 0.75, -0.75, -0.75), radius, radius
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(muted)
            painter.drawEllipse(knob)
        painter.restore()

    def editorEvent(self, event, model, option, index):
        status = index.data(STATUS_ROLE)
        if status is None or not status.switchable:
            return False
        kind = event.type()
        if kind in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
        ):
            if event.button() != Qt.MouseButton.LeftButton:
                return False
            target = self.switchRect(option.rect).adjusted(-4, -4, 4, 4)
            if not target.contains(event.position()):
                return False
            if kind == QEvent.Type.MouseButtonRelease:
                model.requestToggle(index)
                return True
            # a press also selects the row; a double click is swallowed
            return kind == QEvent.Type.MouseButtonDblClick
        if kind == QEvent.Type.KeyPress and event.key() in (
            Qt.Key.Key_Space,
            Qt.Key.Key_Select,
        ):
            model.requestToggle(index)
            return True
        return False

    def helpEvent(self, event, view, option, index):
        if event.type() == QEvent.Type.ToolTip and index.isValid():
            status = index.data(STATUS_ROLE)
            if status is not None:
                QToolTip.showText(
                    event.globalPos(), status_tooltip(status, self.clock()), view
                )
                return True
        return super().helpEvent(event, view, option, index)


class CompactDestinationDelegate(DestinationDelegate):
    """Paints a destination on one line, for the Live layout's outputs list:
    the state glyph, the name, the status line and the time of the last
    activity. It has no switch: the outputs are turned on and off in the
    inspector."""

    # the width of the name column, as a multiple of the font's height
    NAME_COLUMN_EMS = 6.5

    def rowHeight(self, font: QFont) -> int:
        return (
            max(
                QFontMetrics(self.nameFont(font)).height(),
                QFontMetrics(self.noteFont(font)).height(),
            )
            + _PAD_Y
        )

    def sizeHint(self, option, index):
        metrics = QFontMetrics(self.nameFont(option.font))
        return QSize(
            round(metrics.height() * self.NAME_COLUMN_EMS) + 120,
            self.rowHeight(option.font),
        )

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

        status: DestinationStatus | None = index.data(STATUS_ROLE)
        palette = opt.palette
        enabled = bool(opt.state & QStyle.StateFlag.State_Enabled)
        group = QPalette.ColorGroup.Normal if enabled else QPalette.ColorGroup.Disabled
        text_color = palette.color(group, QPalette.ColorRole.Text)
        muted = palette.color(group, QPalette.ColorRole.PlaceholderText)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(opt.rect).adjusted(_PAD_X, 0, -_PAD_X, 0)
        name_font = self.nameFont(opt.font)
        name_metrics = QFontMetrics(name_font)
        note_font = self.noteFont(opt.font)
        note_metrics = QFontMetrics(note_font)

        # the state glyph
        glyph_size = max(8.0, note_metrics.ascent() * 0.85)
        state_color = muted
        if status is not None:
            color_name = STATE_COLORS.get(status.state)
            if color_name and enabled:
                state_color = theme.color(color_name, palette)
            glyph = QRectF(
                rect.left(), rect.center().y() - glyph_size / 2, glyph_size, glyph_size
            )
            theme.draw_state_shape(
                painter, glyph, STATE_SHAPES[status.state], state_color
            )
        left = rect.left() + glyph_size + _GAP

        # the name, in a column
        name_width = round(name_metrics.height() * self.NAME_COLUMN_EMS)
        name = index.data(Qt.ItemDataRole.DisplayRole) or ""
        painter.setFont(name_font)
        painter.setPen(text_color)
        painter.drawText(
            QRectF(left, rect.top(), name_width, rect.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            name_metrics.elidedText(name, Qt.TextElideMode.ElideRight, name_width),
        )
        left += name_width + _GAP

        # the time of the last activity, at the right
        painter.setFont(note_font)
        right = rect.right()
        if status is not None and status.last_activity_at is not None:
            age = age_text(self.clock() - status.last_activity_at)
            age_width = note_metrics.horizontalAdvance(age)
            painter.setPen(muted)
            painter.drawText(
                QRectF(right - age_width, rect.top(), age_width, rect.height()),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                age,
            )
            right -= age_width + _GAP

        # the status line, between them
        if status is not None and right > left:
            painter.setPen(state_color if status.has_problem else muted)
            painter.drawText(
                QRectF(left, rect.top(), right - left, rect.height()),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                note_metrics.elidedText(
                    status.detail, Qt.TextElideMode.ElideMiddle, int(right - left)
                ),
            )
        painter.restore()

    def editorEvent(self, event, model, option, index):
        # no switch to click
        return False
