"""光为 OCR：强制分步首次向导（第1/2/3/4步，必须点完）。

接入：
- MainWindow.__init__ 末尾在 setupViewer 之后调用 maybe_show_cn_first_run_wizard(self)
- 仅 zh 语言且 scoresight.json 无 cn_wizard_seen 时弹出
- 用户走完最后一步「完成」才写入 cn_wizard_seen=True；中途不能叉掉跳过
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QCoreApplication
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QWizard,
    QWizardPage,
    QLabel,
    QVBoxLayout,
    QCheckBox,
)

from product_brand import APP_DISPLAY_NAME
from storage import fetch_data, store_data

CN_WIZARD_SEEN = "cn_wizard_seen"


def _tr(s: str) -> str:
    return QCoreApplication.translate("CnFirstRunWizard", s)


class _StepPage(QWizardPage):
    def __init__(self, step: int, total: int, title: str, body_html: str, parent=None):
        super().__init__(parent)
        self.setTitle(_tr("第 {n} 步 / 共 {total} 步").format(n=step, total=total))
        self.setSubTitle(title)
        lay = QVBoxLayout(self)
        lab = QLabel(body_html, self)
        lab.setWordWrap(True)
        lab.setTextFormat(Qt.TextFormat.RichText)
        lab.setOpenExternalLinks(True)
        lab.setAlignment(Qt.AlignmentFlag.AlignTop)
        lay.addWidget(lab)
        lay.addStretch(1)


class CnFirstRunWizard(QWizard):
    """Forced step-by-step Chinese onboarding (no skip, no Esc dismiss)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_tr("{app} — 使用向导").format(app=APP_DISPLAY_NAME))
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoCancelButton, True)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.setMinimumWidth(560)
        self.setMinimumHeight(420)
        # Remove window close button where possible
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)

        pages = [
            (
                _tr("选视频源（采集卡）"),
                _tr(
                    "<p><b>请先接好摄像机 → 采集卡 → 本电脑。</b></p>"
                    "<ol>"
                    "<li>关闭占用采集卡的 OBS / 导播软件（一块卡同时只能被一个程序用）。</li>"
                    "<li>回到主界面后，顶部「源」选你的采集卡（DirectShow）。</li>"
                    "<li>没有设备：按 F5 刷新；分辨率优先 720p/1080p，格式优先 <b>MJPG</b>。</li>"
                    "<li>也可先用「视频文件」打开样片练手。</li>"
                    "</ol>"
                    "<p>点「下一步」继续。</p>"
                ),
            ),
            (
                _tr("四角校正画面"),
                _tr(
                    "<p><b>牌面歪了一定要先校正，不然识别会飘。</b></p>"
                    "<ol>"
                    "<li>画面上方工具栏点「四角校正」。</li>"
                    "<li>依次点击记分牌的四个角，把画面拉正。</li>"
                    "<li>数字尽量端正、别被边框切掉。</li>"
                    "</ol>"
                    "<p>点「下一步」继续。</p>"
                ),
            ),
            (
                _tr("框选要比的分"),
                _tr(
                    "<p><b>用鼠标在画面上拖框。</b></p>"
                    "<ol>"
                    "<li>左边字段点「选框」，再到画面上拖框（例如「主队得分」）。</li>"
                    "<li>在预览画面上按住鼠标拖出框，罩住对应数字。</li>"
                    "<li>同样框：客队得分、比赛时间、节次、犯规、24秒等。</li>"
                    "<li>OCR 模型建议选 <b>PP-OCR accurate</b>；LED 防跳分默认已开（堆栈 4 / 200ms）。</li>"
                    "</ol>"
                    "<p>点「下一步」继续。</p>"
                ),
            ),
            (
                _tr("看结果 / 接到 vMix"),
                _tr(
                    "<p><b>识别结果可以本地查看，也可推到 vMix。</b></p>"
                    "<ol>"
                    "<li>浏览器打开 "
                    "<code>http://localhost:18099/json?pivot</code> 看实时 JSON。</li>"
                    "<li>菜单「工具」里开 vMix 输出（默认 API 端口 8088），用 SetText。</li>"
                    "<li>网站/导播对接目前是<strong>预留</strong>，默认不打开。</li>"
                    "</ol>"
                    "<p>链路：<b>摄像机 → 采集卡 → {app} → vMix / JSON</b></p>"
                    "<p>点「完成」进入软件。向导只在首次强制走一遍。</p>"
                ).format(app=APP_DISPLAY_NAME),
            ),
        ]
        for i, (title, body) in enumerate(pages, 1):
            self.addPage(_StepPage(i, len(pages), title, body, self))

        self.setButtonText(QWizard.WizardButton.NextButton, _tr("下一步"))
        self.setButtonText(QWizard.WizardButton.BackButton, _tr("上一步"))
        self.setButtonText(QWizard.WizardButton.FinishButton, _tr("完成，开始使用"))

        self._finished_ok = False
        self.finished.connect(self._on_finished)

    def _on_finished(self, result: int) -> None:
        if result == QWizard.DialogCode.Accepted:
            self._finished_ok = True

    def reject(self) -> None:
        # Block Esc / reject until finished
        if self.currentId() < self.pageIds()[-1]:
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._finished_ok and self.currentId() < self.pageIds()[-1]:
            event.ignore()
            return
        super().closeEvent(event)

    def should_persist_seen(self) -> bool:
        return self._finished_ok


def maybe_show_cn_first_run_wizard(main_window) -> bool:
    """Show forced wizard once for Chinese locale. Returns True if shown."""
    if fetch_data("scoresight.json", CN_WIZARD_SEEN, False):
        return False
    locale = ""
    try:
        locale = main_window.currentLocale()
    except Exception:
        locale = fetch_data("scoresight.json", "language") or ""
    if not str(locale).lower().startswith("zh"):
        return False
    dlg = CnFirstRunWizard(main_window)
    dlg.exec()
    if dlg.should_persist_seen():
        store_data("scoresight.json", CN_WIZARD_SEEN, True)
    return True
