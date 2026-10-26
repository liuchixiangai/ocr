"""ocr_eval's Windows OCR adapter (Windows.Media.Ocr). The WinRT API is faked,
so these run anywhere; test_reads_rendered_text_with_the_real_engine also runs
the real engine when this is Windows with the winrt packages and the en-US OCR
language pack."""

import importlib.machinery
import sys
import types

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from ocr_eval.engines import EngineUnavailable, discover  # noqa: E402
from ocr_eval.engines import windows_ocr  # noqa: E402
from ocr_eval.engines.windows_ocr import WindowsOcr  # noqa: E402


def _module(monkeypatch, name, **attributes):
    module = types.ModuleType(name)
    module.__spec__ = importlib.machinery.ModuleSpec(name, None)
    module.__path__ = []
    for key, value in attributes.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)
    return module


class FakeWinRT:
    """The parts of the WinRT API the adapter uses. `read` maps the RGBA image
    the engine was given to [(text, x, y, width, height)]."""

    def __init__(self, monkeypatch, languages=("en-US",), read=None):
        self.languages = list(languages)
        self.read = read or (lambda rgba: [])
        self.images = []
        fake = self

        class Language:
            def __init__(self, tag):
                self.language_tag = tag

        class OcrEngine:
            max_image_dimension = 10000

            @staticmethod
            def is_language_supported(language):
                return language.language_tag in fake.languages

            @staticmethod
            def try_create_from_language(language):
                if language.language_tag not in fake.languages:
                    return None
                return OcrEngine()

            def recognize_async(self, bitmap):
                fake.images.append(bitmap.rgba)
                lines = [
                    types.SimpleNamespace(
                        words=[
                            types.SimpleNamespace(
                                text=text,
                                bounding_rect=types.SimpleNamespace(
                                    x=x, y=y, width=w, height=h
                                ),
                            )
                        ]
                    )
                    for text, x, y, w, h in fake.read(bitmap.rgba)
                ]
                result = types.SimpleNamespace(lines=lines)
                return types.SimpleNamespace(get=lambda: result)

        OcrEngine.available_recognizer_languages = [Language(t) for t in languages]

        class DataWriter:
            def write_bytes(self, data):
                self.data = bytes(data)

            def detach_buffer(self):
                return self.data

        class SoftwareBitmap:
            @staticmethod
            def create_copy_from_buffer(buffer, pixel_format, width, height):
                assert pixel_format == "RGBA8"
                bitmap = SoftwareBitmap()
                bitmap.rgba = np.frombuffer(buffer, np.uint8).reshape(height, width, 4)
                return bitmap

        for name in ("winrt", "winrt.windows", "winrt.windows.graphics"):
            _module(monkeypatch, name)
        _module(monkeypatch, "winrt.windows.globalization", Language=Language)
        _module(
            monkeypatch,
            "winrt.windows.graphics.imaging",
            BitmapPixelFormat=types.SimpleNamespace(RGBA8="RGBA8"),
            SoftwareBitmap=SoftwareBitmap,
        )
        _module(monkeypatch, "winrt.windows.media")
        _module(monkeypatch, "winrt.windows.media.ocr", OcrEngine=OcrEngine)
        _module(monkeypatch, "winrt.windows.storage")
        _module(monkeypatch, "winrt.windows.storage.streams", DataWriter=DataWriter)
        monkeypatch.setattr(windows_ocr, "_platform", lambda: "win32")
        monkeypatch.setattr(windows_ocr, "_load_system_msvcp140", lambda: None)


def _crop(height=24, width=36, colour=(0, 0, 200)):
    crop = np.zeros((height, width, 3), np.uint8)
    crop[:] = colour
    crop.flags.writeable = False
    return crop


