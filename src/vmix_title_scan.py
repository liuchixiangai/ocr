"""Read vMix title field names from the Web Controller state XML.

GET ``http://{host}:{port}/api/`` (no query) returns the Web Controller state.
Text fields are ``<text name="...">`` elements under an ``<input>``. vMix's
Developer API (https://www.vmix.com/help28/DeveloperAPI.html) defines the
``SetText`` ``SelectedName`` as:

- **Title and XAML** — the text field's name. The documented example is
  ``SelectedName=Headline``, with no suffix. The XML name is kept as written.
- **GT** — the Title Editor name, which includes ``.Text`` (example
  ``Headline.Text``). The state XML usually already has that suffix. If a GT
  ``<text>`` name does not, ``.Text`` is appended. Image fields (``.Source``)
  are ``<image>`` elements and are not SetText targets.

Input ``type`` values treated as titles are ``GT``, ``Xaml`` and ``Title``.
Any other input that actually contains ``<text>`` elements is included too;
only GT names gain a missing ``.Text``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass

import requests

# (connect, read) seconds. The state document is larger than a SetText call,
# so the read timeout is longer than output_worker.HTTP_TIMEOUT.
SCAN_TIMEOUT = (3.0, 8.0)

# type attribute, compared case-insensitively. "Xaml" is the spelling vMix
# writes into the state XML.
TITLE_TYPES = frozenset({"gt", "xaml", "title"})

# A guess has to clear this. Weaker overlaps (a bare "home" inside an
# unrelated word that was not caught by the avoid list) stay unset.
MIN_MATCH_SCORE = 40


class VmixScanError(Exception):
    """The Web Controller state could not be read. ``code`` is a stable id;
    ``format_scan_error`` turns it into the status-line sentence."""

    def __init__(self, code: str, **details):
        self.code = code
        self.details = details
        super().__init__(code)


@dataclass(frozen=True)
class VmixTitleInput:
    """One title-like input and the SetText names of its text fields."""

    number: str
    key: str
    type: str
    title: str
    short_title: str
    fields: tuple[str, ...]

    def label_name(self) -> str:
        return self.short_title or self.title or "未命名"


@dataclass(frozen=True)
class TitleChoice:
    """Which title to map, or why the user still has to pick / why none fit.

    ``kind`` is ``unique`` (use ``chosen``), ``pick`` (ask, options in
    ``options``) or ``none``. ``reason`` for ``pick`` is ``empty``,
    ``ambiguous`` or ``no_match``; for ``none`` it is ``no_titles`` or
    ``no_fields``.
    """

    kind: str
    chosen: VmixTitleInput | None
    options: tuple[VmixTitleInput, ...]
    reason: str
    query: str = ""
    empty_title: str = ""
    empty_number: str = ""


def _local(tag: str) -> str:
    return tag.split("}")[-1].lower()


def selected_name(input_type: str, xml_name: str) -> str:
    """SetText SelectedName for one ``<text name>``.

    GT titles need the ``.Text`` suffix the Title Editor shows. Title, XAML
    and anything else keep the name the XML already has, including a suffix
    when vMix put one there.
    """
    name = (xml_name or "").strip()
    if not name:
        return ""
    if input_type.casefold() == "gt" and not name.casefold().endswith(".text"):
        return name + ".Text"
    return name


def parse_vmix_api_xml(xml_text: str) -> list[VmixTitleInput]:
    """Title-like inputs in document order. Raises ``VmixScanError``."""
    text = (xml_text or "").lstrip("\ufeff").strip()
    if not text:
        raise VmixScanError("xml")
    if text.startswith("VERSION OK"):
        raise VmixScanError("tcp_port")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        raise VmixScanError("xml") from error
    if _local(root.tag) != "vmix":
        raise VmixScanError("xml")

    nodes = [
        node for node in root.findall("inputs/input") if _local(node.tag) == "input"
    ]
    if not nodes:
        nodes = [node for node in list(root) if _local(node.tag) == "input"]

    titles: list[VmixTitleInput] = []
    for node in nodes:
        kind = (node.get("type") or "").strip()
        texts = [child for child in list(node) if _local(child.tag) == "text"]
        if kind.casefold() not in TITLE_TYPES and not texts:
            continue
        fields: list[str] = []
        seen: set[str] = set()
        for child in texts:
            name = selected_name(kind, child.get("name") or "")
            if not name or name in seen:
                continue
            seen.add(name)
            fields.append(name)
        titles.append(
            VmixTitleInput(
                number=(node.get("number") or "").strip(),
                key=(node.get("key") or "").strip(),
                type=kind,
                title=(node.get("title") or "").strip(),
                short_title=(node.get("shortTitle") or "").strip(),
                fields=tuple(fields),
            )
        )
    return titles


def _matches_query(title: VmixTitleInput, query: str) -> bool:
    """Number, key, title or shortTitle. Names are matched ignoring case.

    vMix's own Input-by-name lookup is case-sensitive; scanning is a bit
    looser so ``scoreboard`` still finds ``Scoreboard``. Two titles that
    both match are left for the picker instead of guessing.
    """
    if query == title.number:
        return True
    folded = query.casefold()
    if title.key and folded == title.key.casefold():
        return True
    for candidate in (title.title, title.short_title):
        if candidate and folded == candidate.casefold():
            return True
    return False


def choose_title_input(inputs: list[VmixTitleInput], query: str) -> TitleChoice:
    """Pick the title the Input box refers to, or ask when it doesn't."""
    query = (query or "").strip()
    with_fields = tuple(item for item in inputs if item.fields)
    if not with_fields:
        titled = [item for item in inputs if item.type.casefold() in TITLE_TYPES]
        if len(titled) == 1:
            one = titled[0]
            return TitleChoice(
                "none",
                None,
                (),
                "no_fields",
                query,
                one.label_name(),
                one.number,
            )
        if titled:
            return TitleChoice("none", None, (), "no_fields", query)
        return TitleChoice("none", None, (), "no_titles", query)

    if not query:
        if len(with_fields) == 1:
            return TitleChoice("unique", with_fields[0], with_fields, "", query)
        return TitleChoice("pick", None, with_fields, "empty", query)

    matches = tuple(item for item in with_fields if _matches_query(item, query))
    if len(matches) == 1:
        return TitleChoice("unique", matches[0], with_fields, "", query)
    if len(matches) > 1:
        return TitleChoice("pick", None, matches, "ambiguous", query)
    return TitleChoice("pick", None, with_fields, "no_match", query)


