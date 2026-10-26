"""Browser overlays served by the HTTP server: transparent pages that show live
field values, for any production tool that can show a web page as a graphics
layer (OBS Browser Source, vMix Web Browser, Wirecast Web Page, CasparCG's HTML
producer, TriCaster Live Link, Magewell Director, Epiphan Pearl...).

Routes (all GET, mounted on http_server's app):

    /events                 live values as Server-Sent Events (overlay_feed)
    /overlay/state          the same values as one JSON snapshot (polling)
    /overlay/client.js      the client that fills data-field elements
    /overlay/client-worker.js  its shared worker (one stream per browser)
    /overlay/overlay.css    the built-in overlays' styles
    /overlay                an index: the built-in and custom overlays
    /overlay/scorebug       built-in overlays, configured by query
    /overlay/lower-third    parameters (parse_options), rendered with
    /overlay/clock          every value HTML-escaped
    /overlay/field
    /overlay/custom/<path>  files from the user's overlays folder

Security (the server listens on every interface): query parameters are
validated (colours, fonts, numbers and choices against strict patterns) and
every text is HTML-escaped; the built-in pages have no inline script and a
Content-Security-Policy. Custom files are served only from inside the overlays
folder: the resolved path must stay inside it, hidden names, ':' and
unknown file types are refused.
"""

import html
import math
import os
import re
import shutil
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)

from overlay_feed import feed
from resource_path import resource_path
from sc_logging import logger
from storage import get_data_dir

router = APIRouter()

# --- the built-in overlays' options ---------------------------------------------

SCOREBUG, LOWER_THIRD, CLOCK, FIELD = "scorebug", "lower-third", "clock", "field"
OVERLAY_KINDS = (SCOREBUG, LOWER_THIRD, CLOCK, FIELD)

# the standard fields (defaults.default_boxes) each overlay shows by default
DEFAULT_FIELDS = {
    # home score, away score, the clock, then small cells
    SCOREBUG: ("主队得分", "客队得分", "比赛时间", "节次"),
    LOWER_THIRD: ("主队得分", "客队得分", "节次", "比赛时间"),
    # the first one big, the others small under it
    CLOCK: ("比赛时间", "节次"),
    FIELD: ("Time",),
}
# the scorebug's team names when no label is given
DEFAULT_TEAM_LABELS = ("HOME", "AWAY")
# the overlays whose first two labels name the teams (on the lower third: the
# captions of its first two fields, the home and away scores by default)
TEAM_KINDS = (SCOREBUG, LOWER_THIRD)
# the captions of the standard fields when no label is given (other fields:
# their name)
DEFAULT_LABELS = {"主队得分": "主队", "客队得分": "客队", "比赛时间": ""}
# fields that change every second: they don't animate
_TICKING = re.compile(r"time|clock", re.IGNORECASE)

THEMES = ("dark", "light", "minimal", "bold")
DEFAULT_THEME = {SCOREBUG: "dark", LOWER_THIRD: "dark", CLOCK: "dark", FIELD: "minimal"}
POSITIONS = (
    "top-left",
    "top",
    "top-right",
    "left",
    "center",
    "right",
    "bottom-left",
    "bottom",
    "bottom-right",
)
DEFAULT_POSITION = {
    SCOREBUG: "top-left",
    LOWER_THIRD: "bottom-left",
    CLOCK: "top-right",
    FIELD: "center",
}
ANIMATIONS = ("pop", "fade", "slide", "none")
LAYOUTS = ("row", "stack")
FITS = ("width", "none")
TRANSPORTS = ("auto", "sse", "poll")

# font presets: installed fonts only (broadcast machines are often offline,
# so nothing comes from the internet)
FONTS = {
    "sans": '"Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans", '
    '"Liberation Sans", sans-serif',
    "condensed": 'Bahnschrift, "DIN Alternate", "Roboto Condensed", '
    '"Arial Narrow", "Helvetica Neue", Arial, sans-serif',
    "serif": 'Georgia, Cambria, "Times New Roman", "Noto Serif", serif',
    "mono": 'Consolas, "SF Mono", Menlo, "DejaVu Sans Mono", "Liberation Mono", '
    "monospace",
}
DEFAULT_FONT = "condensed"