def test_is_discovered_with_its_requirements_and_variants():
    registry = discover()
    assert registry.engines["windows-ocr"] is WindowsOcr
    assert WindowsOcr.requirements == windows_ocr.REQUIREMENTS
    assert all("==" in r for r in WindowsOcr.requirements)
    assert set(WindowsOcr.variants) == {
        "raw",
        "raw-digits",
        "upscale",
        "upscale-digits",
        "tile",
        "tile-digits",
    }
    engine = WindowsOcr("tile-digits")
    assert engine.spec.preprocessing == "tile" and engine.spec.digits
    with pytest.raises(ValueError):
        WindowsOcr("accurate-raw")


def test_unavailable_off_windows(monkeypatch):
    monkeypatch.setattr(windows_ocr, "_platform", lambda: "linux")
    assert WindowsOcr.unavailable_reason() == "Windows only (Windows.Media.Ocr)"


def test_unavailable_without_the_winrt_packages(monkeypatch):
    monkeypatch.setattr(windows_ocr, "_platform", lambda: "win32")
    monkeypatch.setattr(
        windows_ocr, "WINRT_MODULES", ("no_such_winrt_xyz.windows.media.ocr",)
    )
    reason = WindowsOcr.unavailable_reason()
    assert reason.startswith("missing Python modules: no_such_winrt_xyz")
    assert "winrt-Windows.Media.Ocr==" in reason


def test_unavailable_without_the_language_pack(monkeypatch):
    FakeWinRT(monkeypatch, languages=("de-DE",))
    reason = WindowsOcr.unavailable_reason()
    assert "no Windows OCR language pack for en-US (installed: de-DE)" in reason
    assert 'Add-WindowsCapability -Online -Name "Language.OCR~~~en-US~0.0.1.0"' in (
        reason
    )
    engine = WindowsOcr("raw")
    with pytest.raises(EngineUnavailable, match="Add-WindowsCapability"):
        engine.load()


def test_available_with_the_language_pack(monkeypatch):
    FakeWinRT(monkeypatch)
    assert WindowsOcr.unavailable_reason() is None


def test_system_msvcp140_is_loaded_before_winrt(monkeypatch):
    FakeWinRT(monkeypatch)
    calls = []
    monkeypatch.setattr(windows_ocr, "_load_system_msvcp140", lambda: calls.append(1))
    assert WindowsOcr.unavailable_reason() is None
    assert calls == [1]
    WindowsOcr("raw").load()
    assert calls == [1, 1]


def test_load_system_msvcp140(monkeypatch, tmp_path):
    loaded = []

    def windll(path):
        loaded.append(path)
        if "broken" in path:
            raise OSError("bad image")

    monkeypatch.setattr(windows_ocr, "ctypes", types.SimpleNamespace(WinDLL=windll))
    monkeypatch.setattr(windows_ocr, "_platform", lambda: "win32")
    monkeypatch.setenv("SystemRoot", str(tmp_path / "Windows"))
    windows_ocr._load_system_msvcp140()  # no System32 copy: nothing to load
    assert loaded == []

    dll = tmp_path / "Windows" / "System32" / "msvcp140.dll"
    dll.parent.mkdir(parents=True)
    dll.write_bytes(b"MZ")
    windows_ocr._load_system_msvcp140()
    assert loaded == [str(dll)]

    monkeypatch.setenv("SystemRoot", str(tmp_path / "broken"))
    (tmp_path / "broken" / "System32").mkdir(parents=True)
    (tmp_path / "broken" / "System32" / "msvcp140.dll").write_bytes(b"MZ")
    windows_ocr._load_system_msvcp140()  # a failing load is ignored
    assert len(loaded) == 2

    monkeypatch.setattr(windows_ocr, "_platform", lambda: "linux")
    windows_ocr._load_system_msvcp140()
    assert len(loaded) == 2