def input_choice_label(title: VmixTitleInput) -> str:
    name = title.short_title or title.title or "未命名"
    kind = title.type or "?"
    return f"{title.number}  {name}  （{kind}，{len(title.fields)} 个文字）"


def cell_is_placeholder(box_name: str, current: str | None) -> bool:
    """True when a mapping cell is blank or still shows the box's own name.

    The table fills the right column with the box name until someone types a
    vMix field. That default is not a saved choice: a scan may replace it.
    Any other non-empty text is left alone, including a value the user set
    equal to some other field. A value the user explicitly saved that happens
    to equal the box name is still treated as the default and may be
    replaced; vMix GT fields almost always need a ``.Text`` suffix, so the
    box name alone is not a finished mapping.
    """
    text = (current or "").strip()
    return text == "" or text == (box_name or "").strip()


def _compact(field_name: str) -> str:
    base = field_name.strip()
    if base.casefold().endswith(".text"):
        base = base[: -len(".text")]
    return "".join(ch for ch in base.casefold() if ch not in " _-.")


def _has_any(compact: str, tokens: tuple[str, ...]) -> bool:
    return any(token in compact for token in tokens)


# avoid: if any token is in the compact field name, the role does not match.
# hits: (substring, score). The highest hit wins. ``groups`` is an AND of two
# token lists. ``exact`` matches the whole compact name. ``weak`` is a short
# substring used when nothing more specific hit.
_FieldRule = dict


def _rule(
    avoid: tuple[str, ...],
    hits: tuple[tuple[str, int], ...] = (),
    groups: tuple[tuple[tuple[str, ...], tuple[str, ...], int], ...] = (),
    exact: tuple[tuple[str, ...], int] | None = None,
    weak: tuple[tuple[str, ...], int] | None = None,
) -> _FieldRule:
    return {
        "avoid": avoid,
        "hits": hits,
        "groups": groups,
        "exact": exact,
        "weak": weak,
    }