SCALE_RANGE = (0.25, 4.0)
POLL_MS_RANGE = (100, 10000)
MAX_FIELDS = 32
MAX_NAME_LENGTH = 100
MAX_TEXT_LENGTH = 120

# a font family that can't break out of a quoted CSS string
_FONT_FAMILY = re.compile(r"[A-Za-z0-9][A-Za-z0-9 \-]{0,63}")
_HEX_COLOR = re.compile(
    r"#?([0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})"
)
# CSS named colours
_COLOR_NAMES = frozenset(
    """aliceblue antiquewhite aqua aquamarine azure beige bisque black
    blanchedalmond blue blueviolet brown burlywood cadetblue chartreuse chocolate
    coral cornflowerblue cornsilk crimson cyan darkblue darkcyan darkgoldenrod
    darkgray darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange
    darkorchid darkred darksalmon darkseagreen darkslateblue darkslategray
    darkslategrey darkturquoise darkviolet deeppink deepskyblue dimgray dimgrey
    dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite
    gold goldenrod gray green greenyellow grey honeydew hotpink indianred indigo
    ivory khaki lavender lavenderblush lawngreen lemonchiffon lightblue
    lightcoral lightcyan lightgoldenrodyellow lightgray lightgreen lightgrey
    lightpink lightsalmon lightseagreen lightskyblue lightslategray
    lightslategrey lightsteelblue lightyellow lime limegreen linen magenta
    maroon mediumaquamarine mediumblue mediumorchid mediumpurple mediumseagreen
    mediumslateblue mediumspringgreen mediumturquoise mediumvioletred
    midnightblue mintcream mistyrose moccasin navajowhite navy oldlace olive
    olivedrab orange orangered orchid palegoldenrod palegreen paleturquoise
    palevioletred papayawhip peachpuff peru pink plum powderblue purple
    rebeccapurple red rosybrown royalblue saddlebrown salmon sandybrown seagreen
    seashell sienna silver skyblue slateblue slategray slategrey snow
    springgreen steelblue tan teal thistle tomato turquoise violet wheat white
    whitesmoke yellow yellowgreen transparent""".split()
)
# the colour parameters and the CSS variables they set
COLOR_PARAMS = {
    "bg": "--bg",
    "fg": "--fg",
    "accent": "--accent",
    "home": "--home",
    "away": "--away",
}
# characters that don't belong in a label (control characters)
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")


def parse_color(value: str | None) -> str | None:
    """A CSS colour from a query parameter ("ff0000", "#f00", "red"), or None.
    The "#" is optional because it would start the URL's fragment."""
    if not value:
        return None
    value = value.strip()
    match = _HEX_COLOR.fullmatch(value)
    if match:
        return "#" + match.group(1).lower()
    if value.lower() in _COLOR_NAMES:
        return value.lower()
    return None


def parse_font(value: str | None) -> str:
    """A CSS font-family list: a preset, or an installed family in front of the
    default preset."""
    if not value:
        return FONTS[DEFAULT_FONT]
    value = value.strip()
    if value.lower() in FONTS:
        return FONTS[value.lower()]
    if _FONT_FAMILY.fullmatch(value):
        return f'"{value}", {FONTS[DEFAULT_FONT]}'
    return FONTS[DEFAULT_FONT]


def parse_number(value: str | None, default: float, low: float, high: float) -> float:
    try:
        number = float(value) if value is not None else default
    except ValueError:
        return default
    if not math.isfinite(number):
        return default
    return min(high, max(low, number))


