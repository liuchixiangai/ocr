# 参与贡献 / Contributing

感谢你改进光为 OCR。终端用户的问题可以进 QQ 群 **124356408**。代码、文档和缺陷请走 GitHub：<https://github.com/liuchixiangai/ocr>。

参与即表示你同意 [Code of Conduct](CODE_OF_CONDUCT.md)。贡献以 [MIT](LICENSE) 授权。安全漏洞不要开公开 Issue，见 [SECURITY.md](SECURITY.md)。

本仓库是 [ScoreSight](https://github.com/royshil/scoresight) 的中文分支。改动应保留上游版权声明。不要把 ScoreSight 的 issue 编号当作本仓库的 issue。

## 可以怎么帮忙

- 用 [bug 模板](https://github.com/liuchixiangai/ocr/issues/new/choose) 报告缺陷，并附上日志。
- 改进 `docs/`，尤其是中文说明。
- 提供认错的记分牌截图或短视频（不要包含不必要的个人信息）。
- 小修复可以直接开 Pull Request。大改动请先开 Issue，写明直播现场要解决的问题。

## 开发环境

需要 Python **3.13**、git。Windows 上还需要微软 C++ 生成工具。建议用 [uv](https://docs.astral.sh/uv/)，用 `venv` + `pip` 也可以。

```powershell
git clone https://github.com/liuchixiangai/ocr.git
cd ocr
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt -r requirements-win.txt
python scripts/compile_ui.py
python src\win32DeviceEnum\setup.py build_ext --inplace
python scripts/fetch_models.py
python src\main.py --profile dev
```

`python scripts/fetch_models.py` 下载的是 PP-OCR 权重，不在 git 里。Tesseract 的 `tesseract/tessdata/*.traineddata` **也不在本仓库**。只跑 PP-OCR 可以先不复制；要跑 Tesseract 测试或 PyInstaller，需从 ScoreSight 检出或本地打包机复制这四个 `.traineddata`。详见 [README](README.md)。

macOS / Linux 不是这个产品的发行平台。上游命令仍可用（`requirements-mac.txt`，Linux 只装 `requirements-dev.txt`），但不作为光为 OCR 的支持范围。

## 提交代码

1. 从 `main` 拉分支，一个 PR 做一件事。
2. 用 ruff 和现有测试检查你改动的部分：

   ```bash
   ruff check .
   ruff format --check .
   python scripts/compile_ui.py --check
   python -m pytest -m "not ocr and not ppocr and not qt"
   ```

3. 不要提交 `.venv/`、`dist/`、`build/`、`ocr_eval/`、`ocr_models` 里的 onnx、`tesseract/`、`.env`、`.pfx`、`.p12`。
4. `src/ui_*.py` 由 `src/*.ui` 生成。改界面请改 `.ui`，再运行 `python scripts/compile_ui.py`。
5. 用户能看见的行为变化，在 [CHANGELOG.md](CHANGELOG.md) 的 `## [Unreleased]` 写一笔。
6. PR 说明请用中文或中英双语，写清在 Windows 上怎么验证。

`src/` 里的模块按文件名互相导入（`from storage import fetch_data`）。日志用 `from sc_logging import logger`。打包数据走 `resource_path(...)`，并让 `scoresight.spec` 的 `DATA_GLOBS` 能匹配到。

## 代码签名

不要在 PR 里加入签名证书、HookSigntool，或把 SignPath 步骤改成“已经可用”。政策在 [Code signing policy](docs/code-signing-policy.md)。申请草稿在 [docs/SIGNPATH_APPLICATION.md](docs/SIGNPATH_APPLICATION.md)。只有维护者 [liuchixiangai](https://github.com/liuchixiangai) 能批准将来的签名请求，并且必须先打开 GitHub 和 SignPath 的 MFA。
