"""光为 OCR · 数据口接收（串口 / TCP 透传 → 与 OCR 同一字段总线）。

用途：场馆记分台有 RS232/485/网口（或经有人 USR-W610 WiFi 透传）时，
不拍视频也能收比分与时钟；与摄像头 OCR 并存，由「模式」决定谁覆盖。

协议分层：
  (A) 原始字节 → 十六进制抓包日志（对 Bodet 等私有协议时用）
  (B) 光为标准 JSON 帧（本模块定义，便于自测与第三方）
  (C) 可插拔解析器（Bodet 等先 stub，以后再加）

配置落盘 scoresight.json，键前缀 data_ingest_*。
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from PySide6.QtCore import QObject, Signal

from storage import fetch_data, store_data

logger = logging.getLogger(__name__)

SETTINGS_FILE = "scoresight.json"

# --- 模式（与 UI 文案一致） -------------------------------------------------
MODE_OCR_ONLY = "ocr_only"  # 仅摄像头 OCR
MODE_DATA_ONLY = "data_only"  # 仅数据口
MODE_DATA_PREFER = "data_prefer"  # 数据口优先（有数据就覆盖对应字段）
MODES = (MODE_OCR_ONLY, MODE_DATA_ONLY, MODE_DATA_PREFER)

# --- 传输 -------------------------------------------------------------------
TRANSPORT_TCP_SERVER = "tcp_server"  # 本机听端口（W610 作 TCP Client 连进来）
TRANSPORT_TCP_CLIENT = "tcp_client"  # 本机去连对方推送
TRANSPORT_SERIAL = "serial"  # USB 转 232/485 的 COM 口
TRANSPORTS = (TRANSPORT_TCP_SERVER, TRANSPORT_TCP_CLIENT, TRANSPORT_SERIAL)

# --- 配置键 -----------------------------------------------------------------
KEY_ENABLED = "data_ingest_enabled"
KEY_MODE = "data_ingest_mode"
KEY_TRANSPORT = "data_ingest_transport"
KEY_TCP_HOST = "data_ingest_tcp_host"
KEY_TCP_PORT = "data_ingest_tcp_port"
KEY_SERIAL_PORT = "data_ingest_serial_port"
KEY_SERIAL_BAUD = "data_ingest_serial_baud"
KEY_PARSER = "data_ingest_parser"  # "json" | "raw" | "bodet_stub"
KEY_HEX_LOG = "data_ingest_hex_log"

DEFAULTS: dict[str, Any] = {
    KEY_ENABLED: False,
    KEY_MODE: MODE_OCR_ONLY,
    KEY_TRANSPORT: TRANSPORT_TCP_SERVER,
    KEY_TCP_HOST: "0.0.0.0",
    KEY_TCP_PORT: 5000,
    KEY_SERIAL_PORT: "COM3",
    KEY_SERIAL_BAUD: 9600,
    KEY_PARSER: "json",
    KEY_HEX_LOG: True,
}

# 光为标准 JSON 字段键 → 默认中/英文字段名（与 defaults.default_boxes 对齐）
CANONICAL_TO_BOX_NAMES: dict[str, tuple[str, ...]] = {
    "home_score": ("主队得分", "Home Score", "Home score"),
    "away_score": ("客队得分", "Away Score", "Away score"),
    "time": ("比赛时间", "Time", "Clock"),
    "period": ("节次", "Period"),
    "home_fouls": ("主队犯规", "Home Fouls", "#Home Fouls"),
    "away_fouls": ("客队犯规", "Away Fouls", "#Away Fouls"),
    "shot_clock": ("24秒", "Shot Clock", "shotclock", "Shot clock"),
}

JSON_FRAME_DOC = """
光为标准 JSON 帧（TCP/串口按行发送，UTF-8，一行一个 JSON）：

{
  "source": "guangwei_data",
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
  }
}