def clean_text(value: str | None, limit: int = MAX_TEXT_LENGTH) -> str:
    """A label without control characters, at most limit characters (it is
    escaped where it is used)."""
    if not value:
        return ""
    return _CONTROL_CHARS.sub("", value).strip()[:limit]


def _choice(value: str | None, choices: tuple[str, ...], default: str) -> str:
    value = (value or "").strip().lower()
    return value if value in choices else default


def _list_param(params, plural: str, singular: str, limit: int) -> list[str]:
    """The values of a list parameter: "plural=a,b,c" and/or repeated
    "singular=a&singular=b" (for names that contain a comma)."""
    values = []
    for raw in params.getlist(plural):
        values += raw.split(",")
    values += params.getlist(singular)
    return [clean_text(value, limit) for value in values][:MAX_FIELDS]


@dataclass(frozen=True)
class OverlayOptions:
    """A built-in overlay's validated options (see parse_options)."""

    kind: str
    fields: tuple[str, ...]
    labels: tuple[str, ...] = ()
    theme: str = "dark"
    position: str = "top-left"
    layout: str = "row"
    animate: str = "pop"
    fit: str = "width"
    scale: float = 1.0
    font: str = FONTS[DEFAULT_FONT]
    colors: dict[str, str] = field(default_factory=dict)  # CSS variable -> colour
    title: str = ""
    subtitle: str = ""
    transport: str = "auto"
    poll_ms: int = 500

    def label(self, index: int, default: str) -> str:
        if index < len(self.labels) and self.labels[index]:
            return self.labels[index]
        return default


def parse_options(kind: str, params) -> OverlayOptions:
    """Validate a built-in overlay's query parameters (a Starlette
    QueryParams or any mapping with getlist()). Unknown parameters are ignored
    and invalid values fall back to the defaults: an overlay URL never fails."""
    fields = [
        name for name in _list_param(params, "fields", "field", MAX_NAME_LENGTH) if name
    ]
    if kind == FIELD:
        name = clean_text(params.get("name"), MAX_NAME_LENGTH)
        if name:
            fields.insert(0, name)
        fields = fields[:1]
    if not fields:
        fields = list(DEFAULT_FIELDS[kind])
    labels = _list_param(params, "labels", "label", MAX_TEXT_LENGTH)
    colors = {}
    for param, variable in COLOR_PARAMS.items():
        color = parse_color(params.get(param))
        if color:
            colors[variable] = color
    return OverlayOptions(
        kind=kind,
        fields=tuple(fields),
        labels=tuple(labels),
        theme=_choice(params.get("theme"), THEMES, DEFAULT_THEME[kind]),
        position=_choice(params.get("position"), POSITIONS, DEFAULT_POSITION[kind]),
        layout=_choice(params.get("layout"), LAYOUTS, "row"),
        animate=_choice(params.get("animate"), ANIMATIONS, "pop"),
        fit=_choice(params.get("fit"), FITS, "width"),
        scale=round(parse_number(params.get("scale"), 1.0, *SCALE_RANGE), 3),
        font=parse_font(params.get("font")),
        colors=colors,
        title=clean_text(params.get("title")),
        subtitle=clean_text(params.get("subtitle")),
        transport=_choice(params.get("transport"), TRANSPORTS, "auto"),
        poll_ms=int(parse_number(params.get("poll"), 500, *POLL_MS_RANGE)),
    )