_SCORE_WORDS = ("score", "得分", "比分")
_FOUL_WORDS = ("foul", "犯规")
_CLOCK_AVOID = (
    "shot",
    "24",
    "foul",
    "犯规",
    "period",
    "quarter",
    "score",
    "得分",
    "比分",
    "timeout",
    "暂停",
    "title",
    "name",
    "队名",
    "节",
)
_NAME_AVOID = (
    "score",
    "得分",
    "比分",
    "foul",
    "犯规",
    "clock",
    "period",
    "shot",
    "时间",
)

_RULES: dict[str, _FieldRule] = {
    "home_score": _rule(
        avoid=_FOUL_WORDS
        + (
            "title",
            "name",
            "队名",
            "名称",
            "logo",
            "timeout",
            "暂停",
            "clock",
            "period",
            "quarter",
            "shot",
            "节",
            "away",
            "客",
        ),
        hits=(
            ("homescore", 100),
            ("主队得分", 100),
            ("主队比分", 100),
        ),
        groups=((("home", "主队", "主"), _SCORE_WORDS, 90),),
        exact=(("home", "主队"), 55),
        weak=(("home", "主队"), 50),
    ),
    "away_score": _rule(
        avoid=_FOUL_WORDS
        + (
            "title",
            "name",
            "队名",
            "名称",
            "logo",
            "timeout",
            "暂停",
            "clock",
            "period",
            "quarter",
            "shot",
            "节",
            "home",
            "主",
        ),
        hits=(
            ("awayscore", 100),
            ("guestscore", 100),
            ("visitorscore", 100),
            ("客队得分", 100),
            ("客队比分", 100),
        ),
        groups=((("away", "guest", "visitor", "客队", "客"), _SCORE_WORDS, 90),),
        exact=(("away", "guest", "客队", "客"), 55),
        weak=(("away", "guest", "visitor", "客队"), 50),
    ),
    "game_clock": _rule(
        avoid=_CLOCK_AVOID,
        hits=(
            ("gameclock", 100),
            ("比赛时间", 100),
            ("gametime", 95),
            ("总计", 90),
            ("总时间", 90),
            ("clock", 85),
            ("时间", 70),
            ("time", 60),
        ),
        exact=(("clock", "time", "总计", "总时间"), 80),
    ),
    "shot_clock": _rule(
        avoid=(
            "timeout",
            "暂停",
            "foul",
            "犯规",
            "period",
            "quarter",
            "score",
            "得分",
            "比分",
            "title",
            "name",
            "队名",
            "节次",
        ),
        hits=(
            ("shotclock", 100),
            ("24秒", 100),
            ("进攻时间", 100),
            ("shot", 80),
        ),
        groups=((("shot", "24"), ("clock", "时间"), 95),),
        exact=(("24", "shot"), 70),
    ),
    "period": _rule(
        avoid=(
            "clock",
            "score",
            "得分",
            "比分",
            "foul",
            "犯规",
            "shot",
            "time",
            "时间",
            "24",
        ),
        hits=(
            ("period", 100),
            ("节次", 100),
            ("quarter", 90),
            ("qtr", 80),
        ),
        exact=(("节", "局", "prd"), 75),
    ),
    "home_fouls": _rule(
        avoid=(
            "score",
            "得分",
            "比分",
            "clock",
            "period",
            "quarter",
            "shot",
            "title",
            "name",
            "队名",
            "时间",
            "away",
            "客",
        ),
        hits=(("homefoul", 100), ("主队犯规", 100)),
        groups=((("home", "主队", "主"), _FOUL_WORDS, 95),),
        weak=(_FOUL_WORDS, 45),
    ),
    "away_fouls": _rule(
        avoid=(
            "score",
            "得分",
            "比分",
            "clock",
            "period",
            "quarter",
            "shot",
            "title",
            "name",
            "队名",
            "时间",
            "home",
            "主",
        ),
        hits=(("awayfoul", 100), ("guestfoul", 100), ("客队犯规", 100)),
        groups=((("away", "guest", "visitor", "客队", "客"), _FOUL_WORDS, 95),),
        weak=(_FOUL_WORDS, 45),
    ),
    "fouls": _rule(
        avoid=("score", "得分", "比分", "clock", "period", "shot", "title", "时间"),
        hits=(("foul", 70), ("犯规", 70)),
    ),
    "home_name": _rule(
        avoid=_NAME_AVOID + ("away", "客"),
        hits=(
            ("hometitle", 100),
            ("homename", 100),
            ("hometeam", 100),
            ("主队名", 100),
            ("主队名称", 100),
        ),
        groups=((("home", "主队"), ("title", "name", "team", "队名", "名称"), 90),),
    ),
    "away_name": _rule(
        avoid=_NAME_AVOID + ("home", "主"),
        hits=(
            ("awaytitle", 100),
            ("awayname", 100),
            ("awayteam", 100),
            ("客队名", 100),
            ("客队名称", 100),
        ),
        groups=(
            (("away", "guest", "客队"), ("title", "name", "team", "队名", "名称"), 90),
        ),
    ),
}


