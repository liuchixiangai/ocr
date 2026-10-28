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
# The fetched harness was written for upstream ScoreSight. Three eval-only
# adjustments make its baselines comparable on this fork; none of them
# change the app:
#   * score boxes wait led_stable_ms before a first value, and a still crop
#     never gets a second reading, so that debounce and the LED frame stack
#     are turned off (the harness already disables smoothing and skip-similar);
#   * format_prefixes[11] is "Any text" upstream and "Any number" here, so
#     text crops are scored with ^.*$ instead of a digits-only expression;
#   * a box named Period uses the Chinese period whitelist (a lone O becomes
#     OT). The baselines were read as digits, so the detector name is not
#     "Period". The Period preset's settings are unchanged.
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
original = text

led_needle = '    "composite_box": False,\n}'
led_insert = """    "composite_box": False,
    # This fork waits led_stable_ms before a score box's first value.
    # One still crop has no later reading, so do not debounce it here.
    "led_stack_frames": 0,
    "led_stable_ms": 0,
}"""
if '"led_stable_ms"' not in text:
    if led_needle not in text:
        raise SystemExit(
            "ocr_eval/fields.py: EVAL_SETTING_OVERRIDES changed; not patching"
        )
    text = text.replace(led_needle, led_insert, 1)

any_text = 'settings["format_regex"] = defaults.format_prefixes[11]  # Any text'
any_text_fixed = 'settings["format_regex"] = "^.*$"  # Any text'
if any_text in text:
    text = text.replace(any_text, any_text_fixed, 1)
elif any_text_fixed not in text:
    raise SystemExit("ocr_eval/fields.py: Any-text format line changed; not patching")

period_name = '''def box_name(field_type: str, role: str | None = None) -> str:
    return (
        role_box(field_type, role) or _BOX_NAMES.get(field_type) or f"Eval {field_type}"
    )'''
period_name_fixed = '''def box_name(field_type: str, role: str | None = None) -> str:
    name = (
        role_box(field_type, role) or _BOX_NAMES.get(field_type) or f"Eval {field_type}"
    )
    # Upstream baselines read a period as digits. This fork treats the name
    # "Period" as a Chinese period box and can publish "OT" for a digit.
    if name == "Period":
        return "Eval value"
    return name'''
if period_name in text:
    text = text.replace(period_name, period_name_fixed, 1)
elif "return \"Eval value\"" not in text:
    raise SystemExit("ocr_eval/fields.py: box_name() changed; not patching")

if text != original:
    path.write_text(text)
PY

test -f ocr_eval/__main__.py
test -f ocr_eval/baselines/tesseract.json
if [ "$WITH_TESSDATA" -eq 1 ]; then
  test -f tesseract/tessdata/scoreboard_general.traineddata
fi