def overlay_url(
    base: str,
    kind: str,
    fields: list[str] | tuple[str, ...] = (),
    theme: str | None = None,
    scale: float = 1.0,
    labels: list[str] | tuple[str, ...] = (),
    **extra: str,
) -> str:
    """The URL of a built-in overlay (base: "http://host:port"). Parameters that
    are the overlay's defaults are left out; spaces are sent as %20. An empty
    label keeps the overlay's own."""
    params: list[tuple[str, str]] = []
    fields = [name for name in fields if name]
    if kind == FIELD:
        if fields:
            params.append(("name", fields[0]))
    elif fields and tuple(fields) != DEFAULT_FIELDS[kind]:
        if any("," in name for name in fields):
            params += [("field", name) for name in fields]
        else:
            params.append(("fields", ",".join(fields)))
    labels = [label.strip() for label in labels]
    while labels and not labels[-1]:
        labels.pop()
    if any("," in label for label in labels):
        params += [("label", label) for label in labels]
    elif labels:
        params.append(("labels", ",".join(labels)))
    if theme and theme != DEFAULT_THEME[kind]:
        params.append(("theme", theme))
    if not math.isclose(scale, 1.0):
        params.append(("scale", f"{scale:g}"))
    params += [(key, value) for key, value in extra.items() if value]
    url = f"{base}/overlay/{kind}"
    if params:
        url += "?" + urlencode(params, quote_via=quote, safe=",")
    return url


# --- rendering -----------------------------------------------------------------

# the built-in pages load only ScoreSight's own script and styles; the inline
# <style> holds the validated CSS variables
BUILTIN_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; img-src 'self' data:; font-src 'self' data:; "
    "base-uri 'none'; form-action 'none'"
)
NO_CACHE = {"Cache-Control": "no-cache"}

PLACEHOLDER = "–"  # shown until a field has a good reading

_TITLES = {
    SCOREBUG: "Scorebug",
    LOWER_THIRD: "Lower third",
    CLOCK: "Clock",
    FIELD: "Field",
}


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _is_ticking(name: str) -> bool:
    """A field that changes every second (a clock): it doesn't animate."""
    return bool(_TICKING.search(name))


def _value(name: str, css_class: str = "") -> str:
    classes = ["ss-value"] + ([css_class] if css_class else [])
    if _is_ticking(name) or css_class == "ss-clock":
        classes.append("ss-ticking")
    return (
        f'<span class="{" ".join(classes)}" data-field="{_esc(name)}">'
        f"{PLACEHOLDER}</span>"
    )


def _caption(text: str) -> str:
    return f'<span class="ss-caption">{_esc(text)}</span>' if text else ""


def _default_label(name: str) -> str:
    """A field's caption when no label is given: shorter for the standard
    fields ("Home Score" is "Home", the time needs none)."""
    return DEFAULT_LABELS.get(name, name)


def default_team_names(
    kind: str, fields: list[str] | tuple[str, ...]
) -> tuple[str, str]:
    """What a team-names overlay shows as the first two labels when none is
    given: the scorebug's HOME and AWAY, the lower third's first two
    captions."""
    if kind == SCOREBUG:
        return DEFAULT_TEAM_LABELS
    names = (list(fields) + ["", ""])[:2]
    return tuple(_default_label(name) for name in names)


def _render_scorebug(options: OverlayOptions) -> str:
    fields = options.fields
    teams = []
    for index, side in enumerate(("ss-home", "ss-away")):
        if index >= len(fields):
            break
        teams.append(
            f'<div class="ss-team {side}">'
            f'<span class="ss-team-name">'
            f"{_esc(options.label(index, DEFAULT_TEAM_LABELS[index]))}</span>"
            f'<span class="ss-score-cell">{_value(fields[index], "ss-score")}</span>'
            "</div>"
        )
    side = []
    if len(fields) > 2:
        side.append(f'<div class="ss-clock-cell">{_value(fields[2], "ss-clock")}</div>')
    for index in range(3, len(fields)):
        caption = options.label(index, _default_label(fields[index]))
        side.append(
            f'<div class="ss-info">{_caption(caption)}{_value(fields[index])}</div>'
        )
    return (
        f'<div class="ss-panel ss-bug ss-layout-{options.layout}">'
        f'<div class="ss-teams">{"".join(teams)}</div>'
        f'<div class="ss-side">{"".join(side)}</div>'
        "</div>"
    )