def score_field(role: str, field_name: str) -> int:
    """How well ``field_name`` fits ``role`` (0 = no)."""
    rule = _RULES.get(role)
    if rule is None:
        return 0
    compact = _compact(field_name)
    if not compact or _has_any(compact, rule["avoid"]):
        return 0
    best = 0
    for token, weight in rule["hits"]:
        if token in compact:
            best = max(best, weight)
    for left, right, weight in rule["groups"]:
        if _has_any(compact, left) and _has_any(compact, right):
            best = max(best, weight)
    exact = rule["exact"]
    if exact is not None and compact in exact[0]:
        best = max(best, exact[1])
    weak = rule["weak"]
    if weak is not None and _has_any(compact, weak[0]):
        best = max(best, weak[1])
    return best


def classify_box(name: str) -> str | None:
    """The scoreboard role of an OCR box name, or None when it isn't obvious."""
    raw = (name or "").strip()
    if not raw:
        return None
    low = raw.casefold()
    compact = _compact(raw)

    shot = (
        "24秒" in raw
        or compact in {"24", "shot", "shotclock"}
        or "shotclock" in compact
        or "shot_clock" in low
        or ("shot" in low and "clock" in low)
        or ("进攻" in raw and ("时间" in raw or "24" in raw))
    )
    if shot and "timeout" not in low and "暂停" not in raw:
        return "shot_clock"

    foul = "foul" in low or "犯规" in raw
    homeish = "home" in low or "主" in raw
    awayish = "away" in low or "guest" in low or "visitor" in low or "客" in raw
    if foul and homeish and not awayish:
        return "home_fouls"
    if foul and awayish and not homeish:
        return "away_fouls"
    if foul:
        return "fouls"

    if (
        "节次" in raw
        or compact in {"period", "quarter", "qtr", "prd"}
        or "period" in low
        or "quarter" in low
    ):
        return "period"

    if "timeout" in low or "暂停" in raw:
        clockish = False
    else:
        clockish = (
            "比赛时间" in raw
            or raw in {"总计", "总时间"}
            or "gameclock" in compact
            or "game_clock" in low
            or "gametime" in compact
            or compact in {"clock", "time"}
            or low in {"time", "clock", "game clock", "game time"}
            or ("clock" in low and "shot" not in low)
        )
    if clockish:
        return "game_clock"

    nameish = (
        "name" in low
        or "title" in low
        or "team" in low
        or "队名" in raw
        or "名称" in raw
    )
    scoreish = "score" in low or "得分" in raw or "比分" in raw
    if homeish and nameish and not scoreish:
        return "home_name"
    if awayish and nameish and not scoreish:
        return "away_name"
    if homeish and not awayish:
        return "home_score"
    if awayish and not homeish:
        return "away_score"
    return None


def _prefer_exact_field(
    group: list[tuple[int, str, str]],
) -> list[tuple[int, str, str]]:
    """Drop same-score rivals when one field's name is the box name.

    ``Home Team`` scores the same on ``Home Team.Text`` and ``Home Title.Text``.
    The name that matches the box wins. A tie between two boxes for one field
    is left alone, and so is a box with no exact name among its rivals.
    """
    by_box: dict[str, list[tuple[int, str, str]]] = {}
    for item in group:
        by_box.setdefault(item[1], []).append(item)
    kept: list[tuple[int, str, str]] = []
    for box, items in by_box.items():
        if len(items) == 1:
            kept.extend(items)
            continue
        box_key = _compact(box)
        exact = [item for item in items if _compact(item[2]) == box_key]
        kept.extend(exact if len(exact) == 1 else items)
    return kept