也可用字段框真实名字作键（中/英均可），例如 "主队得分": "78"。
W610 透传：设成 TCP Client，目标=本机 IP，端口=本软件「数据口」听的端口。
"""


def ensure_default_settings() -> None:
    """首次把默认键写入 scoresight.json（不覆盖已有值）。"""
    for key, default in DEFAULTS.items():
        if fetch_data(SETTINGS_FILE, key) is None:
            store_data(SETTINGS_FILE, key, default)


def load_config() -> dict[str, Any]:
    cfg = {}
    for key, default in DEFAULTS.items():
        val = fetch_data(SETTINGS_FILE, key, default)
        cfg[key] = default if val is None else val
    # 规范化
    if cfg[KEY_MODE] not in MODES:
        cfg[KEY_MODE] = MODE_OCR_ONLY
    if cfg[KEY_TRANSPORT] not in TRANSPORTS:
        cfg[KEY_TRANSPORT] = TRANSPORT_TCP_SERVER
    try:
        cfg[KEY_TCP_PORT] = int(cfg[KEY_TCP_PORT])
    except (TypeError, ValueError):
        cfg[KEY_TCP_PORT] = 5000
    try:
        cfg[KEY_SERIAL_BAUD] = int(cfg[KEY_SERIAL_BAUD])
    except (TypeError, ValueError):
        cfg[KEY_SERIAL_BAUD] = 9600
    cfg[KEY_ENABLED] = bool(cfg[KEY_ENABLED])
    cfg[KEY_HEX_LOG] = bool(cfg[KEY_HEX_LOG])
    return cfg


def save_config(cfg: Mapping[str, Any]) -> None:
    for key in DEFAULTS:
        if key in cfg:
            store_data(SETTINGS_FILE, key, cfg[key])


# --- 解析器接口 -------------------------------------------------------------


@dataclass
class ParseResult:
    """一次解析结果：字段名→文本；raw_line 给 UI 最近一行。"""

    fields: dict[str, str] = field(default_factory=dict)
    raw_line: str = ""
    ok: bool = False
    note: str = ""


class FrameParser(ABC):
    """可插拔解析器：吃原始字节，吐字段。"""

    name: str = "base"

    @abstractmethod
    def feed(self, data: bytes) -> list[ParseResult]:
        ...

    def reset(self) -> None:
        pass


class RawHexParser(FrameParser):
    """(A) 只记原始，不产出字段——方便以后对协议。"""

    name = "raw"

    def feed(self, data: bytes) -> list[ParseResult]:
        if not data:
            return []
        hex_s = data.hex(" ")
        return [
            ParseResult(
                fields={},
                raw_line=f"[RAW {len(data)}B] {hex_s}",
                ok=True,
                note="raw",
            )
        ]


class GuangweiJsonParser(FrameParser):
    """(B) 光为标准 JSON：按行缓冲，解析 fields。"""

    name = "json"

    def __init__(self) -> None:
        self._buf = bytearray()

    def reset(self) -> None:
        self._buf.clear()

    def feed(self, data: bytes) -> list[ParseResult]:
        if not data:
            return []
        self._buf.extend(data)
        out: list[ParseResult] = []
        while True:
            nl = self._buf.find(b"\n")
            if nl < 0:
                # 也容忍单包完整 JSON（无换行）
                if self._try_whole_buffer(out):
                    continue
                break
            line = bytes(self._buf[:nl])
            del self._buf[: nl + 1]
            if line.endswith(b"\r"):
                line = line[:-1]
            if not line.strip():
                continue
            out.append(self._parse_line(line))
        return out

    def _try_whole_buffer(self, out: list[ParseResult]) -> bool:
        """缓冲里已是完整 JSON 对象且无换行时直接解析。"""
        s = bytes(self._buf).strip()
        if not s.startswith(b"{") or not s.endswith(b"}"):
            return False
        try:
            json.loads(s.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return False
        self._buf.clear()
        out.append(self._parse_line(s))
        return True

    def _parse_line(self, line: bytes) -> ParseResult:
        text = line.decode("utf-8", errors="replace").strip()
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            return ParseResult(raw_line=text[:500], ok=False, note=f"JSON错误: {e}")
        if not isinstance(obj, dict):
            return ParseResult(raw_line=text[:500], ok=False, note="根须为对象")
        fields_in = obj.get("fields")
        mapped: dict[str, str] = {}
        if isinstance(fields_in, dict):
            for k, v in fields_in.items():
                if v is None:
                    continue
                mapped.update(_expand_field_key(str(k), str(v)))
        # 根上直接挂的字段名也认
        for k, v in obj.items():
            if k in ("source", "version", "ts_ms", "fields", "raw"):
                continue
            if isinstance(v, (str, int, float)):
                mapped.update(_expand_field_key(str(k), str(v)))
        return ParseResult(fields=mapped, raw_line=text[:500], ok=True, note="json")


class BodetStubParser(FrameParser):
    """(C) Bodet TV/串口协议占位：先只出原始行，字段以后补。"""

    name = "bodet_stub"

    def __init__(self) -> None:
        self._buf = bytearray()

    def reset(self) -> None:
        self._buf.clear()

    def feed(self, data: bytes) -> list[ParseResult]:
        if not data:
            return []
        self._buf.extend(data)
        # 暂按行切；真实 Bodet 帧格式待抓包后替换
        out: list[ParseResult] = []
        while True:
            nl = self._buf.find(b"\n")
            if nl < 0:
                if len(self._buf) > 4096:
                    chunk = bytes(self._buf[:256])
                    del self._buf[:256]
                    out.append(
                        ParseResult(
                            raw_line=f"[Bodet stub] {chunk.hex(' ')}",
                            ok=True,
                            note="bodet_stub",
                        )
                    )
                break
            line = bytes(self._buf[:nl])
            del self._buf[: nl + 1]
            out.append(
                ParseResult(
                    raw_line=f"[Bodet stub] {line.hex(' ')}",
                    ok=True,
                    note="bodet_stub（尚未解析字段）",
                )
            )
        return out


def _expand_field_key(key: str, value: str) -> dict[str, str]:
    """把 canonical / 中英文名都展开成「可能匹配的字段名」→ 同一值。

    主界面合并时会用当前盒子真实名字匹配；这里多写几个别名即可。
    """
    out: dict[str, str] = {key: value}
    aliases = CANONICAL_TO_BOX_NAMES.get(key)
    if aliases:
        for a in aliases:
            out[a] = value
    # 若传入的是某个中文/英文名，也挂上 canonical
    for canon, names in CANONICAL_TO_BOX_NAMES.items():
        if key in names or key == canon:
            out[canon] = value
            for a in names:
                out[a] = value
    return out


def make_parser(name: str) -> FrameParser:
    if name == "raw":
        return RawHexParser()
    if name == "bodet_stub":
        return BodetStubParser()
    return GuangweiJsonParser()


# --- 传输线程骨架 -----------------------------------------------------------


class _Stop:
    def __init__(self) -> None:
        self._e = threading.Event()

    def set(self) -> None:
        self._e.set()

    def is_set(self) -> bool:
        return self._e.is_set()

    def wait(self, timeout: float) -> bool:
        return self._e.wait(timeout)


def _hex_preview(data: bytes, limit: int = 64) -> str:
    if len(data) <= limit:
        return data.hex(" ")
    return data[:limit].hex(" ") + f" …(+{len(data) - limit}B)"


class DataIngestWorker:
    """后台收字节 → 解析 → 回调（不碰 Qt）。"""

    def __init__(
        self,
        on_parse: Callable[[ParseResult, bytes], None],
        on_status: Callable[[str], None],
        on_error: Callable[[str], None],
    ) -> None:
        self._on_parse = on_parse
        self._on_status = on_status
        self._on_error = on_error
        self._stop = _Stop()
        self._thread: threading.Thread | None = None
        self._parser: FrameParser = GuangweiJsonParser()
        self._hex_log = True
        self._cfg: dict[str, Any] = {}

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, cfg: Mapping[str, Any]) -> None:
        self.stop()
        self._stop = _Stop()
        self._cfg = dict(cfg)
        self._parser = make_parser(str(cfg.get(KEY_PARSER, "json")))
        self._hex_log = bool(cfg.get(KEY_HEX_LOG, True))
        t = threading.Thread(target=self._run, name="data-ingest", daemon=True)
        self._thread = t
        t.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
        self._thread = None

    def _run(self) -> None:
        transport = self._cfg.get(KEY_TRANSPORT, TRANSPORT_TCP_SERVER)
        try:
            if transport == TRANSPORT_TCP_SERVER:
                self._run_tcp_server()
            elif transport == TRANSPORT_TCP_CLIENT:
                self._run_tcp_client()
            elif transport == TRANSPORT_SERIAL:
                self._run_serial()
            else:
                self._on_error(f"未知传输类型: {transport}")
        except Exception as e:
            logger.exception("data ingest worker crashed")
            self._on_error(str(e))

    def _handle_bytes(self, data: bytes) -> None:
        if not data:
            return
        if self._hex_log:
            logger.info("数据口 RAW %s", _hex_preview(data))
        for pr in self._parser.feed(data):
            try:
                self._on_parse(pr, data)
            except Exception:
                logger.exception("data ingest on_parse failed")

    def _run_tcp_server(self) -> None:
        host = str(self._cfg.get(KEY_TCP_HOST) or "0.0.0.0")
        port = int(self._cfg.get(KEY_TCP_PORT) or 5000)
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind((host, port))
            srv.listen(5)
            srv.settimeout(0.5)
            self._on_status(f"TCP 服务端监听 {host}:{port}（等 W610/记分台连入）")
            while not self._stop.is_set():
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                self._on_status(f"已连接 {addr[0]}:{addr[1]}")
                conn.settimeout(0.5)
                try:
                    while not self._stop.is_set():
                        try:
                            chunk = conn.recv(4096)
                        except socket.timeout:
                            continue
                        if not chunk:
                            break
                        self._handle_bytes(chunk)
                finally:
                    try:
                        conn.close()
                    except OSError:
                        pass
                    self._on_status(f"连接断开，继续听 {host}:{port}")
        except OSError as e:
            self._on_error(f"无法监听 {host}:{port}: {e}")
        finally:
            try:
                srv.close()
            except OSError:
                pass
            self._on_status("TCP 服务端已停止")

    def _run_tcp_client(self) -> None:
        host = str(self._cfg.get(KEY_TCP_HOST) or "127.0.0.1")
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        port = int(self._cfg.get(KEY_TCP_PORT) or 5000)
        while not self._stop.is_set():
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3.0)
            try:
                self._on_status(f"TCP 客户端连接 {host}:{port}…")
                sock.connect((host, port))
                sock.settimeout(0.5)
                self._on_status(f"已连上 {host}:{port}")
                while not self._stop.is_set():
                    try:
                        chunk = sock.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break
                    self._handle_bytes(chunk)
            except OSError as e:
                self._on_error(f"连接失败: {e}（3 秒后重试）")
                self._stop.wait(3.0)
            finally:
                try:
                    sock.close()
                except OSError:
                    pass
        self._on_status("TCP 客户端已停止")

    def _run_serial(self) -> None:
        try:
            import serial  # type: ignore
            from serial import SerialException  # type: ignore
        except ImportError:
            self._on_error(
                "未安装 pyserial。请在环境执行: pip install pyserial\n"
                "便携版打包时请把 pyserial 打进依赖。"
            )
            return
        port = str(self._cfg.get(KEY_SERIAL_PORT) or "COM3")
        baud = int(self._cfg.get(KEY_SERIAL_BAUD) or 9600)
        while not self._stop.is_set():
            ser = None
            try:
                self._on_status(f"打开串口 {port} @ {baud}（RS232/485 经 USB 转接）")
                ser = serial.Serial(port=port, baudrate=baud, timeout=0.5)
                self._on_status(f"串口已打开 {port}")
                while not self._stop.is_set():
                    chunk = ser.read(4096)
                    if chunk:
                        self._handle_bytes(chunk)
            except SerialException as e:
                self._on_error(f"串口错误: {e}（3 秒后重试）")
                self._stop.wait(3.0)
            except Exception as e:
                self._on_error(f"串口异常: {e}")
                break
            finally:
                if ser is not None:
                    try:
                        ser.close()
                    except Exception:
                        pass
        self._on_status("串口已停止")


# --- Qt 门面：主线程信号 ----------------------------------------------------


class DataIngestManager(QObject):
    """主界面用的数据口管理器：启停、配置、最新字段、信号。"""

    # fields: dict[str,str] 已展开别名；raw_line: 最近一行原文
    fields_updated = Signal(dict, str)
    status_changed = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        ensure_default_settings()
        self._cfg = load_config()
        self._latest_fields: dict[str, str] = {}
        self._latest_raw: str = ""
        self._recent_raw: deque[str] = deque(maxlen=50)
        self._last_update_mono: float = 0.0
        self._status: str = "未启动"
        self._worker = DataIngestWorker(
            on_parse=self._on_parse_thread,
            on_status=self._on_status_thread,
            on_error=self._on_error_thread,
        )
        # 用 QueuedConnection 把后台线程回调切回主线程
        self._bridge_parse = _Bridge(self)
        self._bridge_parse.parsed.connect(self._apply_parse)
        self._bridge_parse.status.connect(self._apply_status)
        self._bridge_parse.error.connect(self._apply_error)

    @property
    def config(self) -> dict[str, Any]:
        return dict(self._cfg)

    @property
    def mode(self) -> str:
        return str(self._cfg.get(KEY_MODE, MODE_OCR_ONLY))

    @property
    def enabled(self) -> bool:
        return bool(self._cfg.get(KEY_ENABLED))

    @property
    def running(self) -> bool:
        return self._worker.running

    @property
    def latest_fields(self) -> dict[str, str]:
        return dict(self._latest_fields)

    @property
    def latest_raw(self) -> str:
        return self._latest_raw

    @property
    def status(self) -> str:
        return self._status

    @property
    def seconds_since_update(self) -> float | None:
        if self._last_update_mono <= 0:
            return None
        return time.monotonic() - self._last_update_mono

    def update_config(self, **kwargs) -> None:
        self._cfg.update(kwargs)
        save_config(self._cfg)

    def start(self) -> None:
        self._cfg[KEY_ENABLED] = True
        save_config(self._cfg)
        self._worker.start(self._cfg)

    def stop(self) -> None:
        self._cfg[KEY_ENABLED] = False
        save_config(self._cfg)
        self._worker.stop()
        self._apply_status("已停止")

    def shutdown(self) -> None:
        """关窗口时停线程，不改 enabled（下次仍可按配置自动启）。"""
        self._worker.stop()
        self._apply_status("已关闭")

    def restart_if_enabled(self) -> None:
        if self._cfg.get(KEY_ENABLED):
            self._worker.start(self._cfg)

    def _on_parse_thread(self, pr: ParseResult, _raw: bytes) -> None:
        self._bridge_parse.parsed.emit(pr)

    def _on_status_thread(self, msg: str) -> None:
        self._bridge_parse.status.emit(msg)

    def _on_error_thread(self, msg: str) -> None:
        self._bridge_parse.error.emit(msg)

    def _apply_parse(self, pr: ParseResult) -> None:
        if pr.raw_line:
            self._latest_raw = pr.raw_line
            self._recent_raw.appendleft(pr.raw_line)
        if pr.ok and pr.fields:
            self._latest_fields.update(pr.fields)
            self._last_update_mono = time.monotonic()
            self.fields_updated.emit(dict(self._latest_fields), self._latest_raw)
        elif pr.raw_line:
            # 无字段也通知 UI 刷新「最近一行」
            self.fields_updated.emit(dict(self._latest_fields), self._latest_raw)

    def _apply_status(self, msg: str) -> None:
        self._status = msg
        self.status_changed.emit(msg)
        logger.info("数据口: %s", msg)

    def _apply_error(self, msg: str) -> None:
        self._status = msg
        self.error_occurred.emit(msg)
        logger.warning("数据口错误: %s", msg)


class _Bridge(QObject):
    parsed = Signal(object)
    status = Signal(str)
    error = Signal(str)


def resolve_value_for_box(fields: Mapping[str, str], box_name: str) -> str | None:
    """从展开后的 fields 里取某个盒子的值。"""
    if box_name in fields:
        return fields[box_name]
    for canon, names in CANONICAL_TO_BOX_NAMES.items():
        if box_name in names or box_name == canon:
            if canon in fields:
                return fields[canon]
            for n in names:
                if n in fields:
                    return fields[n]
    return None


def build_results_from_data(
    boxes: list,
    fields: Mapping[str, str],
    *,
    previous: Mapping[str, str] | None = None,
):
    """把数据口字段合成 TextDetectionTargetWithResult 列表（与 OCR 同结构）。

    未出现在 fields 里的盒子 → Empty；值相对 previous 未变 → SameNoChange。
    """
    from text_detection_target import TextDetectionTargetWithResult

    RS = TextDetectionTargetWithResult.ResultState
    prev = previous or {}
    out = []
    for box in boxes:
        name = box.name
        val = resolve_value_for_box(fields, name)
        if val is None or val == "":
            state = RS.Empty
            text = ""
        elif prev.get(name) == val:
            state = RS.SameNoChange
            text = val
        else:
            state = RS.Success
            text = val
        out.append(TextDetectionTargetWithResult(box, text, state))
    return out


def overlay_data_on_ocr(
    ocr_results: list,
    fields: Mapping[str, str],
):
    """数据口优先：有数据的字段覆盖 OCR 结果，其余保留 OCR。"""
    from text_detection_target import TextDetectionTargetWithResult

    RS = TextDetectionTargetWithResult.ResultState
    if not fields:
        return ocr_results
    out = []
    for r in ocr_results:
        val = resolve_value_for_box(fields, r.name)
        if val is None:
            out.append(r)
            continue
        if r.result == val and r.result_state in (RS.Success, RS.SameNoChange):
            state = RS.SameNoChange
        else:
            state = RS.Success
        out.append(TextDetectionTargetWithResult(r, val, state))
    return out
