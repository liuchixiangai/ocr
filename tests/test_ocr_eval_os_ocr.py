"""ocr_eval: the preprocessing and word assembly shared by the operating
systems' OCR adapters (ocr_eval/engines/_os_ocr.py). No OCR engine needed."""

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from ocr_eval.engines import _os_ocr  # noqa: E402
from ocr_eval.engines._os_ocr import Word  # noqa: E402


def _crop(height=20, width=30, background=(10, 20, 30), text=(250, 250, 250)):
    crop = np.empty((height, width, 3), np.uint8)
    crop[:] = background
    crop[height // 4 : -height // 4, width // 4 : -width // 4] = text
    crop.flags.writeable = False
    return crop


def test_variant_names_and_parsing():
    assert _os_ocr.variant_names() == (
        "raw",
        "raw-digits",
        "upscale",
        "upscale-digits",
        "tile",
        "tile-digits",
    )
    names = _os_ocr.variant_names(("accurate", "fast"))
    assert len(names) == 12
    assert names[0] == "accurate-raw" and names[-1] == "fast-tile-digits"
    assert _os_ocr.parse_variant("tile-digits") == _os_ocr.VariantSpec(
        None, "tile", True
    )
    assert _os_ocr.parse_variant("accurate-upscale") == _os_ocr.VariantSpec(
        "accurate", "upscale", False
    )
    for bad in ("digits", "accurate-bogus", ""):
        with pytest.raises(ValueError):
            _os_ocr.parse_variant(bad)
    assert _os_ocr.describe(_os_ocr.parse_variant("tile")) == {
        "preprocessing": "tile",
        "digit_lookalikes": False,
        "upscale_height": _os_ocr.UPSCALE_HEIGHT,
        "margin": _os_ocr.MARGIN,
        "copies": _os_ocr.TILE_COPIES,
        "gap": _os_ocr.TILE_GAP,
    }


def test_raw_keeps_the_crop():
    crop = _crop()
    prepared = _os_ocr.prepare(crop, "raw")
    assert np.array_equal(prepared.image, crop)
    assert prepared.copies == ((0.0, 30.0),)


def test_upscale_scales_small_crops_and_adds_a_margin_in_the_border_colour():
    crop = _crop(height=20, width=30)
    prepared = _os_ocr.prepare(crop, "upscale", height=64, margin=0.5)
    # 20x30 -> 64x96, plus 32 px on every side
    assert prepared.image.shape == (128, 160, 3)
    assert prepared.image.flags.c_contiguous and prepared.image.dtype == np.uint8
    assert prepared.copies == ((32.0, 128.0),)
    assert tuple(prepared.image[0, 0]) == (10, 20, 30)
    assert tuple(prepared.image[64, 80]) == (250, 250, 250)
    assert not crop.flags.writeable  # the read-only input is untouched


def test_upscale_leaves_tall_crops_at_their_size():
    crop = _crop(height=100, width=40)
    prepared = _os_ocr.prepare(crop, "upscale", height=64, margin=0.25)
    assert prepared.image.shape == (150, 90, 3)
    assert np.array_equal(prepared.image[25:125, 25:65], crop)


def test_tile_places_copies_side_by_side():
    crop = _crop(height=64, width=40)
    prepared = _os_ocr.prepare(crop, "tile", height=64, margin=0.5, copies=3, gap=0.5)
    # margins 32, copies 40 wide, gaps 32
    assert prepared.copies == ((32.0, 72.0), (104.0, 144.0), (176.0, 216.0))
    assert prepared.image.shape == (128, 248, 3)
    for start, end in prepared.copies:
        assert np.array_equal(prepared.image[32:96, int(start) : int(end)], crop)


def test_images_larger_than_the_engine_limit_are_scaled_down():
    crop = _crop(height=100, width=400)
    prepared = _os_ocr.prepare(crop, "tile", margin=0.5, max_dimension=1000)
    assert max(prepared.image.shape[:2]) == 1000
    assert prepared.copies[-1][1] < 1000


def test_tiny_crops_can_be_prepared():
    crop = np.zeros((1, 1, 3), np.uint8)
    for preprocessing in _os_ocr.PREPROCESSING:
        image = _os_ocr.prepare(crop, preprocessing).image
        assert image.ndim == 3 and image.shape[2] == 3


def test_fix_digits_only_touches_numeric_fields():
    assert _os_ocr.fix_digits("lO:S7", "clock") == "10:57"
    assert _os_ocr.fix_digits("B", "score") == "8"
    assert _os_ocr.fix_digits("33,9", "number") == "33.9"
    assert _os_ocr.fix_digits("BOS", "text") == "BOS"
    assert _os_ocr.fix_digits("BOS", "other") == "BOS"


def test_join_words_reads_lines_top_to_bottom_and_words_left_to_right():
    words = [
        Word("AWAY", 60, 2, 40, 20),
        Word("HOME", 0, 0, 50, 20),
        Word("12", 5, 40, 20, 20),
        Word("  ", 0, 80, 5, 5),
    ]
    assert _os_ocr.join_words(words) == "HOME AWAY 12"
    assert _os_ocr.join_words([]) == ""


def test_assemble_single_copy_with_confidence():
    prepared = _os_ocr.prepare(_crop(), "upscale")
    words = [Word("3", 50, 40, 10, 30, 0.9), Word("1", 30, 40, 10, 30, 0.5)]
    assert _os_ocr.assemble(words, prepared, "score") == ("1 3", 0.5, None)
    assert _os_ocr.assemble([Word("O", 1, 1, 9, 9)], prepared, "score", True) == (
        "0",
        None,
        None,
    )
    assert _os_ocr.assemble([], prepared, "score") == ("", None, None)


def _tiled():
    return _os_ocr.prepare(
        _crop(height=64, width=40), "tile", margin=0.5, copies=3, gap=0.5
    )


def test_assemble_votes_over_the_copies():
    prepared = _tiled()  # copies at x 32-72, 104-144, 176-216
    words = [
        Word("12", 34, 30, 36, 40, 0.4),
        Word("17", 106, 30, 36, 40, 0.8),
        Word("12", 178, 30, 36, 40, 0.6),
    ]
    text, confidence, info = _os_ocr.assemble(words, prepared, "score")
    assert (text, confidence) == ("12", 0.4)
    assert info == {"copies": ["12", "17", "12"]}


def test_assemble_tie_goes_to_the_leftmost_reading_copy():
    prepared = _tiled()
    words = [Word("7", 110, 30, 30, 40), Word("1", 180, 30, 30, 40)]
    text, _, info = _os_ocr.assemble(words, prepared, "score")
    assert text == "7"
    assert info == {"copies": ["", "7", "1"]}


def test_assemble_splits_a_word_merged_across_copies():
    prepared = _tiled()
    # the engine read the three copies of a "3" as one word over all of them
    text, _, info = _os_ocr.assemble([Word("333", 30, 30, 190, 40)], prepared, "score")
    assert text == "3"
    assert info == {"copies": ["3", "3", "3"]}
    # unequal parts can't be split: the word goes to the copy under its centre
    text, _, info = _os_ocr.assemble([Word("ooa", 30, 30, 190, 40)], prepared, "text")
    assert info == {"copies": ["", "ooa", ""]}
    assert text == "ooa"


def test_assemble_maps_lookalikes_before_voting():
    prepared = _tiled()
    words = [
        Word("1O", 34, 30, 36, 40),
        Word("10", 106, 30, 36, 40),
        Word("I0", 178, 30, 36, 40),
    ]
    text, _, info = _os_ocr.assemble(words, prepared, "score", digits=True)
    assert text == "10"
    assert info == {"copies": ["10", "10", "10"]}
    assert _os_ocr.assemble([], prepared, "score") == (
        "",
        None,
        {"copies": ["", "", ""]},
    )


def test_missing_modules_handles_dotted_names_of_missing_packages():
    assert _os_ocr.missing_modules(["json", "os.path"]) == []
    assert _os_ocr.missing_modules(
        ["no_such_package_xyz.sub.module", "no_such_module_xyz"]
    ) == ["no_such_package_xyz.sub.module", "no_such_module_xyz"]