def auto_match_fields(box_names: list[str], field_names: list[str]) -> dict[str, str]:
    """Best-effort ``{box name: vMix field}``.

    A field is used at most once. When two boxes (or two fields) tie at the
    same score, neither is filled: a wrong guess is worse than an empty cell.
    The one exception is a single box tied across fields: the field whose
    name equals the box name is kept.
    """
    candidates: list[tuple[int, str, str]] = []
    for box in box_names:
        role = classify_box(box)
        if role is None:
            continue
        for field in field_names:
            weight = score_field(role, field)
            if weight >= MIN_MATCH_SCORE:
                candidates.append((weight, box, field))
    candidates.sort(key=lambda item: item[0], reverse=True)

    used_boxes: set[str] = set()
    used_fields: set[str] = set()
    matched: dict[str, str] = {}
    index = 0
    while index < len(candidates):
        weight = candidates[index][0]
        group: list[tuple[int, str, str]] = []
        while index < len(candidates) and candidates[index][0] == weight:
            group.append(candidates[index])
            index += 1
        group = _prefer_exact_field(
            [
                item
                for item in group
                if item[1] not in used_boxes and item[2] not in used_fields
            ]
        )
        by_box: dict[str, list[tuple[int, str, str]]] = {}
        by_field: dict[str, list[tuple[int, str, str]]] = {}
        for item in group:
            by_box.setdefault(item[1], []).append(item)
            by_field.setdefault(item[2], []).append(item)
        conflict_boxes = {name for name, items in by_box.items() if len(items) > 1}
        conflict_fields = {name for name, items in by_field.items() if len(items) > 1}
        for _, box, field in group:
            if box in conflict_boxes or field in conflict_fields:
                continue
            if box in used_boxes or field in used_fields:
                continue
            matched[box] = field
            used_boxes.add(box)
            used_fields.add(field)
    return matched


def format_scan_error(error: VmixScanError, zh: bool = True) -> str:
    """Status-line sentence for a failed read of ``/api/``."""
    status = error.details.get("status")
    if zh:
        texts = {
            "no_host": "请先填写 vMix 主机。本机一般填 localhost。",
            "bad_port": "端口不对。请填 Web Controller 的端口，一般是 8088（不要填 8099）。",
            "connection": (
                "连不上 vMix。请打开 vMix → Settings → Web Controller，"
                "勾选 Enable，并核对主机和端口（默认 8088）。"
            ),
            "timeout": (
                "等太久了，vMix 没有回应。请检查主机、端口和网络，"
                "确认 Web Controller 已打开。"
            ),
            "unauthorized": (
                f"vMix 要求登录（HTTP {status or 401}）。"
                "请在 Web Controller 里允许不登录访问 API。"
            ),
            "http": f"vMix 返回了 HTTP {status}，没有拿到标题列表。",
            "tcp_port": (
                "这个端口像是 vMix 的 TCP API（一般 8099），不是网页接口。"
                "请改成 Web Controller 的端口，一般是 8088。"
            ),
            "xml": (
                "连上了，但返回的不是 vMix 的状态 XML。"
                "请确认端口是 Web Controller（一般 8088）。"
            ),
            "unexpected": "扫描标题字段时出错了。请看日志，或稍后再试。",
        }
    else:
        texts = {
            "no_host": "Enter the vMix host first (localhost if vMix is on this computer).",
            "bad_port": "That port is not valid. The Web Controller port is 8088 by default.",
            "connection": (
                "Can't reach vMix. Enable Settings → Web Controller "
                "and check the host and port (8088 by default)."
            ),
            "timeout": "vMix did not answer in time. Check the host, port and network.",
            "unauthorized": (
                f"vMix asked for a login (HTTP {status or 401}). "
                "Allow API access without a login in Web Controller."
            ),
            "http": f"vMix returned HTTP {status}.",
            "tcp_port": (
                "That port looks like the vMix TCP API (usually 8099). "
                "Use the Web Controller port, 8088 by default."
            ),
            "xml": "The reply was not vMix state XML. Check the Web Controller port (8088).",
            "unexpected": "Couldn't scan the title fields. See the log and try again.",
        }
    return texts.get(error.code, texts["unexpected"])