def _pairs(options: OverlayOptions, names, first_index: int = 0) -> str:
    return "".join(
        f'<span class="ss-pair">'
        f"{_caption(options.label(first_index + i, _default_label(name)))}"
        f"{_value(name)}</span>"
        for i, name in enumerate(names)
    )


def _render_lower_third(options: OverlayOptions) -> str:
    parts = ['<div class="ss-l3">']
    if options.title:
        parts.append(f'<div class="ss-panel ss-l3-title">{_esc(options.title)}</div>')
    parts.append(
        f'<div class="ss-panel ss-l3-bar">{_pairs(options, options.fields)}</div>'
    )
    if options.subtitle:
        parts.append(f'<div class="ss-panel ss-l3-sub">{_esc(options.subtitle)}</div>')
    parts.append("</div>")
    return "".join(parts)


def _render_clock(options: OverlayOptions) -> str:
    main, rest = options.fields[0], options.fields[1:]
    info = (
        f'<div class="ss-clock-info">{_pairs(options, rest, 1)}</div>' if rest else ""
    )
    caption = _caption(options.labels[0]) if options.labels else ""
    return (
        f'<div class="ss-panel ss-clockbox">{caption}'
        f"{_value(main, 'ss-clock')}{info}</div>"
    )


def _render_field(options: OverlayOptions) -> str:
    caption = _caption(options.labels[0]) if options.labels else ""
    return f'<div class="ss-panel ss-single">{caption}{_value(options.fields[0])}</div>'


_RENDERERS = {
    SCOREBUG: _render_scorebug,
    LOWER_THIRD: _render_lower_third,
    CLOCK: _render_clock,
    FIELD: _render_field,
}


def render_overlay(options: OverlayOptions) -> str:
    """A built-in overlay's page. Every text in it is escaped; the CSS
    variables hold only validated values (the colours on body.ss-overlay, so
    they win over the theme's)."""
    variables = f"--scale: {options.scale:g}; --font: {options.font};"
    colors = " ".join(f"{name}: {value};" for name, value in options.colors.items())
    body = _RENDERERS[options.kind](options)
    return f"""<!DOCTYPE html>
<html lang="en" class="ss-fit-{options.fit}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ScoreSight {_TITLES[options.kind]}</title>
<link rel="stylesheet" href="/overlay/overlay.css">
<style>:root {{ {variables} }} body.ss-overlay {{ {colors} }}</style>
</head>
<body class="ss-overlay ss-kind-{options.kind} ss-theme-{options.theme} ss-pos-{options.position}" data-animate="{options.animate}">
<div class="ss-frame">{body}</div>
<script src="/overlay/client.js" data-transport="{options.transport}" data-poll-ms="{options.poll_ms}"></script>
</body>
</html>
"""


# --- the overlays folder (custom overlays) ----------------------------------------

OVERLAYS_FOLDER = "overlays"
EXAMPLE_NAME = "example.html"
# the file types served from the overlays folder
CUSTOM_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
}
MAX_LISTED = 200
# custom_overlays() looks this many folders deep, at this many entries at most
MAX_DEPTH = 3
MAX_VISITED = 5000
# Windows device names (also with an extension: "nul.html")
_DEVICE_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"{device}{n}" for device in ("COM", "LPT") for n in range(1, 10)]
)


def overlays_dir() -> str:
    """The folder designers put their overlays in (in the data directory)."""
    return os.path.join(get_data_dir(), OVERLAYS_FOLDER)


def ensure_overlays_dir() -> str:
    """Create the overlays folder with the example overlay the first time.
    Never raises (a read-only data directory only logs a warning)."""
    folder = overlays_dir()
    if os.path.isdir(folder):
        return folder
    try:
        os.makedirs(folder, exist_ok=True)
        shutil.copyfile(
            resource_path("overlays", "example.html"),
            os.path.join(folder, EXAMPLE_NAME),
        )
    except OSError as e:
        logger.warning("Could not create the overlays folder %s: %s", folder, e)
    return folder


