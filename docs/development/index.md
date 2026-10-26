---
title: Developer Guide
nav_order: 9
has_children: true
description: "Build ScoreSight from source, understand its architecture, run tests, translate it and cut releases."
---

# Developer Guide

ScoreSight is a Python 3.13 desktop app built with PySide6 (Qt 6), OpenCV and Tesseract (via tesserocr). These pages are for contributors and packagers:

- [Architecture](architecture.md): how frames flow from the source through OCR to the outputs, and a map of the modules.
- [Building from Source](building.md): set up a development environment with uv, run the app, and package it with PyInstaller.
- [Tests & Code Style](testing.md): pytest, the self-test, ruff and pre-commit.
- [Translations](translations.md): update and compile the Qt translation files, and add a language.
- [Releasing](releasing.md): versioning, the changelog and the tag-triggered release workflow.
- [OCR Evaluation](ocr-eval.md): measure OCR engines on real scoreboard crops against a baseline, add an engine or contribute data.

Also read [CONTRIBUTING.md](https://github.com/royshil/scoresight/blob/main/CONTRIBUTING.md) before opening a pull request.

## TL;DR

```bash
git clone https://github.com/royshil/scoresight.git
cd scoresight
uv venv --python 3.13
# activate: .venv\Scripts\activate (Windows) or source .venv/bin/activate (macOS/Linux)
uv pip install -r requirements-dev.txt -r requirements-win.txt   # Windows
uv pip install -r requirements-dev.txt -r requirements-mac.txt   # macOS
uv pip install -r requirements-dev.txt                           # Linux
python scripts/compile_ui.py
python src/main.py
```

On Windows you also need to build the native camera enumerator once (see [Building from Source](building.md#windows-build-the-camera-enumerator)).