def format_title_problem(choice: TitleChoice, zh: bool = True) -> str:
    """Status line when no title can be mapped."""
    if choice.reason == "no_titles":
        if zh:
            return (
                "连上了，但没有找到标题（GT / XAML / Title）。"
                "请先在 vMix 里添加一个标题输入。"
            )
        return "Connected, but no GT, XAML or Title inputs were found."
    if choice.reason == "no_fields":
        if choice.empty_title:
            if zh:
                return (
                    f"找到了标题「{choice.empty_title}」"
                    f"（Input {choice.empty_number}），但里面没有文字字段。"
                    "请在标题编辑器里确认有文本对象。"
                )
            return (
                f"Title “{choice.empty_title}” (input {choice.empty_number}) "
                "has no text fields."
            )
        if zh:
            return "找到了标题，但里面没有文字字段。请在标题编辑器里确认有文本对象。"
        return "Titles were found, but none of them have text fields."
    return format_scan_error(VmixScanError("unexpected"), zh)


def format_pick_prompt(choice: TitleChoice, zh: bool = True) -> str:
    query = choice.query
    if zh:
        if choice.reason == "empty":
            head = "Input 是空的。"
        elif choice.reason == "ambiguous":
            head = f"Input「{query}」对上了好几个标题。"
        else:
            head = f"Input「{query}」对不上带文字的标题。"
        return head + "请选一个："
    if choice.reason == "empty":
        head = "Input is empty."
    elif choice.reason == "ambiguous":
        head = f"Input “{query}” matches more than one title."
    else:
        head = f"Input “{query}” does not match a title that has text fields."
    return head + " Choose one:"


def format_scan_success(
    title: VmixTitleInput,
    filled: int,
    kept: int,
    unmatched: int,
    zh: bool = True,
) -> str:
    name = title.label_name()
    kind = title.type or "?"
    count = len(title.fields)
    if zh:
        parts = [
            f"已扫描「{name}」（Input {title.number}，{kind}），共 {count} 个文字字段。"
        ]
        if filled:
            parts.append(
                f"自动填了 {filled} 个空着（或仍是字段原名）的格子；"
                f"已经改过的 {kept} 个没动。"
            )
        elif kept:
            parts.append(f"已经改过的 {kept} 个映射都保留了，没有盖掉。")
        else:
            parts.append("没有需要自动填写的格子。")
        if unmatched:
            parts.append(f"还有 {unmatched} 个没猜到，在那一行的下拉里选，或直接手打。")
        else:
            parts.append("每一行的下拉都可以改，也可以手打。")
        return "".join(parts)
    parts = [
        f"Scanned “{name}” (input {title.number}, {kind}): {count} text field(s). "
    ]
    if filled:
        parts.append(
            f"Filled {filled} empty row(s); kept {kept} row(s) you already edited. "
        )
    elif kept:
        parts.append(f"Kept all {kept} row(s) you already edited. ")
    if unmatched:
        parts.append(
            f"{unmatched} row(s) were not guessed — pick them in that row's dropdown, "
            "or type a name."
        )
    else:
        parts.append("Every row's dropdown can be changed, or you can type a name.")
    return "".join(parts)


def fetch_vmix_api_xml(
    host: str,
    port: str,
    get: Callable | None = None,
    timeout: tuple[float, float] = SCAN_TIMEOUT,
) -> str:
    """GET ``/api/`` and return the body. Raises ``VmixScanError``.

    ``get`` defaults to ``requests.get`` and is injected by tests. It is
    called as ``get(url, timeout=timeout)``.
    """
    host = (host or "").strip()
    port = (port or "").strip()
    if not host:
        raise VmixScanError("no_host")
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise VmixScanError("bad_port")
    url = f"http://{host}:{port}/api/"
    getter = get or requests.get
    try:
        response = getter(url, timeout=timeout)
    except requests.Timeout as error:
        raise VmixScanError("timeout") from error
    except requests.ConnectionError as error:
        if "VERSION OK" in str(error):
            raise VmixScanError("tcp_port") from error
        raise VmixScanError("connection") from error
    except requests.RequestException as error:
        raise VmixScanError("connection") from error

    status = int(getattr(response, "status_code", 200) or 0)
    body = getattr(response, "text", "") or ""
    if str(body).lstrip().startswith("VERSION OK"):
        raise VmixScanError("tcp_port")
    if status in (401, 403):
        raise VmixScanError("unauthorized", status=status)
    if status >= 400:
        raise VmixScanError("http", status=status)
    return body