def custom_overlay_file(relative: str) -> str | None:
    """The file in the overlays folder for a URL path (relative to
    /overlay/custom/), or None when it isn't one that may be served: outside
    the folder (.., absolute paths, links that point out), hidden, a Windows
    stream or device name, a folder, or of an unknown type."""
    if not relative or "\x00" in relative or ":" in relative:
        return None
    parts = re.split(r"[\\/]+", relative)
    if any(
        part in ("", ".", "..")
        or part.startswith(".")
        or part.split(".")[0].upper() in _DEVICE_NAMES
        for part in parts
    ):
        return None
    if os.path.splitext(parts[-1])[1].lower() not in CUSTOM_TYPES:
        return None
    root = os.path.realpath(overlays_dir())
    path = os.path.realpath(os.path.join(root, *parts))
    try:
        inside = os.path.commonpath([root, path]) == root and path != root
    except ValueError:  # another drive (Windows)
        return None
    if not inside or not os.path.isfile(path):
        return None
    return path


def custom_overlays() -> list[str]:
    """The HTML files in the overlays folder (paths relative to it, with "/"),
    sorted; at most MAX_LISTED, without hidden files and folders. Looks at most
    MAX_DEPTH folders deep and at MAX_VISITED entries, so a big folder (say a
    node_modules) can't make it slow."""
    root = overlays_dir()
    found = []
    visited = 0
    for folder, subfolders, files in os.walk(root):
        depth = os.path.relpath(folder, root).count(os.sep) + (folder != root)
        visited += len(subfolders) + len(files)
        if depth >= MAX_DEPTH or visited > MAX_VISITED:
            subfolders[:] = []
        else:
            subfolders[:] = sorted(d for d in subfolders if not d.startswith("."))
        for name in sorted(files):
            if name.startswith(".") or not name.lower().endswith((".html", ".htm")):
                continue
            relative = os.path.relpath(os.path.join(folder, name), root)
            found.append(relative.replace(os.sep, "/"))
            if len(found) >= MAX_LISTED:
                return found
        if visited > MAX_VISITED:
            break
    return found


def custom_overlay_url(base: str, relative: str) -> str:
    return f"{base}/overlay/custom/{quote(relative)}"


# --- bundled files -----------------------------------------------------------------

_asset_cache: dict[str, bytes] = {}


def _asset(name: str) -> bytes:
    """A bundled file from overlays/ (read once)."""
    data = _asset_cache.get(name)
    if data is None:
        if name == "client.js":
            path = resource_path("overlays", "client.js")
        elif name == "client-worker.js":
            path = resource_path("overlays", "client-worker.js")
        elif name == "overlay.css":
            path = resource_path("overlays", "overlay.css")
        else:
            raise KeyError(name)
        with open(path, "rb") as f:
            data = f.read()
        _asset_cache[name] = data
    return data


# --- routes ------------------------------------------------------------------------


