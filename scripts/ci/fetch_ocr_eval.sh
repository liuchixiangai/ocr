#!/usr/bin/env bash
# Put the OCR evaluation harness where `python -m ocr_eval` can import it.
#
# ocr_eval is a package directory at the repository root. Running it as a
# module already searches the working directory, which is also how pytest
# sees it (pythonpath = "." in pyproject.toml). This portable tree lists
# ocr_eval/ in .gitignore and pyproject.toml has no project table, so
# `pip install -e .` cannot install the module.
#
# The harness and its datasets/baselines are fetched from the upstream
# commit they were published with. Pass --with-tessdata on the Tesseract
# job: those models live in tesseract/tessdata, which this tree also
# gitignores, and the Tesseract adapter refuses to load without them.
#
# Score boxes in this fork wait led_stable_ms before publishing a first
# value. A still crop never gets a second reading, so the fetched harness
# is told to turn that debounce (and the LED frame stack) off. That is an
# eval-only override of the multi-frame settings the harness already
# disables (smoothing, skip-similar); it does not change the app.
set -euo pipefail

REV="1db9b218f44547079c44ba56c05e8345bbd6ce9e"
REPO="https://github.com/royshil/scoresight.git"
WITH_TESSDATA=0
if [ "${1:-}" = "--with-tessdata" ]; then
  WITH_TESSDATA=1
elif [ -n "${1:-}" ]; then
  echo "usage: scripts/ci/fetch_ocr_eval.sh [--with-tessdata]" >&2
  exit 2
fi

need_eval=0
need_tess=0
if [ ! -f ocr_eval/__main__.py ]; then
  need_eval=1
fi
if [ "$WITH_TESSDATA" -eq 1 ] && [ ! -f tesseract/tessdata/scoreboard_general.traineddata ]; then
  need_tess=1
fi

if [ "$need_eval" -eq 1 ] || [ "$need_tess" -eq 1 ]; then
  dest="$(mktemp -d)"
  trap 'rm -rf "$dest"' EXIT
  git -c init.defaultBranch=main init "$dest"
  git -C "$dest" remote add origin "$REPO"
  git -C "$dest" sparse-checkout init --cone
  sparse=(ocr_eval)
  if [ "$need_tess" -eq 1 ]; then
    sparse+=(tesseract)
  fi
  # An eval that is already on disk is not fetched again.
  if [ "$need_eval" -eq 0 ]; then
    sparse=(tesseract)
  fi
  git -C "$dest" sparse-checkout set "${sparse[@]}"
  git -C "$dest" fetch --filter=blob:none --depth 1 origin "$REV"
  git -C "$dest" checkout --detach FETCH_HEAD
  if [ "$need_eval" -eq 1 ]; then
    rm -rf ocr_eval
    mv "$dest/ocr_eval" ocr_eval
  fi
  if [ "$need_tess" -eq 1 ]; then
    mkdir -p tesseract
    rm -rf tesseract/tessdata
    mv "$dest/tesseract/tessdata" tesseract/tessdata
  fi
  rm -rf "$dest"
  trap - EXIT
fi

python3 - << 'PY'
from pathlib import Path

path = Path("ocr_eval/fields.py")
text = path.read_text()
if '"led_stable_ms"' in text:
    raise SystemExit(0)
needle = '    "composite_box": False,\n}'
insert = """    "composite_box": False,
    # This fork waits led_stable_ms before a score box's first value.
    # One still crop has no later reading, so do not debounce it here.
    "led_stack_frames": 0,
    "led_stable_ms": 0,
}"""
if needle not in text:
    raise SystemExit(
        "ocr_eval/fields.py: EVAL_SETTING_OVERRIDES changed; not patching"
    )
path.write_text(text.replace(needle, insert, 1))
PY

test -f ocr_eval/__main__.py
test -f ocr_eval/baselines/tesseract.json
if [ "$WITH_TESSDATA" -eq 1 ]; then
  test -f tesseract/tessdata/scoreboard_general.traineddata
fi
