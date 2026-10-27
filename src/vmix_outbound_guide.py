# -*- coding: utf-8 -*-
"""vMix outbound guide and the two-path chooser on the Output tab.

The guide card lives on the text-files page: short steps, a big
「复制 JSON 路径」 button, the results.json path, and a live write status.
The chooser sits above every output page so JSON → vMix Data Sources and
the advanced vMix API are both one click away. It does not change how
FileOutput writes.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from file_output import CSV_FILE_NAME, JSON_FILE_NAME, json_row_from_values
from theme import is_dark

JSON_PATH = "json"
API_PATH = "api"

SAMPLE_JSON = (
    "[\n"
    "  {\n"
    '    "home_score": "52",\n'
    '    "away_score": "55",\n'
    '    "game_clock": "01:16.7",\n'
    '    "period": "4",\n'
    '    "shot_clock": "4.9"\n'
    "  }\n"
    "]\n"
)


class VmixOutboundGuide:
    """Guide + copy buttons living on the Text files output page."""

    def __init__(self, ui, window):
        self.ui = ui
        self.window = window
        self._installed = False
        self._zh = True
        self.frame: QFrame | None = None
        self.label_title: QLabel | None = None
        self.label_steps: QLabel | None = None
        self.label_status: QLabel | None = None
        self.btn_copy_json: QPushButton | None = None
        self.btn_copy_csv: QPushButton | None = None
        self.btn_copy_sample: QPushButton | None = None
        self.btn_open_folder: QPushButton | None = None
        self.label_path: QLabel | None = None
        self._advanced: QWidget | None = None
        self._highlighted = False

    def install(self) -> None:
        if self._installed:
            return
        parent = self.ui.tab_textFiles
        layout = self.ui.horizontalLayout_14  # VBox of format checkboxes

        self.frame = QFrame(parent)
        self.frame.setObjectName("frame_vmixOutboundGuide")
        self.frame.setFrameShape(QFrame.StyledPanel)
        self._apply_frame_style()
        v = QVBoxLayout(self.frame)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(6)

        self.label_title = QLabel(self.frame)
        self.label_title.setWordWrap(True)
        f = self.label_title.font()
        f.setPointSize(max(f.pointSize() + 1, 11))
        f.setBold(True)
        self.label_title.setFont(f)
        v.addWidget(self.label_title)

        self.label_steps = QLabel(self.frame)
        self.label_steps.setWordWrap(True)
        self.label_steps.setTextFormat(Qt.RichText)
        self.label_steps.setOpenExternalLinks(False)
        self.label_steps.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse
        )
        v.addWidget(self.label_steps)

        self.btn_copy_json = QPushButton(self.frame)
        self.btn_copy_json.setObjectName("btn_copyJsonPath")
        self.btn_copy_json.setCursor(Qt.PointingHandCursor)
        self.btn_copy_json.setMinimumHeight(44)
        self.btn_copy_json.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        copy_font = self.btn_copy_json.font()
        copy_font.setBold(True)
        if copy_font.pointSize() > 0:
            copy_font.setPointSize(min(copy_font.pointSize() + 1, 16))
        self.btn_copy_json.setFont(copy_font)
        v.addWidget(self.btn_copy_json)

        self.label_path = QLabel(self.frame)
        self.label_path.setObjectName("label_jsonPath")
        self.label_path.setWordWrap(True)
        self.label_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path_font = self.label_path.font()
        path_font.setBold(True)
        self.label_path.setFont(path_font)
        v.addWidget(self.label_path)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.btn_copy_csv = QPushButton(self.frame)
        self.btn_copy_sample = QPushButton(self.frame)
        self.btn_open_folder = QPushButton(self.frame)
        for b in (self.btn_copy_csv, self.btn_copy_sample, self.btn_open_folder):
            b.setCursor(Qt.PointingHandCursor)
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)

        self.label_status = QLabel(self.frame)
        self.label_status.setWordWrap(True)
        self.label_status.setStyleSheet("color: #93c5fd;")
        v.addWidget(self.label_status)

        # Put guide at the top of the checkbox column
        layout.insertWidget(0, self.frame)

        # Prefer JSON first among checkboxes
        for box in (
            self.ui.checkBox_saveJson,
            self.ui.checkBox_saveCsv,
            self.ui.checkBox_saveXML,
            self.ui.checkBox_saveKeyValue,
            self.ui.checkBox_savePerBoxTxt,
        ):
            layout.removeWidget(box)
        layout.addWidget(self.ui.checkBox_saveJson)
        layout.addWidget(self.ui.checkBox_saveCsv)

        # Collapse less-common formats under "高级"
        self._advanced = QWidget(parent)
        adv_l = QVBoxLayout(self._advanced)
        adv_l.setContentsMargins(12, 0, 0, 0)
        adv_l.setSpacing(2)
        self._btn_toggle_adv = QPushButton(parent)
        self._btn_toggle_adv.setFlat(True)
        self._btn_toggle_adv.setCursor(Qt.PointingHandCursor)
        self._btn_toggle_adv.setStyleSheet("text-align: left; color: #64748b;")
        self._advanced_body = QWidget(self._advanced)
        body_l = QVBoxLayout(self._advanced_body)
        body_l.setContentsMargins(0, 0, 0, 0)
        body_l.setSpacing(2)
        for box in (
            self.ui.checkBox_saveXML,
            self.ui.checkBox_saveKeyValue,
            self.ui.checkBox_savePerBoxTxt,
        ):
            body_l.addWidget(box)
        self._advanced_body.hide()
        adv_l.addWidget(self._btn_toggle_adv)
        adv_l.addWidget(self._advanced_body)
        layout.addWidget(self._advanced)
        self._btn_toggle_adv.clicked.connect(self._toggle_advanced)

        self.btn_copy_json.clicked.connect(self.copy_json_path)
        self.btn_copy_csv.clicked.connect(self.copy_csv_path)
        self.btn_copy_sample.clicked.connect(self.copy_sample_json)
        self.btn_open_folder.clicked.connect(self.open_folder)

        self._installed = True
        self.apply_texts(True)
        self.refresh()

    def _toggle_advanced(self) -> None:
        show = self._advanced_body.isHidden()
        self._advanced_body.setVisible(show)
        self.apply_texts(self._zh)

    def _apply_frame_style(self) -> None:
        if self.frame is None:
            return
        border = "#93c5fd" if self._highlighted else "#3b82f6"
        width = "2px" if self._highlighted else "1px"
        self.frame.setStyleSheet(
            "QFrame#frame_vmixOutboundGuide {"
            f" background-color: #1e3a5f; border: {width} solid {border};"
            " border-radius: 8px; }"
            "QFrame#frame_vmixOutboundGuide QLabel { color: #e8f1ff; }"
            "QFrame#frame_vmixOutboundGuide QPushButton {"
            " background: transparent; color: #dbeafe;"
            " border: 1px solid #60a5fa; border-radius: 4px;"
            " padding: 4px 8px; font-weight: 600; }"
            "QFrame#frame_vmixOutboundGuide QPushButton:hover {"
            " background: #1e40af; }"
            "QFrame#frame_vmixOutboundGuide QPushButton#btn_copyJsonPath {"
            " background: #2563eb; color: white; border: none;"
            " border-radius: 6px; padding: 10px 14px; font-weight: 700; }"
            "QFrame#frame_vmixOutboundGuide QPushButton#btn_copyJsonPath:hover {"
            " background: #1d4ed8; }"
        )

    def set_highlighted(self, on: bool) -> None:
        """Brighten the card when the JSON path button focuses it."""
        self._highlighted = bool(on)
        self._apply_frame_style()

    def apply_texts(self, zh: bool) -> None:
        self._zh = zh
        if not self._installed:
            return
        if zh:
            self.label_title.setText("推荐：JSON → vMix 数据源")
            self.label_steps.setText(
                "<ol style='margin:0 0 0 18px; padding:0;'>"
                "<li>勾选下方「给 vMix 的 JSON（推荐）」——文件写到桌面「光为OCR输出」</li>"
                "<li>打开 <b>vMix → Data Sources → + → JSON</b></li>"
                "<li>点「复制 JSON 路径」粘贴。Update 填 <b>100</b> 毫秒"
                "（填 1000 会一秒一跳）</li>"
                "<li>标题绑定英文列名：<b>home_score、away_score、game_clock、"
                "period、shot_clock、home_name、away_name</b>。"
                "不要再加一堆 Text 源</li>"
                "</ol>"
                "<p style='margin:4px 0 0 0;color:#bfdbfe;'>"
                "列名必须是英文，软件已自动转换。比赛时间和 24 秒的小数会原样写出。"
                "备选才用 CSV（Data Sources → Excel/CSV）。</p>"
            )
            self.btn_copy_json.setText("复制 JSON 路径")
            self.btn_copy_csv.setText("复制 CSV 路径")
            self.btn_copy_sample.setText("复制示例 JSON")
            self.btn_open_folder.setText("打开文件夹")
            self.ui.checkBox_saveJson.setText("给 vMix 的 JSON（推荐）· results.json")
            self.ui.checkBox_saveJson.setToolTip(
                "只写一个 results.json：全部字段在同一行数组里，"
                '格式 [{"home_score":"52","away_score":"55","game_clock":"0:04.9","period":"4","shot_clock":"4.9"}]。'
                "vMix JSON 列名必须是英文（软件已自动转换）；给 Data Sources → JSON 用。"
            )
            self.ui.checkBox_saveCsv.setText("给 vMix 的 CSV · results.csv")
            self.ui.checkBox_saveCsv.setToolTip(
                "首行表头=字段名，第二行=当前值。vMix → Data Sources → Excel/CSV。"
                "追加方式请选「不追加」，只保留最新一行。"
            )
            self.ui.checkBox_saveXML.setText("results.xml（少用）")
            self.ui.checkBox_saveKeyValue.setText("results.txt 键=值（非 vMix 首选）")
            self.ui.checkBox_saveKeyValue.setToolTip(
                "给 SuiteCG / TriCaster 等。vMix 动态数据请用上面的 JSON。"
            )
            self.ui.checkBox_savePerBoxTxt.setText("每个字段单独 .txt（不推荐）")
            self.ui.checkBox_savePerBoxTxt.setToolTip(
                "会冒出很多文件，容易误加成多个 Text 源。vMix 只要一个 results.json。"
            )
            if self.ui.label_7 is not None:
                self.ui.label_7.setText("输出文件夹")
            adv_open = not self._advanced_body.isHidden()
            self._btn_toggle_adv.setText(
                "▾ 高级格式（XML / 键值 / 每字段 txt）"
                if adv_open
                else "▸ 高级格式（一般不用点开）"
            )
        else:
            self.label_steps.setText(
                "<ol style='margin:0 0 0 18px;padding:0;'>"
                "<li>Tick JSON for vMix — files auto-write to Desktop\光为OCR输出</li>"
                "<li>vMix → Data Sources → + → <b>JSON</b></li>"
                "<li>Paste the path (Copy JSON path) · Update <b>100</b> ms (1000 ms = one jump per second even if OCR is fast)</li>"
                "<li>Bind title fields with English columns (home_score, away_score, game_clock, period, shot_clock…) — do not add five Text sources</li>"
                "</ol>"
                "<p style='margin:4px 0 0 0;color:#bfdbfe;'>"
                "Set vMix Update to 100 ms or the clock will jump once a second. vMix JSON keys must be ASCII; the app maps Chinese box names to English automatically. CSV may keep Chinese headers.</p>"
            )
            self.label_title.setText("Recommended: JSON → vMix Data Sources")
            self.btn_copy_json.setText("Copy JSON path")
            self.btn_copy_csv.setText("Copy CSV path")
            self.btn_copy_sample.setText("Copy sample JSON")
            self.btn_open_folder.setText("Open folder")
            self.ui.checkBox_saveJson.setText("JSON for vMix (recommended)")
            self.ui.checkBox_saveCsv.setText("CSV for vMix · results.csv")
            self.ui.checkBox_savePerBoxTxt.setText("Per-field .txt (not recommended)")
            adv_open = not self._advanced_body.isHidden()
            self._btn_toggle_adv.setText(
                "▾ Advanced formats" if adv_open else "▸ Advanced formats"
            )
        self.refresh()

    def _folder(self) -> str | None:
        folder = getattr(self.window, "out_folder", None)
        if folder and os.path.isdir(folder):
            return folder
        text = (self.ui.lineEdit_folder.text() or "").strip()
        return text if text and os.path.isdir(text) else (text or None)

    def json_path(self) -> str | None:
        folder = self._folder()
        return os.path.join(folder, JSON_FILE_NAME) if folder else None

    def csv_path(self) -> str | None:
        folder = self._folder()
        return os.path.join(folder, CSV_FILE_NAME) if folder else None

    def refresh(self, status=None) -> None:
        if not self._installed or self.label_status is None:
            return
        zh = self._zh
        folder = self._folder()
        json_p = self.json_path()
        csv_p = self.csv_path()
        parts = []
        self._set_path_line(json_p, zh)
        if not folder:
            parts.append("还没选输出文件夹" if zh else "No output folder yet")
        else:
            writing = bool(self.ui.checkBox_saveJson.isChecked())
            if writing and json_p and os.path.isfile(json_p):
                try:
                    mtime = os.path.getmtime(json_p)
                    age = time.time() - mtime
                    stamp = datetime.fromtimestamp(mtime).strftime("%H:%M:%S")
                    if zh:
                        parts.append(
                            f"JSON 正在写：{json_p}　最近写入 {stamp}"
                            f"（约 {int(age)} 秒前）"
                        )
                    else:
                        parts.append(f"JSON writing: {json_p} · last {stamp}")
                except OSError:
                    parts.append(
                        f"已勾选 JSON，等待首帧写出：{json_p}"
                        if zh
                        else f"JSON enabled, waiting for first write: {json_p}"
                    )
            elif writing:
                parts.append(
                    f"已勾选 JSON，路径将是：{json_p}"
                    if zh
                    else f"JSON enabled → {json_p}"
                )
            else:
                parts.append(
                    "请勾选「给 vMix 的 JSON（推荐）」" if zh else "Tick JSON for vMix"
                )
            if self.ui.checkBox_saveCsv.isChecked() and csv_p:
                exists = os.path.isfile(csv_p)
                parts.append(
                    (f"CSV：{csv_p}" + ("（已有文件）" if exists else "（尚未写出）"))
                    if zh
                    else f"CSV: {csv_p}"
                )
        if status is not None and getattr(status, "last_error", None):
            parts.append(
                f"写入出错：{status.last_error}"
                if zh
                else f"Write error: {status.last_error}"
            )
        self.label_status.setText("　|　".join(parts) if parts else "")

    def _set_path_line(self, json_p: str | None, zh: bool) -> None:
        if self.label_path is None:
            return
        if json_p:
            self.label_path.setText(f"路径：{json_p}" if zh else f"Path: {json_p}")
        else:
            self.label_path.setText(
                "路径：桌面\\光为OCR输出\\results.json"
                if zh
                else "Path: Desktop\\光为OCR输出\\results.json"
            )

    def _copy(self, text: str, ok_msg: str) -> None:
        if not text:
            QMessageBox.information(
                self.window,
                "光为 OCR" if self._zh else "GuangWei OCR",
                "还没有输出文件夹（应自动是桌面\\光为OCR输出）"
                if self._zh
                else "No output folder yet",
            )
            return
        QGuiApplication.clipboard().setText(text)
        QMessageBox.information(
            self.window,
            "已复制" if self._zh else "Copied",
            ok_msg + "\n\n" + text,
        )

    def copy_json_path(self) -> None:
        path = self.json_path()
        self._copy(
            path or "",
            "已复制 results.json 完整路径。到 vMix → Data Sources → + → JSON 里粘贴。"
            " Update 填 100 毫秒。"
            if self._zh
            else "Copied results.json path. Paste in vMix Data Sources → JSON.",
        )

    def copy_csv_path(self) -> None:
        path = self.csv_path()
        self._copy(
            path or "",
            "已复制 results.csv 路径。到 vMix → Data Sources → + → Excel/CSV 里打开。"
            if self._zh
            else "Copied results.csv path for vMix Excel/CSV.",
        )

    def copy_sample_json(self) -> None:
        # Prefer live values from FileOutput if available
        text = SAMPLE_JSON
        fo = getattr(self.window, "fileOutput", None)
        if fo is not None and getattr(fo, "_boxes", None):
            values = json_row_from_values(fo._all_values())
            if any(values.values()):
                text = json.dumps([values], ensure_ascii=False, indent=2) + "\n"
        QGuiApplication.clipboard().setText(text)
        QMessageBox.information(
            self.window,
            "已复制" if self._zh else "Copied",
            "示例 JSON 已复制（英文列名 + 数组一行对象；vMix 认这个形状）。"
            if self._zh
            else "Sample JSON copied (one-row array).",
        )

    def open_folder(self) -> None:
        folder = self._folder()
        if not folder or not os.path.isdir(folder):
            QMessageBox.information(
                self.window,
                "光为 OCR" if self._zh else "GuangWei OCR",
                "还没有输出文件夹（应自动是桌面\\光为OCR输出）"
                if self._zh
                else "No output folder yet",
            )
            return
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))


def _outbound_button_qss(kind: str, selected: bool, dark: bool) -> str:
    """Both path buttons stay filled and the same size. The ring shows which
    page is open. Blue is JSON; orange is the advanced API."""
    if kind == "json":
        bg, hover = ("#2563eb", "#1d4ed8") if dark else ("#1d4ed8", "#1e3a8a")
        name = "btn_outboundJson"
    else:
        bg, hover = ("#c2410c", "#9a3412")
        name = "btn_outboundApi"
    border = "#f8fafc" if selected else "transparent"
    return (
        f"QPushButton#{name} {{"
        f" background-color: {bg}; color: #ffffff;"
        f" border: 3px solid {border}; border-radius: 8px;"
        " padding: 8px 12px; font-weight: 700; }"
        f"QPushButton#{name}:hover {{ background-color: {hover}; }}"
    )


def focus_json_outbound(ui, guide) -> None:
    """Open the text-files page, tick JSON, and focus Copy JSON path."""
    ui.stackedWidget_outputs.setCurrentWidget(ui.tab_textFiles)
    checkbox = ui.checkBox_saveJson
    if not checkbox.isChecked():
        checkbox.setChecked(True)
    if guide is not None and getattr(guide, "_installed", False):
        guide.set_highlighted(True)
        button = guide.btn_copy_json
        if button is not None:
            button.setFocus(Qt.FocusReason.OtherFocusReason)


def focus_api_outbound(ui, guide=None) -> None:
    """Open the vMix API page and focus the scan button."""
    ui.stackedWidget_outputs.setCurrentWidget(ui.tab_vmix)
    if guide is not None and getattr(guide, "_installed", False):
        guide.set_highlighted(False)
    scan = getattr(ui, "btn_scan_vmix_fields", None)
    if scan is not None:
        scan.setFocus(Qt.FocusReason.OtherFocusReason)


class OutboundPathChooser(QWidget):
    """Two equal buttons at the top of Output: JSON data source, or vMix API."""

    path_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("outboundPathChooser")
        self._zh = True
        self._guard = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 6)
        layout.setSpacing(8)
        self.btn_json = QPushButton(self)
        self.btn_api = QPushButton(self)
        self.btn_json.setObjectName("btn_outboundJson")
        self.btn_api.setObjectName("btn_outboundApi")
        for button in (self.btn_json, self.btn_api):
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(56)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            font = button.font()
            font.setBold(True)
            if font.pointSize() > 0:
                font.setPointSize(min(font.pointSize() + 1, 16))
            button.setFont(font)
            layout.addWidget(button, 1)
        self.btn_json.clicked.connect(lambda: self._choose(JSON_PATH))
        self.btn_api.clicked.connect(lambda: self._choose(API_PATH))
        self.apply_texts(True)

    def _choose(self, path: str) -> None:
        if self._guard:
            return
        self.set_path(path)
        self.path_selected.emit(path)

    def set_path(self, path: str | None) -> None:
        self._guard = True
        try:
            self.btn_json.setChecked(path == JSON_PATH)
            self.btn_api.setChecked(path == API_PATH)
        finally:
            self._guard = False
        self.apply_style()

    def apply_texts(self, zh: bool) -> None:
        self._zh = zh
        if zh:
            self.btn_json.setText("JSON → vMix 数据源\n推荐")
            self.btn_api.setText("进阶：vMix API\n扫描标题字段")
            self.btn_json.setToolTip(
                "推荐。写一个 results.json 到桌面「光为OCR输出」，"
                "vMix 用 Data Sources → JSON 来读。"
                "点这里打开步骤，并勾选「给 vMix 的 JSON」。"
            )
            self.btn_api.setToolTip(
                "可选。标题已经做好、想直接改标题文字时再用。"
                "点这里去填主机、扫描标题字段、核对映射。"
            )
        else:
            self.btn_json.setText("JSON → vMix Data Sources\nRecommended")
            self.btn_api.setText("Advanced: vMix API\nScan title fields")
            self.btn_json.setToolTip(
                "Recommended. Write results.json and open the copy-path steps."
            )
            self.btn_api.setToolTip(
                "Optional. Scan a vMix title and map fields with the Web API."
            )
        self.apply_style()

    def apply_style(self) -> None:
        dark = is_dark(self.palette())
        self.btn_json.setStyleSheet(
            _outbound_button_qss("json", self.btn_json.isChecked(), dark)
        )
        self.btn_api.setStyleSheet(
            _outbound_button_qss("api", self.btn_api.isChecked(), dark)
        )

    def sync_from_page(self, page) -> None:
        name = "" if page is None else page.objectName()
        if name == "tab_textFiles":
            self.set_path(JSON_PATH)
        elif name == "tab_vmix":
            self.set_path(API_PATH)
        else:
            self.set_path(None)