@router.get("/events")
async def get_events():
    """The live values as Server-Sent Events: a snapshot, then the changes and
    a heartbeat every few seconds."""
    return StreamingResponse(
        feed.stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/overlay/state")
async def get_state():
    """The live values at once (the polling fallback of client.js)."""
    return JSONResponse(content=feed.snapshot(), headers={"Cache-Control": "no-store"})


@router.get("/overlay/client.js")
async def get_client_js():
    return Response(
        content=_asset("client.js"),
        media_type="text/javascript; charset=utf-8",
        headers=NO_CACHE,
    )


@router.get("/overlay/client-worker.js")
async def get_client_worker_js():
    """The shared worker through which the overlay pages of one browser share
    one /events stream."""
    return Response(
        content=_asset("client-worker.js"),
        media_type="text/javascript; charset=utf-8",
        headers=NO_CACHE,
    )


@router.get("/overlay/overlay.css")
async def get_overlay_css():
    return Response(
        content=_asset("overlay.css"),
        media_type="text/css; charset=utf-8",
        headers=NO_CACHE,
    )


def _builtin_page(kind: str, request: Request) -> HTMLResponse:
    options = parse_options(kind, request.query_params)
    return HTMLResponse(
        content=render_overlay(options),
        headers={"Content-Security-Policy": BUILTIN_CSP, **NO_CACHE},
    )


@router.get("/overlay/scorebug", response_class=HTMLResponse)
async def get_scorebug(request: Request):
    return _builtin_page(SCOREBUG, request)


@router.get("/overlay/lower-third", response_class=HTMLResponse)
async def get_lower_third(request: Request):
    return _builtin_page(LOWER_THIRD, request)


@router.get("/overlay/clock", response_class=HTMLResponse)
async def get_clock(request: Request):
    return _builtin_page(CLOCK, request)


@router.get("/overlay/field", response_class=HTMLResponse)
async def get_field(request: Request):
    return _builtin_page(FIELD, request)


@router.get("/overlay/custom/{path:path}")
def get_custom(path: str):
    # (not async: FastAPI runs it on a worker thread, so the file system calls
    # never hold up the event loop that serves the live streams)
    file = custom_overlay_file(path)
    if file is None:
        return Response("Not found", status_code=404, media_type="text/plain")
    media_type = CUSTOM_TYPES[os.path.splitext(file)[1].lower()]
    return FileResponse(
        file,
        media_type=media_type,
        headers={"X-Content-Type-Options": "nosniff", **NO_CACHE},
    )


def render_index(base: str = "") -> str:
    """The /overlay page: the built-in overlays, the custom ones and the live
    values."""
    builtins = "".join(
        f'<li><a href="{_esc(overlay_url(base, kind))}">{_esc(_TITLES[kind])}</a> '
        f"<code>{_esc(overlay_url(base, kind))}</code></li>"
        for kind in OVERLAY_KINDS
    )
    custom = custom_overlays()
    if custom:
        customs = "".join(
            f'<li><a href="{_esc(custom_overlay_url(base, name))}">{_esc(name)}</a></li>'
            for name in custom
        )
    else:
        customs = "<li>None yet.</li>"
    rows = "".join(
        f"<tr><td>{_esc(name)}</td><td>{_value(name)}</td></tr>"
        for name in feed.snapshot()["fields"]
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ScoreSight overlays</title>
<style>
  body {{ font: 15px/1.5 "Segoe UI", Roboto, Arial, sans-serif; margin: 2em; color: #1e293b; background: #f8fafc; }}
  code {{ font-size: 13px; color: #475569; word-break: break-all; }}
  li {{ margin: .3em 0; }}
  table {{ border-collapse: collapse; }}
  td {{ border-bottom: 1px solid #e2e8f0; padding: .25em 1.5em .25em 0; }}
  .ss-value[data-empty="true"] {{ color: #94a3b8; }}
</style>
</head>
<body>
<h1>ScoreSight overlays</h1>
<p>Transparent pages for a browser source (OBS, vMix, Wirecast, CasparCG...).
Options go in the URL, for example <code>?theme=light&amp;scale=1.5</code>.</p>
<h2>Built-in</h2>
<ul>{builtins}</ul>
<h2>Custom</h2>
<p>From the <code>overlays</code> folder in ScoreSight's data folder.</p>
<ul>{customs}</ul>
<h2>Live values</h2>
<table>{rows}</table>
<p>Live push: <code>/events</code> (Server-Sent Events) &middot; polling: <code>/overlay/state</code></p>
<script src="/overlay/client.js"></script>
</body>
</html>
"""


@router.get("/overlay", response_class=HTMLResponse)
@router.get("/overlay/", response_class=HTMLResponse, include_in_schema=False)
def get_index():
    # (not async: it lists the overlays folder on a worker thread)
    return HTMLResponse(
        content=render_index(),
        headers={"Content-Security-Policy": BUILTIN_CSP, **NO_CACHE},
    )
