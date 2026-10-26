"""光为 OCR — 网站/在线计分板出站预留接口（本阶段不真对接）。

设计目标：
- 配置项先落盘、字段先定死，以后接导播星球或其它网站只改 URL/鉴权；
- 默认 enable_website_push=False，绝不向占位地址发请求、不刷屏报错；
- 可与现有「输出 → HTTP API」(api_output) 并用：网站预留走本模块，
  通用 HTTP 仍走 api_output；二者互不强制。

配置键（scoresight.json）：
  enable_website_push   bool   总开关，默认 False
  website_api_url       str    POST 目标，例如 https://api.example.com/score/update
  website_auth_header   str    整行请求头，例如 Authorization: Bearer <token>
  website_extra_headers str    额外头，多行 Name: value（可选）

JSON 正文见 build_website_payload() / WEBSITE_PAYLOAD_DOC。
"""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urlparse

from storage import fetch_data, store_data, subscribe_to_data

SETTINGS_FILE = "scoresight.json"

# --- public constants -----------------------------------------------------------

SETTING_ENABLE = "enable_website_push"
SETTING_URL = "website_api_url"
SETTING_AUTH = "website_auth_header"
SETTING_EXTRA_HEADERS = "website_extra_headers"

# Outbound HTTP API preset key (api_output.PRESETS) — UI label「光为网站（预留）」
OUT_API_PRESET_KEY = "guangwei_website"
PLACEHOLDER_HOST = "YOUR-WEBSITE-HOST"

# Documented payload field names (stable contract for the future website).
PAYLOAD_FIELDS = (
    "home_score",
    "away_score",
    "time",
    "period",
    "home_fouls",
    "away_fouls",
    "shot_clock",
    "raw",  # dict of every box name -> reading
)

WEBSITE_PAYLOAD_DOC = """
光为 OCR → 网站 预留 JSON（POST, application/json）示例：

{
  "source": "guangwei_ocr",
  "version": 1,
  "ts_ms": 1730000000000,
  "fields": {
    "home_score": "78",
    "away_score": "65",
    "time": "3:42",
    "period": "2",
    "home_fouls": "4",
    "away_fouls": "3",
    "shot_clock": "14"
  },
  "raw": {
    "Home Score": "78",
    "Away Score": "65",
    "Time": "3:42",
    "Period": "2"
  }
}

网站侧需要提供：
1) HTTPS（或内网 HTTP）接收地址 website_api_url
2) 可选鉴权头 website_auth_header（Authorization: Bearer ...）
3) 返回 2xx 视为成功；4xx/5xx 由客户端节流记日志，不阻塞识别
"""

# Map English box names → payload keys
_NAME_MAP = {
    "Home Score": "home_score",
    "Away Score": "away_score",
    "Time": "time",
    "Period": "period",
    "Home Fouls": "home_fouls",
    "Away Fouls": "away_fouls",
    "Shot Clock": "shot_clock",
}


enable_website_push: bool = bool(fetch_data(SETTINGS_FILE, SETTING_ENABLE, False))
website_api_url: str = fetch_data(SETTINGS_FILE, SETTING_URL, "") or ""
website_auth_header: str = fetch_data(SETTINGS_FILE, SETTING_AUTH, "") or ""
website_extra_headers: str = fetch_data(SETTINGS_FILE, SETTING_EXTRA_HEADERS, "") or ""


def _setup_enable(v):
    global enable_website_push
    enable_website_push = bool(v)


def _setup_url(v):
    global website_api_url
    website_api_url = v or ""


def _setup_auth(v):
    global website_auth_header
    website_auth_header = v or ""


def _setup_extra(v):
    global website_extra_headers
    website_extra_headers = v or ""


subscribe_to_data(SETTINGS_FILE, SETTING_ENABLE, _setup_enable)
subscribe_to_data(SETTINGS_FILE, SETTING_URL, _setup_url)
subscribe_to_data(SETTINGS_FILE, SETTING_AUTH, _setup_auth)
subscribe_to_data(SETTINGS_FILE, SETTING_EXTRA_HEADERS, _setup_extra)


def is_placeholder_url(url: str | None) -> bool:
    if not url:
        return True
    try:
        host = (urlparse(url.strip()).hostname or "").upper()
    except ValueError:
        return True
    return (not host) or host == PLACEHOLDER_HOST.upper() or "YOUR-" in host.upper()


def is_ready() -> bool:
    """True only when the user turned the switch on and set a real URL."""
    return bool(enable_website_push) and (not is_placeholder_url(website_api_url))


def ensure_default_settings() -> None:
    """Write reserved keys once so scoresight.json documents the contract."""
    if fetch_data(SETTINGS_FILE, SETTING_ENABLE, None) is None:
        store_data(SETTINGS_FILE, SETTING_ENABLE, False)
    if fetch_data(SETTINGS_FILE, SETTING_URL, None) is None:
        store_data(SETTINGS_FILE, SETTING_URL, "")
    if fetch_data(SETTINGS_FILE, SETTING_AUTH, None) is None:
        store_data(SETTINGS_FILE, SETTING_AUTH, "")
    if fetch_data(SETTINGS_FILE, SETTING_EXTRA_HEADERS, None) is None:
        store_data(SETTINGS_FILE, SETTING_EXTRA_HEADERS, "")


def build_website_payload(
    readings: Mapping[str, str] | None,
    *,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    """Build the JSON body the future website will accept.

    readings: {box_name: text}, e.g. {"Home Score": "78", "Time": "3:42"}
    """
    import time

    raw = {str(k): ("" if v is None else str(v)) for k, v in (readings or {}).items()}
    fields: dict[str, str] = {}
    for eng, key in _NAME_MAP.items():
        if eng in raw:
            fields[key] = raw[eng]
    return {
        "source": "guangwei_ocr",
        "version": 1,
        "ts_ms": int(ts_ms if ts_ms is not None else time.time() * 1000),
        "fields": fields,
        "raw": raw,
    }


def maybe_push_website(readings: Mapping[str, str] | None) -> bool:
    """No-op unless enabled + real URL. Returns True if a send was attempted.

    Intentionally does nothing in this polish round when not ready, so a
    placeholder never floods the log. Real HTTP will be wired when the site API
    exists (reuse api_output.OutputWorker or requests here).
    """
    if not is_ready():
        return False
    # Future: POST build_website_payload(readings) to website_api_url with auth.
    # Kept as a stub so callers can already invoke it every OCR cycle.
    _ = build_website_payload(readings)
    return False  # stub: no network yet


def status_label() -> str:
    """Short Chinese status for UI / docs."""
    if not enable_website_push:
        return "网站推送：预留（未启用）"
    if is_placeholder_url(website_api_url):
        return "网站推送：已开开关，但未填写真实网址"
    return f"网站推送：已配置 {website_api_url}"