def test_raw_reads_the_crop_as_rgba(monkeypatch):
    fake = FakeWinRT(
        monkeypatch,
        read=lambda rgba: [("34", 20, 2, 14, 20), ("12:", 1, 2, 16, 20)],
    )
    engine = WindowsOcr("raw")
    engine.load()
    [result] = engine.recognize([_crop()], ["clock"])
    assert result == ("12: 34", None, None)
    [image] = fake.images
    assert image.shape == (24, 36, 4)
    # BGR (0, 0, 200) -> RGBA (200, 0, 0, 255)
    assert tuple(image[0, 0]) == (200, 0, 0, 255)
    info = engine.info()
    assert info["preprocessing"] == "raw" and info["language"] == "en-US"
    assert "upscale_height" not in info
    engine.close()


def test_upscale_digits_maps_lookalikes(monkeypatch):
    fake = FakeWinRT(monkeypatch, read=lambda rgba: [("lO", 30, 30, 60, 60)])
    engine = WindowsOcr("upscale-digits")
    engine.load()
    results = engine.recognize([_crop(), _crop()], ["score", "text"])
    assert [r.text for r in results] == ["10", "lO"]
    # 24x36 -> 64x96, plus a 32 px margin on every side
    assert fake.images[0].shape == (128, 160, 4)
    assert engine.info()["upscale_height"] == 64


def test_tile_votes_and_splits_merged_words(monkeypatch):
    def read(rgba):
        # copies of the 64x96 crop at x 32-128, 160-256, 288-384
        assert rgba.shape == (128, 416, 4)
        return [("777", 30, 30, 360, 60)]

    FakeWinRT(monkeypatch, read=read)
    engine = WindowsOcr("tile")
    engine.load()
    [result] = engine.recognize([_crop()], ["score"])
    assert result.text == "7"
    assert result.info == {"copies": ["7", "7", "7"]}
    assert engine.info()["copies"] == 3


def test_runs_in_the_harness(monkeypatch, tmp_path):
    from test_ocr_eval_dataset import _row, make_dataset

    from ocr_eval.dataset import load_dataset
    from ocr_eval.runner import RunOptions, load_images, run_engine

    FakeWinRT(monkeypatch, read=lambda rgba: [("1Z", 0, 0, 10, 10)])
    rows = [
        _row(id="a1", image="images/a1.png", text="12"),
        _row(id="a2", image="images/a2.png", text="13"),
    ]
    dataset = load_dataset(str(make_dataset(tmp_path / "ds", rows)))
    record = run_engine(
        WindowsOcr, "raw-digits", dataset, load_images(dataset), RunOptions(), None
    )
    assert record["status"] == "ok", record.get("traceback")
    assert [p["text"] for p in record["predictions"]] == ["12", "12"]
    assert record["scores"]["raw"]["exact"]["k"] == 1
    assert record["engine_info"]["digit_lookalikes"] is True


def _render(text, height=60):
    scale = height / 30
    thickness = max(1, int(scale * 2))
    (width, text_height), baseline = cv2.getTextSize(
        text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness
    )
    pad = text_height // 2
    image = np.full(
        (text_height + baseline + 2 * pad, width + 2 * pad, 3), 255, np.uint8
    )
    cv2.putText(
        image,
        text,
        (pad, pad + text_height),
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        (0, 0, 0),
        thickness,
        cv2.LINE_AA,
    )
    image.flags.writeable = False
    return image


def test_reads_rendered_text_with_the_real_engine():
    if sys.platform != "win32":
        pytest.skip("Windows only")
    reason = WindowsOcr.unavailable_reason()
    if reason:
        pytest.skip(reason)
    engine = WindowsOcr("upscale")
    engine.load()
    try:
        results = engine.recognize(
            [_render("12:34"), _render("HOME")], ["clock", "text"]
        )
        # (it may split "12:34" into "1 2:34"; scoring ignores whitespace)
        assert ["".join(r.text.split()) for r in results] == ["12:34", "HOME"]
        assert all(r.confidence is None for r in results)
    finally:
        engine.close()
    # a lone character: Windows OCR returns nothing; three copies read as a word
    tile = WindowsOcr("tile")
    tile.load()
    try:
        [result] = tile.recognize([_render("7", height=40)], ["score"])
        assert result.text == "7", result.info
    finally:
        tile.close()
