"""光为 OCR ·「数据口」设置对话框（中文）。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)

from data_ingest import (
    KEY_HEX_LOG,
    KEY_MODE,
    KEY_PARSER,
    KEY_SERIAL_BAUD,
    KEY_SERIAL_PORT,
    KEY_TCP_HOST,
    KEY_TCP_PORT,
    KEY_TRANSPORT,
    MODE_DATA_ONLY,
    MODE_DATA_PREFER,
    MODE_OCR_ONLY,
    TRANSPORT_SERIAL,
    TRANSPORT_TCP_CLIENT,
    TRANSPORT_TCP_SERVER,
    DataIngestManager,
    JSON_FRAME_DOC,
)


class DataIngestDialog(QDialog):
    """工具 → 数据口…"""

    def __init__(self, manager: DataIngestManager, parent=None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle("数据口接收")
        self.setMinimumWidth(520)
        self.setMinimumHeight(480)
        self._build()
        self._load_from_manager()
        self._wire()
        self._refresh_transport_enabled()
        self._refresh_running_ui()

    def _build(self) -> None:
        root = QVBoxLayout(self)

        tip = QLabel(
            "不方便采集视频时，可从记分台串口/网口收比分与时间"
            "（有人 USR-W610 等透传也可）。与摄像头 OCR 两条路并存。"
        )
        tip.setWordWrap(True)
        root.addWidget(tip)

        # 模式
        mode_box = QGroupBox("与 OCR 并存策略")
        mode_form = QFormLayout(mode_box)
        self.combo_mode = QComboBox()
        self.combo_mode.addItem("仅摄像头 OCR", MODE_OCR_ONLY)
        self.combo_mode.addItem("仅数据口", MODE_DATA_ONLY)
        self.combo_mode.addItem("数据口优先（有数据就覆盖对应字段）", MODE_DATA_PREFER)
        mode_form.addRow("模式", self.combo_mode)
        root.addWidget(mode_box)

        # 传输
        tx_box = QGroupBox("输入方式")
        tx_form = QFormLayout(tx_box)
        self.combo_transport = QComboBox()
        self.combo_transport.addItem(
            "TCP 服务端（本机听端口，W610 作 Client 连入）", TRANSPORT_TCP_SERVER
        )
        self.combo_transport.addItem(
            "TCP 客户端（本机去连对方推送）", TRANSPORT_TCP_CLIENT
        )
        self.combo_transport.addItem(
            "串口 COM（USB 转 RS232/485）", TRANSPORT_SERIAL
        )
        tx_form.addRow("传输", self.combo_transport)

        self.edit_host = QLineEdit()
        self.edit_host.setPlaceholderText("服务端用 0.0.0.0；客户端填对方 IP")
        tx_form.addRow("主机", self.edit_host)

        self.spin_port = QSpinBox()
        self.spin_port.setRange(1, 65535)
        self.spin_port.setValue(5000)
        tx_form.addRow("端口", self.spin_port)

        self.edit_com = QLineEdit()
        self.edit_com.setPlaceholderText("例如 COM3")
        tx_form.addRow("COM 口", self.edit_com)

        self.spin_baud = QSpinBox()
        self.spin_baud.setRange(300, 921600)
        self.spin_baud.setValue(9600)
        tx_form.addRow("波特率", self.spin_baud)

        self.combo_parser = QComboBox()
        self.combo_parser.addItem("光为标准 JSON", "json")
        self.combo_parser.addItem("仅原始抓包（十六进制日志）", "raw")
        self.combo_parser.addItem("Bodet（占位，尚未解析）", "bodet_stub")
        tx_form.addRow("协议", self.combo_parser)

        self.check_hex = QCheckBox("把原始字节记到日志（十六进制，方便对协议）")
        self.check_hex.setChecked(True)
        tx_form.addRow("", self.check_hex)
        root.addWidget(tx_box)

        # 启停
        btn_row = QHBoxLayout()
        self.btn_start = QPushButton("启动接收")
        self.btn_stop = QPushButton("停止")
        self.btn_save = QPushButton("保存设置")
        self.btn_help = QPushButton("JSON 帧说明")
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_stop)
        btn_row.addWidget(self.btn_save)
        btn_row.addWidget(self.btn_help)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        self.label_status = QLabel("状态：—")
        self.label_status.setWordWrap(True)
        root.addWidget(self.label_status)

        self.text_raw = QTextEdit()
        self.text_raw.setReadOnly(True)
        self.text_raw.setPlaceholderText("最近收到的一行原文会显示在这里…")
        self.text_raw.setMaximumHeight(120)
        root.addWidget(QLabel("最近收到："))
        root.addWidget(self.text_raw)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_btn:
            close_btn.setText("关闭")
        root.addWidget(buttons)

    def _load_from_manager(self) -> None:
        cfg = self.manager.config
        self._set_combo_data(self.combo_mode, cfg.get(KEY_MODE, MODE_OCR_ONLY))
        self._set_combo_data(
            self.combo_transport, cfg.get(KEY_TRANSPORT, TRANSPORT_TCP_SERVER)
        )
        self.edit_host.setText(str(cfg.get(KEY_TCP_HOST, "0.0.0.0")))
        self.spin_port.setValue(int(cfg.get(KEY_TCP_PORT, 5000)))
        self.edit_com.setText(str(cfg.get(KEY_SERIAL_PORT, "COM3")))
        self.spin_baud.setValue(int(cfg.get(KEY_SERIAL_BAUD, 9600)))
        self._set_combo_data(self.combo_parser, cfg.get(KEY_PARSER, "json"))
        self.check_hex.setChecked(bool(cfg.get(KEY_HEX_LOG, True)))
        if self.manager.latest_raw:
            self.text_raw.setPlainText(self.manager.latest_raw)
        self.label_status.setText(f"状态：{self.manager.status}")

    @staticmethod
    def _set_combo_data(combo: QComboBox, data) -> None:
        for i in range(combo.count()):
            if combo.itemData(i) == data:
                combo.setCurrentIndex(i)
                return

    def _wire(self) -> None:
        self.combo_transport.currentIndexChanged.connect(self._refresh_transport_enabled)
        self.btn_save.clicked.connect(self._save)
        self.btn_start.clicked.connect(self._start)
        self.btn_stop.clicked.connect(self._stop)
        self.btn_help.clicked.connect(self._show_help)
        self.manager.status_changed.connect(self._on_status)
        self.manager.error_occurred.connect(self._on_error)
        self.manager.fields_updated.connect(self._on_fields)

    def _refresh_transport_enabled(self) -> None:
        t = self.combo_transport.currentData()
        is_serial = t == TRANSPORT_SERIAL
        self.edit_host.setEnabled(not is_serial)
        self.spin_port.setEnabled(not is_serial)
        self.edit_com.setEnabled(is_serial)
        self.spin_baud.setEnabled(is_serial)

    def _collect(self) -> dict:
        return {
            KEY_MODE: self.combo_mode.currentData(),
            KEY_TRANSPORT: self.combo_transport.currentData(),
            KEY_TCP_HOST: self.edit_host.text().strip() or "0.0.0.0",
            KEY_TCP_PORT: int(self.spin_port.value()),
            KEY_SERIAL_PORT: self.edit_com.text().strip() or "COM3",
            KEY_SERIAL_BAUD: int(self.spin_baud.value()),
            KEY_PARSER: self.combo_parser.currentData(),
            KEY_HEX_LOG: self.check_hex.isChecked(),
        }

    def _save(self) -> None:
        self.manager.update_config(**self._collect())
        self.label_status.setText("状态：设置已保存")

    def _start(self) -> None:
        self.manager.update_config(**self._collect())
        self.manager.start()
        self._refresh_running_ui()

    def _stop(self) -> None:
        self.manager.stop()
        self._refresh_running_ui()

    def _refresh_running_ui(self) -> None:
        running = self.manager.running
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)

    def _on_status(self, msg: str) -> None:
        self.label_status.setText(f"状态：{msg}")
        self._refresh_running_ui()

    def _on_error(self, msg: str) -> None:
        self.label_status.setText(f"状态：{msg}")
        self._refresh_running_ui()

    def _on_fields(self, _fields: dict, raw_line: str) -> None:
        if raw_line:
            self.text_raw.setPlainText(raw_line)

    def _show_help(self) -> None:
        QMessageBox.information(self, "光为标准 JSON 帧", JSON_FRAME_DOC.strip())
