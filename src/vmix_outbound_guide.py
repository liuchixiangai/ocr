# -*- coding: utf-8 -*-
"""vMix-friendly outbound guide card for the Text-files destination.

Injected at runtime into tab_textFiles: big steps, one-click copy for
results.json / results.csv paths, sample JSON, and a live write status.
Does not change how FileOutput writes; it only guides the user.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from file_output import CSV_FILE_NAME, JSON_FILE_NAME, json_row_from_values

SAMPLE_JSON = (
    "[\n"
    "  {\n"
    '    "home_score": "52",\n'
    '    "away_score": "55",\n'
    '    "game_clock": "01:16.7",\n'
    '    "period": "4",\n'
    '    "shot_clock": "10"\n'
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
        self._advanced: QWidget | None = None

    def install(self) -> None:
        if self._installed:
            return
        parent = self.ui.tab_textFiles
        layout = self.ui.horizontalLayout_14  # VBox of format checkboxes

        self.frame = QFrame(parent)
        self.frame.setObjectName("frame_vmixOutboundGuide")
        self.frame.setFrameShape(QFrame.StyledPanel)
        self.frame.setStyleSheet(
            "QFrame#frame_vmixOutboundGuide {"
            " background-color: #1e3a5f; border: 1px solid #3b82f6;"
            " border-radius: 6px; padding: 6px; }"
            "QFrame#frame_vmixOutboundGuide QLabel { color: #e8f1ff; }"
            "QFrame#frame_vmixOutboundGuide QPushButton {"
            " background: #2563eb; color: white; border: none;"
            " border-radius: 4px; padding: 6px 10px; font-weight: 600; }"
            "QFrame#frame_vmixOutboundGuide QPushButton:hover { background: #1d4ed8; }"
        )
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

        row = QHBoxLayout()
        row.setSpacing(6)
        self.btn_copy_json = QPushButton(self.frame)
        self.btn_copy_csv = QPushButton(self.frame)
        self.btn_copy_sample = QPushButton(self.frame)
        self.btn_open_folder = QPushButton(self.frame)
        for b in (
            self.btn_copy_json,
            self.btn_copy_csv,
            self.btn_copy_sample,
            self.btn_open_folder,
        ):
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

    def apply_texts(self, zh: bool) -> None:
        self._zh = zh
        if not self._installed:
            return
        if zh:
            self.label_title.setText("给 vMix 用：一个 JSON 就够（推荐）")
            self.label_steps.setText(
                "<ol style='margin:0 0 0 18px; padding:0;'>"
                "<li>勾选下方「给 vMix 的 JSON（推荐）」——文件自动写到桌面「光为OCR输出」</li>"
                "<li>打开 <b>vMix → Data Sources → + → JSON</b></li>"
                "<li>粘贴路径（点下面「一键复制 JSON 路径」）→ Update 填 <b>100</b> 毫秒（不要填 1000，否则软件再快也会一秒一跳）</li>"
                "<li>标题里用 Data Source 的英文列名绑定：<b>home_score / away_score / game_clock / period / home_fouls / away_fouls / shot_clock</b>——<b>不要</b>再加五个 Text 源</li>"
                "</ol>"
                "<p style='margin:4px 0 0 0;color:#bfdbfe;'>"
                "vMix 刷新建议改成 100ms，否则软件再快也会一秒一跳。说明：vMix 的 JSON 列名必须是英文，软件已自动把「主队得分」等写成 home_score 等；CSV 仍可用中文表头。"
                "备选：勾选 CSV 后，vMix 用 "
                "<b>Data Sources → + → Excel/CSV/Google Sheets</b> 读 results.csv"
                "（追加方式选「不追加」）。</p>"
            )
            self.btn_copy_json.setText("一键复制 JSON 路径")
            self.btn_copy_csv.setText("复制 CSV 路径")
            self.btn_copy_sample.setText("复制示例 JSON")
            self.btn_open_folder.setText("打开文件夹")
            self.ui.checkBox_saveJson.setText("给 vMix 的 JSON（推荐）· results.json")
            self.ui.checkBox_saveJson.setToolTip(
                "只写一个 results.json：全部字段在同一行数组里，"
                '格式 [{"home_score":"52","away_score":"55","game_clock":"01:16.7","period":"4","shot_clock":"10"}]。'
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
            self.label_title.setText("vMix: one JSON file is enough")
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
