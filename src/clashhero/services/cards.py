"""Image cards (PNG) in ClashHero's look: the website's near-black surfaces, the crimson to gold
gradient, Cinzel headings and Inter text (both bundled under ``assets/fonts``, SIL OFL).

- ``render_cwl_standings``: a CWL group's standings with clan badges.
- ``render_legend_armies``: one player's legend day, one panel per battle with the army used
  drawn as unit icons.

Rendering is pure and synchronous: no network, no database. Call it through
``asyncio.to_thread`` from a command, with any images already downloaded. ``fetch_badges``
downloads clan badges from the official Clash of Clans asset host; unit icons come from
``services.icons``. Both cache in memory and give up quickly, so a slow or missing image only
means a placeholder on the card. Leagues are shown as text.
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
import os
import re
import threading
import unicodedata
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

from clashhero.services.army import Army, HeroLoadout, Unit
from clashhero.services.icons import font_dir

log = logging.getLogger(__name__)

ASSETS = Path(__file__).resolve().parent.parent / "assets"
FONTS = ASSETS / "fonts"
LOGO_PATH = ASSETS / "logo-256.jpg"

RGB = tuple[int, int, int]

# Palette: docs/assets/css/style.css and utils/style.py.
BG: RGB = (11, 11, 14)
SURFACE: RGB = (22, 22, 29)
SURFACE_2: RGB = (28, 28, 37)
BORDER: RGB = (42, 42, 53)
TEXT: RGB = (239, 233, 223)
MUTED: RGB = (170, 162, 152)
DIM: RGB = (118, 112, 104)
CRIMSON: RGB = (201, 38, 43)
EMBER: RGB = (224, 83, 47)
GOLD: RGB = (229, 172, 82)
GOLD_LIGHT: RGB = (246, 207, 126)
CREAM: RGB = (255, 243, 220)
UP: RGB = (46, 173, 98)
DOWN: RGB = (216, 58, 58)
SAME: RGB = (102, 97, 91)

BRAND_GRADIENT = ((0.0, CRIMSON), (0.45, EMBER), (1.0, GOLD))
TITLE_GRADIENT = ((0.0, CREAM), (0.55, GOLD_LIGHT), (1.0, GOLD))

SCALE = 2  # everything is drawn at 2x and downscaled, for smooth shape edges

# Fonts for characters Inter doesn't have (CJK, Hangul, symbols), if the host has them.
FALLBACK_FONT_PATHS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",  # Debian/Ubuntu: fonts-noto-cjk
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",  # macOS
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "C:/Windows/Fonts/msyh.ttc",  # Windows
    "C:/Windows/Fonts/malgun.ttf",
    "C:/Windows/Fonts/seguisym.ttf",
)

# One render at a time: FreeType faces are shared (cached) and aren't safe across threads.
_RENDER_LOCK = threading.Lock()


def _px(v: float) -> int:
    return round(v * SCALE)


# ------------------------------------------------------------------ fonts


@lru_cache(maxsize=48)
def _font(family: str, size: int, weight: int) -> ImageFont.FreeTypeFont:
    """A bundled variable font at ``size`` px (1x) and ``weight``, loaded at drawing scale."""
    path = FONTS / ("Cinzel-Variable.ttf" if family == "cinzel" else "Inter-Variable.ttf")
    font = ImageFont.truetype(str(path), _px(size), layout_engine=ImageFont.Layout.BASIC)
    values = []
    for axis in font.get_variation_axes():
        name = axis["name"]
        name = name.decode() if isinstance(name, bytes) else str(name)
        if name == "Weight":
            values.append(max(axis["minimum"], min(axis["maximum"], weight)))
        elif name == "Optical size":  # Inter: optical size follows the displayed (1x) size
            values.append(max(axis["minimum"], min(axis["maximum"], size)))
        else:
            values.append(axis["default"])
    if values:
        font.set_variation_by_axes(values)
    return font


def _inter(size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    return _font("inter", size, weight)


def _cinzel(size: int, weight: int = 700) -> ImageFont.FreeTypeFont:
    return _font("cinzel", size, weight)


@lru_cache(maxsize=16)
def _fallback_fonts(size: int) -> tuple[ImageFont.FreeTypeFont, ...]:
    fonts = []
    for path in FALLBACK_FONT_PATHS:
        if not os.path.exists(path):
            continue
        try:
            fonts.append(ImageFont.truetype(path, _px(size), layout_engine=ImageFont.Layout.BASIC))
        except OSError:
            continue
    return tuple(fonts)


def _text_fonts(size: int, weight: int) -> tuple[ImageFont.FreeTypeFont, ...]:
    return (_inter(size, weight), *_fallback_fonts(size))


_NOTDEF_PROBE = "\U0010ffff"  # a noncharacter: every font draws its "missing glyph" box for it
_glyph_cache: dict[tuple, bool] = {}
_notdef_cache: dict[tuple, tuple] = {}


def _glyph_signature(font: ImageFont.FreeTypeFont, ch: str) -> tuple:
    box = font.getbbox(ch)
    im = Image.new("L", (max(1, box[2] - box[0]), max(1, box[3] - box[1])))
    ImageDraw.Draw(im).text((-box[0], -box[1]), ch, font=font, fill=255)
    return box, font.getlength(ch), im.tobytes()


def _has_glyph(font: ImageFont.FreeTypeFont, ch: str) -> bool:
    face = (font.path, font.size, font.index)
    key = (*face, ch)
    if key not in _glyph_cache:
        if face not in _notdef_cache:
            _notdef_cache[face] = _glyph_signature(font, _NOTDEF_PROBE)
        _glyph_cache[key] = _glyph_signature(font, ch) != _notdef_cache[face]
    return _glyph_cache[key]


def _visible(ch: str) -> bool:
    if "\ufe00" <= ch <= "\ufe0f" or "\U000e0100" <= ch <= "\U000e01ef":  # variation selectors
        return False
    return unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co", "Cn")


Run = tuple[str, ImageFont.FreeTypeFont]


def _runs(text: str, fonts: Sequence[ImageFont.FreeTypeFont]) -> list[Run]:
    """Split ``text`` into runs drawable with one font each, first font that has the glyph.
    Characters no font has (emoji, mostly) are left out rather than drawn as boxes, and the
    spaces around them collapse."""
    chars: list[tuple[str, ImageFont.FreeTypeFont]] = []
    for ch in text:
        if ch.isspace():
            if chars and chars[-1][0] != " ":
                chars.append((" ", fonts[0]))
            continue
        font = next((f for f in fonts if _has_glyph(f, ch)), None) if _visible(ch) else None
        if font is not None:
            chars.append((ch, font))
    while chars and chars[-1][0] == " ":
        chars.pop()
    runs: list[Run] = []
    for ch, font in chars:
        if runs and runs[-1][1] is font:
            runs[-1] = (runs[-1][0] + ch, font)
        else:
            runs.append((ch, font))
    return runs


def _runs_width(runs: Sequence[Run]) -> float:
    return sum(font.getlength(s) for s, font in runs)


def fit_runs(text: str, fonts: Sequence[ImageFont.FreeTypeFont], max_width: float) -> list[Run]:
    """Runs for ``text`` cut with an ellipsis to fit ``max_width`` (drawing-scale px)."""
    runs = _runs(text, fonts)
    if _runs_width(runs) <= max_width:
        return runs
    ellipsis = ("…", fonts[0])
    chars = [(ch, font) for s, font in runs for ch in s]
    while chars:
        chars.pop()
        while chars and chars[-1][0] == " ":
            chars.pop()
        regrouped: list[Run] = []
        for ch, font in chars:
            if regrouped and regrouped[-1][1] is font:
                regrouped[-1] = (regrouped[-1][0] + ch, font)
            else:
                regrouped.append((ch, font))
        if _runs_width([*regrouped, ellipsis]) <= max_width:
            return [*regrouped, ellipsis]
    return [ellipsis]


def _draw_runs(
    draw: ImageDraw.ImageDraw,
    x: float,
    baseline: float,
    runs: Sequence[Run],
    fill,
    stroke_width: int = 0,
    stroke_fill=None,
) -> None:
    for s, font in runs:
        draw.text(
            (x, baseline),
            s,
            font=font,
            fill=fill,
            anchor="ls",
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
        x += font.getlength(s)


def _baseline(font: ImageFont.FreeTypeFont, center_y: float) -> float:
    """Baseline that centres capitals/digits on ``center_y``."""
    cap = -font.getbbox("H", anchor="ls")[1]
    return center_y + cap / 2


def _tracked(
    draw: ImageDraw.ImageDraw, x: float, baseline: float, text: str, font, fill, tracking: float, align: str
) -> None:
    """Letter-spaced text; ``align`` is left, right or center around ``x``."""
    width = sum(font.getlength(c) for c in text) + tracking * (len(text) - 1)
    if align == "right":
        x -= width
    elif align == "center":
        x -= width / 2
    for c in text:
        draw.text((x, baseline), c, font=font, fill=fill, anchor="ls")
        x += font.getlength(c) + tracking


# ------------------------------------------------------------------ shapes and fills


def _mix(a: RGB, b: RGB, t: float) -> RGB:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))  # type: ignore[return-value]


def _gradient(size: tuple[int, int], stops: Sequence[tuple[float, RGB]], *, vertical: bool = False):
    n = 256
    colors = []
    for i in range(n):
        t = i / (n - 1)
        for (p0, c0), (p1, c1) in zip(stops, stops[1:], strict=False):
            if t <= p1:
                colors.append(_mix(c0, c1, (t - p0) / (p1 - p0) if p1 > p0 else 0))
                break
        else:
            colors.append(stops[-1][1])
    strip = Image.new("RGB", (1, n) if vertical else (n, 1))
    strip.putdata(colors)
    return strip.resize(size, Image.Resampling.BILINEAR)


@lru_cache(maxsize=32)
def _rounded_mask(w: int, h: int, radius: int) -> Image.Image:
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    return mask


def _panel(canvas: Image.Image, box: tuple[int, int, int, int], radius: int, fill, border, width: int):
    """Rounded panel: ``border`` ``width`` px wide around ``fill``. Each is a colour or an image
    the size of the whole panel."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    outer = _rounded_mask(w, h, radius)
    if isinstance(border, Image.Image):
        canvas.paste(border, (x0, y0), outer)
    else:
        canvas.paste(border, box, outer)
    inner = _rounded_mask(w - 2 * width, h - 2 * width, max(0, radius - width))
    if isinstance(fill, Image.Image):
        canvas.paste(fill.crop((width, width, w - width, h - width)), (x0 + width, y0 + width), inner)
    else:
        canvas.paste(fill, (x0 + width, y0 + width, x1 - width, y1 - width), inner)


@lru_cache(maxsize=4)
def _logo(size: int) -> Image.Image:
    logo = Image.open(LOGO_PATH).convert("RGB").resize((size, size), Image.Resampling.LANCZOS)
    big = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(big).ellipse((6, 6, size * 4 - 7, size * 4 - 7), fill=255)
    logo.putalpha(big.resize((size, size), Image.Resampling.LANCZOS))
    return logo


def _star(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, fill: RGB) -> None:
    points = []
    for i in range(10):
        angle = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        points.append((cx + rr * math.cos(angle), cy + rr * math.sin(angle)))
    draw.polygon(points, fill=fill)


def _movement(draw: ImageDraw.ImageDraw, cx: float, cy: float, movement: str) -> None:
    w, h = _px(14), _px(10)
    if movement == "up":
        draw.polygon([(cx, cy - h / 2), (cx + w / 2, cy + h / 2), (cx - w / 2, cy + h / 2)], fill=UP)
    elif movement == "down":
        draw.polygon([(cx - w / 2, cy - h / 2), (cx + w / 2, cy - h / 2), (cx, cy + h / 2)], fill=DOWN)
    else:
        draw.rounded_rectangle(
            (cx - _px(6), cy - _px(1.5), cx + _px(6), cy + _px(1.5)), radius=_px(1.5), fill=SAME
        )


def _contained_image(data: bytes | None, size: int) -> Image.Image | None:
    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.width * im.height > 2048 * 2048:
                return None
            return ImageOps.contain(im.convert("RGBA"), (size, size), Image.Resampling.LANCZOS)
    except Exception:  # any undecodable image just means "no badge"
        return None


def _placeholder_badge(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], name: str) -> None:
    """Neutral stand-in when a badge couldn't be loaded: a ringed disc with the clan's initial."""
    x0, y0, x1, y1 = box
    inset = _px(3)
    draw.ellipse(
        (x0 + inset, y0 + inset, x1 - inset, y1 - inset), fill=(32, 32, 42), outline=BORDER, width=_px(2)
    )
    initial = next((c for c in name if c.isalnum()), "")
    runs = _runs(initial.upper(), _text_fonts(20, 600)) if initial else []
    if runs:
        width = _runs_width(runs)
        _draw_runs(draw, (x0 + x1) / 2 - width / 2, _baseline(runs[0][1], (y0 + y1) / 2), runs, MUTED)


# ------------------------------------------------------------------ CWL standings card


@dataclass(frozen=True)
class CardRow:
    """One clan in the standings. ``movement``: 'up', 'down' or 'same'; ``destruction``: the
    group's tiebreak total (sum of destruction % over every base attacked)."""

    rank: int
    movement: str
    name: str
    badge: bytes | None
    stars: int
    destruction: float
    tag: str | None = None


WIDTH = 1000
PAD = 36
LOGO_SIZE = 88
HEADER_TOP = 38
LABELS_BASELINE = 172
ROWS_TOP = 186
ROW_H = 64
ROW_GAP = 8
BOTTOM_PAD = 32
RADIUS = 14

# Column positions, relative to the row's left edge (row width = WIDTH - 2 * PAD = 928).
COL_ARROW = 30
COL_RANK = 66
COL_BADGE = 96
BADGE = 48
COL_NAME = 160
COL_STAR = 722  # star icon centre; the number sits right-aligned just before it
COL_DEST = 900  # right edge
NAME_MAX_RIGHT = 630


def card_height(rows: int) -> int:
    if rows == 0:
        return ROWS_TOP + ROW_H + BOTTOM_PAD
    return ROWS_TOP + rows * (ROW_H + ROW_GAP) - ROW_GAP + BOTTOM_PAD


def render_cwl_standings(
    *,
    season: str,
    round_label: str,
    league: str | None,
    rows: Sequence[CardRow | tuple],
    highlight: str | None = None,
) -> bytes:
    """PNG of a CWL group's standings.

    ``season``: "November 2026"; ``round_label``: "4/7"; ``league``: "Master League II" or None;
    ``rows``: ``CardRow`` (or tuples in its field order) in rank order; ``highlight``: the tag
    (or name) of the server's clan, drawn with a crimson to gold border."""
    items = [r if isinstance(r, CardRow) else CardRow(*r) for r in rows]
    with _RENDER_LOCK:
        image = _render_standings(season, round_label, league, items, highlight)
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def _render_standings(
    season: str, round_label: str, league: str | None, rows: list[CardRow], highlight: str | None
) -> Image.Image:
    height = card_height(len(rows))
    canvas = Image.new("RGB", (_px(WIDTH), _px(height)), BG)
    draw = ImageDraw.Draw(canvas, "RGBA")

    _backdrop(canvas)
    _header(canvas, draw, season, round_label, league)

    left = PAD
    labels = _inter(12, 600)
    base = _px(LABELS_BASELINE)
    _tracked(draw, _px(left + (COL_ARROW + COL_RANK) / 2), base, "POS", labels, DIM, _px(1.6), "center")
    _tracked(draw, _px(left + COL_BADGE), base, "CLAN", labels, DIM, _px(1.6), "left")
    _tracked(draw, _px(left + COL_STAR + 10), base, "STARS", labels, DIM, _px(1.6), "right")
    _tracked(draw, _px(left + COL_DEST), base, "DESTRUCTION", labels, DIM, _px(1.6), "right")

    if not rows:
        box = (_px(left), _px(ROWS_TOP), _px(WIDTH - PAD), _px(ROWS_TOP + ROW_H))
        _panel(canvas, box, _px(RADIUS), _row_fill(box[2] - box[0], box[3] - box[1]), BORDER, _px(1))
        font = _inter(18, 500)
        msg = "No standings yet"
        draw.text(
            (_px(WIDTH / 2), _baseline(font, _px(ROWS_TOP + ROW_H / 2))),
            msg,
            font=font,
            fill=MUTED,
            anchor="ms",
        )

    hl = (highlight or "").strip()
    for i, row in enumerate(rows):
        top = ROWS_TOP + i * (ROW_H + ROW_GAP)
        is_hl = bool(hl) and (row.tag == hl if row.tag else row.name == hl)
        _row(canvas, draw, top, row, is_hl)

    return canvas.resize((WIDTH, height), Image.Resampling.LANCZOS)


def _backdrop(canvas: Image.Image) -> None:
    """A soft crimson to gold glow from the top edge, under a thin gradient band."""
    w = canvas.width
    glow_h = _px(230)
    glow = _gradient((w, glow_h), BRAND_GRADIENT)
    fade = Image.new("L", (1, 64))
    fade.putdata([round(44 * (1 - i / 63) ** 2) for i in range(64)])
    canvas.paste(glow, (0, 0), fade.resize((w, glow_h), Image.Resampling.BILINEAR))
    canvas.paste(_gradient((w, _px(4)), BRAND_GRADIENT), (0, 0))


def _draw_logo(canvas: Image.Image, draw: ImageDraw.ImageDraw) -> None:
    """The round ClashHero logo with a faint gold ring, top left of a card."""
    logo_px = _px(LOGO_SIZE)
    lx, ly = _px(PAD), _px(HEADER_TOP)
    canvas.paste(_logo(logo_px), (lx, ly), _logo(logo_px))
    draw.ellipse((lx, ly, lx + logo_px - 1, ly + logo_px - 1), outline=(*GOLD, 70), width=_px(1))


def _title(canvas: Image.Image, x: int, baseline: int, title: str, max_w: int, size: int = 34) -> None:
    """A Cinzel heading in the cream to gold gradient, shrunk (down to 22 px) to fit ``max_w``."""
    while size > 22 and _cinzel(size).getlength(title) > max_w:
        size -= 1
    font = _cinzel(size)
    x0, y0, x1, y1 = font.getbbox(title, anchor="ls")
    mask = Image.new("L", (x1 - x0, y1 - y0), 0)
    ImageDraw.Draw(mask).text((-x0, -y0), title, font=font, fill=255, anchor="ls")
    canvas.paste(_gradient(mask.size, TITLE_GRADIENT, vertical=True), (x + x0, baseline + y0), mask)


def _header(canvas, draw, season: str, round_label: str, league: str | None) -> None:
    _draw_logo(canvas, draw)
    x = _px(PAD + LOGO_SIZE + 24)
    max_w = _px(WIDTH - PAD) - x
    _tracked(draw, x, _px(HEADER_TOP + 18), "GROUP STANDINGS", _inter(13, 600), GOLD, _px(1.9), "left")
    _title(canvas, x, _px(HEADER_TOP + 58), f"Clan War League · {season}", max_w)

    fonts = _text_fonts(18, 500)
    sx, sb = x, _px(HEADER_TOP + 88)
    if league:
        runs = fit_runs(league, fonts, max_w // 2)
        _draw_runs(draw, sx, sb, runs, TEXT)
        sx += _runs_width(runs) + _px(10)
        draw.text((sx, sb), "·", font=fonts[0], fill=DIM, anchor="ls")
        sx += fonts[0].getlength("·") + _px(10)
    _draw_runs(draw, sx, sb, fit_runs(f"Round {round_label}", fonts, max_w - (sx - x)), MUTED)


@lru_cache(maxsize=4)
def _row_fill(w: int, h: int) -> Image.Image:
    return _gradient((w, h), ((0.0, SURFACE_2), (1.0, SURFACE)), vertical=True)


@lru_cache(maxsize=4)
def _highlight_fill(w: int, h: int) -> Image.Image:
    base = _mix(SURFACE_2, BG, 0.2)
    return _gradient(
        (w, h), ((0.0, _mix(base, CRIMSON, 0.2)), (0.6, _mix(base, EMBER, 0.1)), (1.0, _mix(base, GOLD, 0.1)))
    )


@lru_cache(maxsize=4)
def _highlight_border(w: int, h: int) -> Image.Image:
    return _gradient((w, h), BRAND_GRADIENT)


def _row(canvas: Image.Image, draw: ImageDraw.ImageDraw, top: int, row: CardRow, highlighted: bool) -> None:
    left = PAD
    box = (_px(left), _px(top), _px(WIDTH - PAD), _px(top + ROW_H))
    w, h = box[2] - box[0], box[3] - box[1]
    if highlighted:
        _panel(canvas, box, _px(RADIUS), _highlight_fill(w, h), _highlight_border(w, h), _px(2))
    else:
        _panel(canvas, box, _px(RADIUS), _row_fill(w, h), BORDER, _px(1))
    cy = _px(top + ROW_H / 2)

    _movement(draw, _px(left + COL_ARROW), cy, row.movement)

    rank_font = _inter(24, 700)
    draw.text(
        (_px(left + COL_RANK), _baseline(rank_font, cy)),
        str(row.rank),
        font=rank_font,
        fill=GOLD_LIGHT if highlighted else TEXT,
        anchor="ms",
    )

    bx, by = _px(left + COL_BADGE), cy - _px(BADGE) // 2
    badge_box = (bx, by, bx + _px(BADGE), by + _px(BADGE))
    badge = _contained_image(row.badge, _px(BADGE))
    if badge is None:
        _placeholder_badge(draw, badge_box, row.name)
    else:
        canvas.paste(
            badge, (bx + (_px(BADGE) - badge.width) // 2, by + (_px(BADGE) - badge.height) // 2), badge
        )

    name_fonts = _text_fonts(22, 600)
    runs = fit_runs(row.name, name_fonts, _px(NAME_MAX_RIGHT - COL_NAME))
    if not runs:
        runs = fit_runs(row.tag or "?", name_fonts, _px(NAME_MAX_RIGHT - COL_NAME))
    _draw_runs(draw, _px(left + COL_NAME), _baseline(name_fonts[0], cy), runs, TEXT)

    num = _inter(24, 700)
    draw.text(
        (_px(left + COL_STAR - 16), _baseline(num, cy)), str(row.stars), font=num, fill=TEXT, anchor="rs"
    )
    _star(draw, _px(left + COL_STAR), cy, _px(10.5), GOLD)

    dest = _inter(20, 500)
    draw.text(
        (_px(left + COL_DEST), _baseline(dest, cy)),
        f"{row.destruction:,.0f}%",
        font=dest,
        fill=TEXT,
        anchor="rs",
    )


# ------------------------------------------------------------------ legend armies card


@dataclass(frozen=True)
class ArmyBattle:
    """One battle on the legend cards (armies and day).

    ``army``: the army used (the attacker's on a defense, the player's own on an attack), None
    when no army code was recorded. ``stars`` and ``destruction`` are None for battles inferred
    from trophy changes, or ``estimated`` from the trophies (Legend I). ``autofill``: a defense the
    game filled in at the daily reset."""

    trophies: int
    opponent: str | None = None
    opponent_th: int | None = None
    occurred_at: datetime | str | None = None
    stars: int | None = None
    destruction: int | None = None
    army: Army | None = None
    autofill: bool = False
    estimated: bool = False

    @property
    def detailed(self) -> bool:
        return self.stars is not None


ARMY_TOP = 158  # first battle panel
PANEL_GAP = 12
PANEL_PAD = 20  # a panel's inner margin, left and right
HEAD_CY = 34  # centre of a panel's top line, from the panel's top
BODY_TOP = 66  # first icon line, from the panel's top
BODY_BOTTOM = 16
COMPACT_H = 68  # a panel without icons
LINE_H = 62
LINE_GAP = 6
TILE = 52
SMALL_TILE = 40  # a hero's pet and equipment
TILE_GAP = 8
GROUP_PAD = 6
NOTE_H = 36
MAX_CARD_BATTLES = 12
CONTENT_L = PAD + PANEL_PAD
CONTENT_W = WIDTH - 2 * (PAD + PANEL_PAD)

# A panel's top line, absolute x at 1x: name on the left; stars, destruction and trophies right.
NAME_X = CONTENT_L + 42
TROPHY_PILL_W = 94
COL_PILL = WIDTH - PAD - PANEL_PAD - TROPHY_PILL_W  # trophy pill's left edge
COL_PCT = COL_PILL - 20  # destruction, right-aligned
COL_STARS = COL_PCT - 72  # right edge of the last star
STAR_R = 11
STAR_STEP = 26

STAT_W = 158
STAT_GAP = 12
STAT_TOP = HEADER_TOP + 2
STAT_H = 86

KIND_TINT: dict[str, RGB] = {
    "troop": (168, 92, 214),
    "spell": (74, 138, 230),
    "hero": GOLD,
    "pet": (72, 184, 128),
    "equipment": EMBER,
}
LOSS_TEXT: RGB = (240, 98, 90)
GAIN_TEXT: RGB = (86, 204, 130)
EMPTY_STAR: RGB = (48, 48, 60)
GROUP_BORDER: RGB = (50, 50, 63)


def _signed(n: int) -> str:
    """``+12``, ``−32`` (a real minus sign) or ``0``."""
    return f"+{n}" if n > 0 else f"−{-n}" if n < 0 else "0"


def _day_label(day: str | date) -> str:
    """``2026-11-07`` -> ``Sat 7 Nov 2026``."""
    try:
        d = day if isinstance(day, date) else date.fromisoformat(day)
    except ValueError:
        return str(day)
    return f"{d:%a} {d.day} {d:%b %Y}"


def _ago(at: datetime | str | None, now: datetime) -> str | None:
    """``16h 38m ago``, ``2d 4h ago``, ``5m ago`` or ``just now``; None when unknown."""
    if isinstance(at, str):
        try:
            at = datetime.fromisoformat(at)
        except ValueError:
            return None
    if at is None:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    minutes = int((now - at).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h ago"
    return f"{hours}h {minutes}m ago" if hours else f"{minutes}m ago"


def initials(name: str) -> str:
    """Placeholder text for a unit without an icon: ``Super Bowler`` -> ``SB``, ``Healer`` ->
    ``He``, ``Rage Spell`` -> ``Ra``, ``P.E.K.K.A`` -> ``PE``."""
    words = re.findall(r"[A-Za-z0-9]+", name)
    if len(words) > 1 and words[-1].lower() == "spell":
        words = words[:-1]
    if not words:
        return (name.strip()[:1] or "?").upper()
    if len(words) == 1:
        return words[0][:1].upper() + words[0][1:2].lower()
    return (words[0][0] + words[1][0]).upper()


@dataclass(frozen=True)
class _Tile:
    unit: Unit
    size: int
    count: bool = False  # draw the ×count badge


@dataclass
class _Block:
    """Something on an icon line: one bare tile, or a pill ("hero" or "cc") around tiles."""

    tiles: list[_Tile]
    gaps: list[int] = field(default_factory=list)  # space before each tile but the first
    style: str | None = None
    label: str | None = None

    @property
    def label_w(self) -> float:
        return _label_width(self.label) + 8 if self.label else 0

    @property
    def width(self) -> float:
        pad = 2 * GROUP_PAD if self.style else 0
        return pad + self.label_w + sum(t.size for t in self.tiles) + sum(self.gaps)

    def split(self, room: float) -> tuple[_Block, _Block] | None:
        """The leading tiles that fit in ``room`` and the rest, or None if none fit."""
        for n in range(len(self.tiles) - 1, 0, -1):
            head = _Block(self.tiles[:n], self.gaps[: n - 1], self.style, self.label)
            if head.width <= room:
                return head, _Block(self.tiles[n:], self.gaps[n:], self.style)
        return None


Line = list[tuple[float, _Block]]  # (x from the content's left edge, block)


def _label_width(text: str) -> float:
    font = _inter(12, 800)
    return (sum(font.getlength(c) for c in text) + _px(1.2) * (len(text) - 1)) / SCALE


def _flow(blocks: Sequence[_Block], width: float = CONTENT_W) -> list[Line]:
    """Lay blocks out left to right, wrapping onto new lines; a clan castle pill too long for
    the rest of a line is split across two."""
    lines: list[Line] = [[]]
    x = 0.0
    queue = list(blocks)
    while queue:
        block = queue.pop(0)
        line = lines[-1]
        start = x + TILE_GAP if line else 0.0
        fits = start + block.width <= width
        parts = None if fits or block.style != "cc" else block.split(width - start)
        if fits or (not line and parts is None):
            line.append((start, block))
            x = start + block.width
            continue
        if parts:
            line.append((start, parts[0]))
            queue.insert(0, parts[1])
        else:
            queue.insert(0, block)
        lines.append([])
        x = 0.0
    return [line for line in lines if line]


def _hero_block(loadout: HeroLoadout) -> _Block:
    extras = [loadout.pet] if loadout.pet else []
    extras += list(loadout.equipment)
    tiles = [_Tile(loadout.hero, TILE), *(_Tile(u, SMALL_TILE) for u in extras)]
    gaps = [8] + [4] * (len(extras) - 1) if extras else []
    return _Block(tiles, gaps, "hero")


def army_lines(army: Army | None) -> list[Line]:
    """Icon lines for an army: troops; spells with the clan castle pill (flush right when it
    shares the line); heroes, each in a pill with its pet and equipment. Long rows wrap."""
    if not army:
        return []
    lines: list[Line] = []
    troops = [_Block([_Tile(u, TILE, True)]) for u in army.troops]
    if troops:
        lines += _flow(troops)
    spells = [_Block([_Tile(u, TILE, True)]) for u in army.spells]
    cc_units = [*army.cc_troops, *army.cc_spells]
    cc = None
    if cc_units:
        split_at = len(army.cc_troops)
        gaps = [12 if i == split_at else 6 for i in range(1, len(cc_units))]
        cc = _Block([_Tile(u, TILE, True) for u in cc_units], gaps, "cc", "CC")
    if spells or cc:
        flowed = _flow(spells + ([cc] if cc else []))
        last = flowed[-1]
        if cc is not None and len(last) > 1 and last[-1][1] is cc:
            last[-1] = (CONTENT_W - cc.width, cc)
        lines += flowed
    heroes = [_hero_block(h) for h in army.heroes]
    if heroes:
        lines += _flow(heroes)
    return lines


def _panel_height(lines: Sequence[Line]) -> int:
    if not lines:
        return COMPACT_H
    return BODY_TOP + len(lines) * LINE_H + (len(lines) - 1) * LINE_GAP + BODY_BOTTOM


def _battle_lines(battle: ArmyBattle) -> list[Line]:
    return [] if battle.autofill else army_lines(battle.army)


def armies_card_height(battles: Sequence[ArmyBattle]) -> int:
    shown = list(battles)[-MAX_CARD_BATTLES:]
    if not shown:
        return ARMY_TOP + COMPACT_H + BOTTOM_PAD
    panels = sum(_panel_height(_battle_lines(b)) for b in shown) + PANEL_GAP * (len(shown) - 1)
    note = NOTE_H if len(battles) > len(shown) else 0
    return ARMY_TOP + panels + note + BOTTOM_PAD


def render_legend_armies(
    *,
    kind: str,
    player_name: str,
    player_tag: str,
    day: str | date,
    trophies: int | None,
    total: int,
    count: str,
    battles: Sequence[ArmyBattle],
    icons: Mapping[str, bytes] | None = None,
    now: datetime | None = None,
) -> bytes:
    """PNG of one player's legend day, one panel per battle with the army drawn as icons.

    ``kind``: "defense" (the attackers' armies) or "attack" (the player's own); ``day``: the
    legend day ("2026-11-07"); ``trophies``: end-of-day trophies; ``total`` and ``count``: the
    day's trophy total and battle count for that side ("8/8"); ``battles``: chronological, at
    most the last MAX_CARD_BATTLES are drawn; ``icons``: in-game unit name -> image bytes (units
    without one get a tile with their initials); ``now``: for "16h 38m ago"."""
    items = list(battles)
    with _RENDER_LOCK:
        image = _render_armies(
            kind,
            player_name,
            player_tag,
            day,
            trophies,
            total,
            count,
            items,
            icons or {},
            now or datetime.now(UTC),
        )
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


class _IconImages:
    """Decoded icons for one render, by (name, size)."""

    def __init__(self, icons: Mapping[str, bytes]) -> None:
        self.icons = icons
        self._cache: dict[tuple[str, int], Image.Image | None] = {}

    def get(self, name: str, size: int) -> Image.Image | None:
        key = (name, size)
        if key not in self._cache:
            self._cache[key] = _contained_image(self.icons.get(name), size)
        return self._cache[key]


def _render_armies(
    kind: str,
    player_name: str,
    player_tag: str,
    day: str | date,
    trophies: int | None,
    total: int,
    count: str,
    battles: list[ArmyBattle],
    icons: Mapping[str, bytes],
    now: datetime,
) -> Image.Image:
    height = armies_card_height(battles)
    canvas = Image.new("RGB", (_px(WIDTH), _px(height)), BG)
    draw = ImageDraw.Draw(canvas, "RGBA")
    _backdrop(canvas)
    _armies_header(canvas, draw, kind, player_name, player_tag, day, trophies, total, count)

    noun = "defenses" if kind == "defense" else "attacks"
    shown = battles[-MAX_CARD_BATTLES:]
    first = len(battles) - len(shown)
    images = _IconImages(icons)
    top = ARMY_TOP
    if not shown:
        _card_panel(canvas, top, COMPACT_H)
        font = _inter(18, 500)
        msg = f"No {noun} recorded for this legend day"
        draw.text(
            (_px(WIDTH / 2), _baseline(font, _px(top + COMPACT_H / 2))),
            msg,
            font=font,
            fill=MUTED,
            anchor="ms",
        )
    for i, battle in enumerate(shown):
        lines = _battle_lines(battle)
        _battle_panel(canvas, draw, top, first + i + 1, battle, lines, images, now)
        top += _panel_height(lines) + PANEL_GAP
    if first:
        font = _inter(15, 500)
        msg = f"Showing the latest {len(shown)} of {len(battles)} {noun}"
        draw.text(
            (_px(WIDTH / 2), _baseline(font, _px(top + NOTE_H / 2 - 8))),
            msg,
            font=font,
            fill=DIM,
            anchor="ms",
        )
    return canvas.resize((WIDTH, height), Image.Resampling.LANCZOS)


def _card_panel(canvas: Image.Image, top: float, height: int) -> None:
    box = (_px(PAD), _px(top), _px(WIDTH - PAD), _px(top + height))
    _panel(canvas, box, _px(RADIUS), _row_fill(box[2] - box[0], box[3] - box[1]), BORDER, _px(1))


def _armies_header(
    canvas,
    draw,
    kind: str,
    name: str,
    tag: str,
    day: str | date,
    trophies: int | None,
    total: int,
    count: str,
) -> None:
    _draw_logo(canvas, draw)
    x = _px(PAD + LOGO_SIZE + 24)
    stats_left = WIDTH - PAD - 2 * STAT_W - STAT_GAP
    max_w = _px(stats_left - 20) - x
    eyebrow = f"Legend day · {_day_label(day)}".upper()
    _tracked(draw, x, _px(HEADER_TOP + 18), eyebrow, _inter(13, 600), GOLD, _px(1.9), "left")
    _title(canvas, x, _px(HEADER_TOP + 58), "Defense Armies" if kind == "defense" else "Attack Armies", max_w)

    fonts = _text_fonts(18, 600)
    base = _px(HEADER_TOP + 88)
    tag_font = _inter(17, 500)
    tag_w = tag_font.getlength(tag) + _px(24)
    runs = fit_runs(name, fonts, max_w - tag_w) or fit_runs(tag, fonts, max_w - tag_w)
    _draw_runs(draw, x, base, runs, TEXT)
    sx = x + _runs_width(runs) + _px(9)
    draw.text((sx, base), "·", font=fonts[0], fill=DIM, anchor="ls")
    sx += fonts[0].getlength("·") + _px(9)
    draw.text((sx, base), tag, font=tag_font, fill=MUTED, anchor="ls")

    value = f"{trophies:,}" if trophies is not None else "\u2014"
    _stat_tile(canvas, draw, stats_left, "TROPHIES", None, value, TEXT, GOLD)
    color = LOSS_TEXT if total < 0 else GAIN_TEXT if total > 0 else MUTED
    label = "DEFENSES" if kind == "defense" else "ATTACKS"
    _stat_tile(canvas, draw, stats_left + STAT_W + STAT_GAP, label, count, _signed(total), color, None)


def _stat_tile(
    canvas, draw, left: int, label: str, aside: str | None, value: str, color: RGB, cup: RGB | None
):
    """A small panel in the header: a tracked label (with ``aside`` on the right) over a big
    value, optionally after a trophy cup in colour ``cup``."""
    _panel(
        canvas,
        (_px(left), _px(STAT_TOP), _px(left + STAT_W), _px(STAT_TOP + STAT_H)),
        _px(RADIUS),
        _row_fill(_px(STAT_W), _px(STAT_H)),
        BORDER,
        _px(1),
    )
    lb = _px(STAT_TOP + 28)
    _tracked(draw, _px(left + 16), lb, label, _inter(12, 700), DIM, _px(1.6), "left")
    if aside:
        draw.text((_px(left + STAT_W - 16), lb), aside, font=_inter(13, 700), fill=MUTED, anchor="rs")
    font = _inter(30, 700)
    vx, cy = _px(left + 16), _px(STAT_TOP + 58)
    if cup is not None:
        _trophy(draw, vx + _px(10), cy, _px(24), cup)
        vx += _px(28)
    draw.text((vx, _baseline(font, cy)), value, font=font, fill=color, anchor="ls")


def _trophy(draw: ImageDraw.ImageDraw, cx: float, cy: float, s: float, fill) -> None:
    """A trophy cup ``s`` px tall (drawing scale), centred on (cx, cy)."""
    w = max(1, round(s * 0.09))
    draw.rectangle((cx - 0.32 * s, cy - 0.48 * s, cx + 0.32 * s, cy - 0.30 * s), fill=fill)
    draw.chord((cx - 0.32 * s, cy - 0.74 * s, cx + 0.32 * s, cy + 0.12 * s), 0, 180, fill=fill)
    draw.arc((cx - 0.54 * s, cy - 0.44 * s, cx - 0.16 * s, cy - 0.02 * s), 90, 270, fill=fill, width=w)
    draw.arc((cx + 0.16 * s, cy - 0.44 * s, cx + 0.54 * s, cy - 0.02 * s), 270, 90, fill=fill, width=w)
    draw.rectangle((cx - 0.06 * s, cy + 0.06 * s, cx + 0.06 * s, cy + 0.30 * s), fill=fill)
    draw.rounded_rectangle(
        (cx - 0.26 * s, cy + 0.28 * s, cx + 0.26 * s, cy + 0.46 * s), radius=0.06 * s, fill=fill
    )


def _trophy_pill(draw: ImageDraw.ImageDraw, cy: float, value: int) -> None:
    """The battle's trophy change, red for a loss and green for a gain, at the panel's right."""
    if value < 0:
        text_c, fill, border = LOSS_TEXT, _mix(SURFACE, DOWN, 0.2), _mix(BORDER, DOWN, 0.6)
    elif value > 0:
        text_c, fill, border = GAIN_TEXT, _mix(SURFACE, UP, 0.18), _mix(BORDER, UP, 0.55)
    else:
        text_c, fill, border = MUTED, SURFACE_2, BORDER
    h = _px(34)
    x0, x1 = _px(COL_PILL), _px(COL_PILL + TROPHY_PILL_W)
    y0 = _px(cy) - h // 2
    draw.rounded_rectangle((x0, y0, x1, y0 + h), radius=h / 2, fill=fill, outline=border, width=_px(1))
    font = _inter(19, 700)
    text = _signed(value)
    cup = _px(15)
    width = cup + _px(6) + font.getlength(text)
    left = (x0 + x1) / 2 - width / 2
    _trophy(draw, left + cup / 2, _px(cy), cup, text_c)
    draw.text((left + cup + _px(6), _baseline(font, _px(cy))), text, font=font, fill=text_c, anchor="ls")


def _stars_row(draw: ImageDraw.ImageDraw, cy: float, stars: int) -> None:
    for i in range(3):
        cx = COL_STARS - STAR_R - (2 - i) * STAR_STEP
        _star(draw, _px(cx), _px(cy), _px(STAR_R), GOLD if i < stars else EMPTY_STAR)


def _index_badge(draw: ImageDraw.ImageDraw, cy: float, index: int) -> None:
    r = 14
    cx = CONTENT_L + r
    draw.ellipse(
        (_px(cx - r), _px(cy - r), _px(cx + r), _px(cy + r)),
        fill=(255, 255, 255, 8),
        outline=GROUP_BORDER,
        width=_px(1),
    )
    font = _inter(13 if index < 10 else 12, 700)
    draw.text((_px(cx), _baseline(font, _px(cy))), str(index), font=font, fill=MUTED, anchor="ms")


def _head_text(
    draw: ImageDraw.ImageDraw,
    cy: float,
    right: float,
    primary: str,
    th: int | None,
    secondary: str | None,
    primary_color: RGB = TEXT,
) -> None:
    """A panel's top line, left part: name (CJK-safe, cut with an ellipsis), a TH pill and a
    note such as the time, all fitted between NAME_X and ``right``. Compact panels pass a muted
    ``primary_color``; their note is dimmer still."""
    name_fonts = _text_fonts(21, 600)
    small = _inter(15, 500)
    th_font = _inter(12, 700)
    x, limit = _px(NAME_X), _px(right)
    th_text = f"TH{th}" if th else None
    th_w = th_font.getlength(th_text) + _px(16) if th_text else 0
    extras = (th_w + _px(10) if th_text else 0) + (small.getlength(secondary) + _px(14) if secondary else 0)
    room = max(_px(150), limit - x - extras)
    runs = fit_runs(primary, name_fonts, room) or fit_runs("?", name_fonts, room)
    _draw_runs(draw, x, _baseline(name_fonts[0], _px(cy)), runs, primary_color)
    x += _runs_width(runs)
    if th_text:
        x += _px(10)
        h = _px(21)
        y0 = _px(cy) - h // 2
        draw.rounded_rectangle((x, y0, x + th_w, y0 + h), radius=_px(6), fill=(255, 255, 255, 14))
        draw.text((x + th_w / 2, _baseline(th_font, _px(cy))), th_text, font=th_font, fill=MUTED, anchor="ms")
        x += th_w
    if secondary and limit - x > _px(40):
        x += _px(14)
        sec = fit_runs(secondary, [small], limit - x)
        _draw_runs(draw, x, _baseline(small, _px(cy)), sec, MUTED if primary_color == TEXT else DIM)


def _battle_panel(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    top: int,
    index: int,
    battle: ArmyBattle,
    lines: list[Line],
    images: _IconImages,
    now: datetime,
) -> None:
    height = _panel_height(lines)
    _card_panel(canvas, top, height)
    cy = top + HEAD_CY
    _index_badge(draw, cy, index)
    _trophy_pill(draw, cy, battle.trophies)
    ago = _ago(battle.occurred_at, now)
    if battle.autofill:
        _head_text(draw, cy, COL_PILL - 20, "Auto-filled by the game", None, "no battle was played", MUTED)
        return
    if not battle.detailed:
        note = " · ".join(x for x in ("inferred from trophy changes", ago) if x)
        _head_text(draw, cy, COL_PILL - 20, battle.opponent or "Unknown opponent", None, note, MUTED)
        return
    _stars_row(draw, cy, max(0, min(3, battle.stars or 0)))
    pct = _inter(21, 600)
    draw.text(
        (_px(COL_PCT), _baseline(pct, _px(cy))),
        f"{battle.destruction or 0}%",
        font=pct,
        fill=TEXT,
        anchor="rs",
    )
    note = ago if lines else " · ".join(x for x in (ago, "army not recorded") if x)
    right = COL_STARS - 2 * STAR_STEP - 2 * STAR_R - 18
    _head_text(draw, cy, right, battle.opponent or "Unknown", battle.opponent_th, note)
    if not lines:
        return
    y = top + BODY_TOP - 10
    draw.line(
        (_px(CONTENT_L), _px(y), _px(CONTENT_L + CONTENT_W), _px(y)), fill=(255, 255, 255, 12), width=_px(1)
    )
    y = top + BODY_TOP
    for line in lines:
        _draw_line(canvas, draw, line, y, images)
        y += LINE_H + LINE_GAP


def _draw_line(
    canvas: Image.Image, draw: ImageDraw.ImageDraw, line: Line, top: float, images: _IconImages
) -> None:
    cy = top + LINE_H / 2
    for x, block in line:
        tx = CONTENT_L + x
        if block.style:
            _group_pill(canvas, (tx, top, tx + block.width, top + LINE_H), block.style)
            tx += GROUP_PAD
            if block.label:
                font = _inter(12, 800)
                _tracked(
                    draw,
                    _px(tx + 4),
                    _baseline(font, _px(cy)),
                    block.label,
                    font,
                    GOLD_LIGHT,
                    _px(1.2),
                    "left",
                )
                tx += block.label_w
        for i, tile in enumerate(block.tiles):
            if i:
                tx += block.gaps[i - 1]
            _draw_tile(canvas, draw, _px(tx), _px(cy - tile.size / 2), tile, images)
            tx += tile.size


def _group_pill(canvas: Image.Image, box: tuple[float, float, float, float], style: str) -> None:
    """The pill behind a hero group (neutral) or the clan castle (gold edged)."""
    if style == "cc":
        fill, border = _mix(SURFACE, GOLD, 0.08), _mix(BORDER, GOLD, 0.5)
    else:
        fill, border = _mix(SURFACE_2, TEXT, 0.03), GROUP_BORDER
    x0, y0, x1, y1 = (_px(v) for v in box)
    _panel(canvas, (x0, y0, x1, y1), _px(16), fill, border, _px(1))


@lru_cache(maxsize=16)
def _tile_fill(size: int, tint: RGB | None) -> Image.Image:
    if tint is None:
        stops = ((0.0, SURFACE_2), (1.0, SURFACE))
    else:
        stops = ((0.0, _mix(SURFACE_2, tint, 0.30)), (1.0, _mix(SURFACE, tint, 0.10)))
    return _gradient((size, size), stops, vertical=True)


def _draw_tile(
    canvas: Image.Image, draw: ImageDraw.ImageDraw, x: int, y: int, tile: _Tile, images: _IconImages
) -> None:
    """A unit tile at (x, y) (drawing scale): its icon, else a tinted tile with its initials
    ("?" for a unit the bot doesn't know), plus the ×count badge for troops and spells."""
    unit = tile.unit
    s = _px(tile.size)
    radius = _px(10 if tile.size >= TILE else 8)
    tint = KIND_TINT.get(unit.kind, MUTED) if unit.name else None
    border = _mix(BORDER, tint, 0.4) if tint else BORDER
    _panel(canvas, (x, y, x + s, y + s), radius, _tile_fill(s, tint), border, _px(1))
    b = _px(1)
    inner = s - 2 * b
    icon = images.get(unit.name, inner) if unit.name else None
    if icon is not None:
        layer = Image.new("RGBA", (inner, inner), (0, 0, 0, 0))
        layer.alpha_composite(icon, ((inner - icon.width) // 2, (inner - icon.height) // 2))
        alpha = ImageChops.multiply(layer.getchannel("A"), _rounded_mask(inner, inner, radius - b))
        canvas.paste(layer.convert("RGB"), (x + b, y + b), alpha)
    else:
        text = initials(unit.name) if unit.name else "?"
        font = _inter(19 if tile.size >= TILE else 15, 700)
        color = _mix(TEXT, tint, 0.35) if tint else DIM
        cy = y + s / 2 - (_px(4) if tile.count else 0)  # clear of the count badge
        draw.text((x + s / 2, _baseline(font, cy)), text, font=font, fill=color, anchor="ms")
    if tile.count:
        _count_badge(draw, x + s, y + s, unit.count)


def _count_badge(draw: ImageDraw.ImageDraw, right: int, bottom: int, n: int) -> None:
    font = _inter(13, 700)
    text = f"×{n}"
    h, pad = _px(18), _px(5)
    x1, y1 = right - _px(2), bottom - _px(2)
    x0, y0 = x1 - font.getlength(text) - 2 * pad, y1 - h
    draw.rounded_rectangle(
        (x0, y0, x1, y1), radius=h / 2, fill=(10, 10, 13, 232), outline=(255, 255, 255, 34), width=_px(1)
    )
    draw.text(((x0 + x1) / 2, _baseline(font, (y0 + y1) / 2)), text, font=font, fill=CREAM, anchor="ms")


# ------------------------------------------------------------------ the game's battle log window

# The game's "Battle Log" window exactly as the game draws it, and nothing around it: the window is
# the whole image. Geometry and colours are measured from the game's own screens: the Legend I list
# view (a 2000 px wide screenshot, halved to the card's 1000 px) and, for the weekly grid's tiles,
# the Legend II grid view. Text is in the game's font (game-assets/fonts, see
# services.icons.font_dir) with a thin dark outline and a drop shadow; without the font it falls
# back to Inter with the same outline.

GAME_FONT = "Clash_Bold.otf"
MAX_HIT = 40
MAX_DAY_ROWS = 30

INK: RGB = (26, 19, 16)  # outline and drop shadow of the game's text
WHITE: RGB = (255, 255, 255)
PALE_YELLOW: RGB = (248, 245, 200)  # band title, the total, the player's name (#f9f7cc)
ESTIMATE_TEXT: RGB = (222, 214, 205)  # the dimmed label of a row whose stars are estimated

# The window (colours sampled from the game, averaged over small areas).
GW_OUTLINE: RGB = (12, 6, 5)  # hairline around the whole window
GW_FRAME: RGB = (92, 79, 71)  # #5c4f47: frame, and the strip behind the tabs
GW_TAB: RGB = (169, 159, 147)  # #a99f93: an inactive tab, darkening towards its foot
GW_TAB_FOOT: RGB = (130, 117, 109)  # #82756d
GW_TAB_ACTIVE: RGB = (211, 207, 195)  # #d3cfc3: the active tab and the strip under the tabs
GW_BODY: RGB = (240, 232, 219)  # #f0e8db: the window's cream body
GW_SHADOW: RGB = (169, 157, 143)  # #a99d8f: the drop shadow under bars, rows and tiles
GW_BAND_TOP: RGB = (87, 35, 54)  # #572336: the maroon banner, lighter at its foot
GW_BAND_FOOT: RGB = (122, 46, 53)  # #7a2e35
GW_BAND_EDGE: RGB = (46, 21, 14)  # #2e150e
GW_SUB_TOP: RGB = (90, 34, 40)  # #5a2228: the Attacks / Defenses panels
GW_SUB_FOOT: RGB = (99, 41, 45)  # #63292d
GW_TOGGLE: RGB = (169, 165, 154)  # #a9a59a: the list / grid toggle
GW_TOGGLE_SHADE: RGB = (149, 142, 134)  # #958e86: its inner top shade
GW_TOGGLE_RIM: RGB = (227, 222, 214)  # #e3ded6: its lighter lower rim
GW_TOGGLE_ON: RGB = (140, 119, 105)  # #8c7769: the highlighted button
GW_TOGGLE_ON_LIGHT: RGB = (185, 156, 135)  # #b99c87: its lighter face
GW_TOGGLE_LINE: RGB = (70, 62, 55)  # the icons' and button's dark outline
GW_ICON: RGB = (214, 214, 214)  # #d6d6d6: the grid icon's squares
GW_TOGGLE_BAND: RGB = (65, 28, 39)  # #411c27: the toggle in the grid view's banner
GW_TOGGLE_BAND_RIM: RGB = (124, 74, 82)  # its lighter lower rim
GW_TOGGLE_ON_BAND: RGB = (176, 141, 118)  # its raised button, lighter than the list view's
GW_TOGGLE_ON_BAND_LIGHT: RGB = (208, 176, 150)
GW_CLOSE: RGB = (229, 50, 53)  # #e53235: the close button
GW_CLOSE_DARK: RGB = (186, 20, 22)  # #ba1416: its darker rim
GW_CLOSE_LIGHT: RGB = (244, 112, 110)  # its highlight

# Rows (list view) and tiles (grid view): a vertical gradient (stops), the border around the sides
# (and its width), the bottom border (and width), the light line along the top, the lighter lower
# edge. Sampled from the game: neutral #cfbeb6; 3-star attack #e6bb76 to #fbe577 with an orange
# rim #d08a1e; tripled defense #e49c8d to #d6b0a3 with a salmon rim #cb9288.
ROW_STYLES: dict[str, dict] = {
    "grey": {
        "stops": ((0.0, (204, 188, 180)), (0.1, (207, 190, 182)), (1.0, (207, 190, 182))),
        "side": (205, 191, 183),
        "side_w": 1.0,
        "foot": (220, 206, 195),
        "foot_w": 1.5,
        "top": (242, 233, 226),
    },
    "gold": {
        "stops": (
            (0.0, (237, 206, 123)),
            (0.1, (230, 187, 118)),
            (0.45, (231, 190, 118)),
            (1.0, (251, 229, 119)),
        ),
        "side": (212, 130, 27),
        "side_w": 2.0,
        "foot": (208, 138, 30),
        "foot_w": 2.5,
        "top": (250, 237, 169),
    },
    "red": {
        "stops": (
            (0.0, (225, 142, 124)),
            (0.12, (228, 156, 141)),
            (0.5, (214, 176, 163)),
            (0.88, (228, 150, 138)),
            (1.0, (228, 154, 141)),
        ),
        "side": (203, 146, 136),
        "side_w": 1.5,
        "foot": (228, 154, 141),
        "foot_w": 1.5,
        "top": (252, 193, 177),
    },
    "empty": {  # a pale sunken slot (#c9bfb7 to #dacfca), no shadow
        "stops": ((0.0, (201, 191, 183)), (0.18, (215, 205, 199)), (1.0, (218, 207, 202))),
        "side": (205, 195, 187),
        "side_w": 1.0,
        "foot": (222, 212, 206),
        "foot_w": 1.0,
        "top": None,
    },
}
# The weekly grid's tiles, sampled from the game's grid view (a slightly different screen).
TILE_STYLES: dict[str, dict] = {
    "grey": {**ROW_STYLES["grey"], "stops": ((0.0, (205, 188, 178)), (1.0, (197, 185, 172)))},
    "gold": {
        **ROW_STYLES["gold"],
        "stops": ((0.0, (234, 192, 122)), (0.5, (226, 191, 116)), (1.0, (220, 160, 58))),
        "side": (232, 176, 72),
        "foot": (190, 126, 40),
        "top": (250, 220, 120),
    },
    "red": {
        **ROW_STYLES["red"],
        "stops": ((0.0, (225, 157, 142)), (0.5, (216, 174, 161)), (1.0, (199, 135, 121))),
    },
    "empty": ROW_STYLES["empty"],
}
# The battle log's stars: silver-white with a thin dark outline when earned, mid grey when not.
STAR_EARNED = ((246, 250, 251), (205, 220, 228))
STAR_GREY = ((178, 169, 160), (160, 151, 143))  # #a39990 to #aaa099, as sampled
STAR_GREY_LINE: RGB = (140, 131, 123)

# Geometry at 1x (the card is drawn at 2x and halved), measured from the game.
GW_EDGE = 9  # frame around the body
GW_TAB_Y0, GW_BODY_Y = 8, 55  # the tabs stand from y=8 down into the body at y=55
GW_TAB_X0, GW_TAB_W, GW_TAB_GAP, GW_TAB_R = 79.5, 212.0, 5.5, 11
GW_TABS = ("Leaderboard", "Battle Log", "League History", "Town Halls")
GW_ACTIVE_TAB = 1
GW_CLOSE_BOX = (953.0, 6.0, 995.0, 52.0)
BODY_STRIP = 6  # the active tab's colour runs along the top of the body
GW_BODY_R = 10

DAY_BAR = (19.0, 66.0, 872.0, 110.5)  # the player bar
DAY_TOGGLE = (889.0, 66.0, 983.0, 111.0)
DAY_BAND = (16.0, 122.0, 983.5, 230.0)
BAND_H = DAY_BAND[3] - DAY_BAND[1]
SUB_Y = (68.5, 103.5)  # the Attacks / Defenses panels, from the banner's top
SUB_COLS = ((22.5, 492.5), (507.5, 977.0))
DAY_COLS = ((25.5, 486.0), (515.5, 976.0))  # left and right row columns
DAY_ROW_Y, DAY_ROW_PITCH, DAY_ROW_H = 239.0, 45.7, 34.5
ROW_R = 8

WEEK_BAND_Y = 61.0
GRID_PER_SIDE = 6
GRID_X = (27.0, 508.5)  # the first attack and defense tile
GRID_TILE_W, GRID_TILE_H = 71.0, 54.0
GRID_PITCH_X, GRID_PITCH_Y = 79.5, 65.5
GRID_TOP = WEEK_BAND_Y + BAND_H + 10
MAX_WEEK_ROWS = 20


def legend_day_rows(attacks: int, defenses: int, cap: int | None) -> int:
    """Slots per column: the daily cap (8, or 7 on a first Legend I day), else the battle count;
    never fewer than the battles played or 1, never more than MAX_DAY_ROWS."""
    return max(1, min(MAX_DAY_ROWS, max(cap or 0, attacks, defenses)))


def day_slot_box(side: int, index: int) -> tuple[float, float, float, float]:
    """(x0, y0, x1, y1) at 1x of a list row: ``side`` 0 attacks (left), 1 defenses (right)."""
    x0, x1 = DAY_COLS[side]
    y0 = DAY_ROW_Y + index * DAY_ROW_PITCH
    return x0, y0, x1, y0 + DAY_ROW_H


def legend_day_height(rows: int) -> int:
    return round(day_slot_box(0, rows - 1)[3] + 9 + GW_EDGE)


def slot_style(battle: ArmyBattle, kind: str) -> str:
    """The game's colours, in the list and the grid alike: gold for a 3-star attack, red for a
    0-star attack or a defense the attacker 3-starred, grey otherwise. Battles without star data
    go by trophies (a max-trophy attack, a 40-trophy loss); auto-filled defenses are grey."""
    if battle.autofill:
        return "grey"
    if kind == "attack":
        if battle.stars is None:
            return "gold" if battle.trophies >= MAX_HIT else "grey"
        return "gold" if battle.stars == 3 else "red" if battle.stars == 0 else "grey"
    if battle.stars is None:
        return "red" if battle.trophies <= -MAX_HIT else "grey"
    return "red" if battle.stars == 3 else "grey"


@lru_cache(maxsize=32)
def _clash_font(path: str, size: float) -> ImageFont.FreeTypeFont | None:
    try:
        return ImageFont.truetype(path, _px(size), layout_engine=ImageFont.Layout.BASIC)
    except OSError:
        log.warning("Couldn't load the game font %s; using Inter", path, exc_info=True)
        return None


def _game_fonts(size: float) -> tuple[ImageFont.FreeTypeFont, ...]:
    """The game's font when it's installed, then Inter and the CJK fallbacks."""
    path = font_dir() / GAME_FONT
    clash = _clash_font(str(path), size) if path.is_file() else None
    return (*((clash,) if clash else ()), _inter(size, 800), *_fallback_fonts(size))


def _stroke(size: float) -> int:
    return max(2, round(_px(size) * 0.09))


def _game_text(
    draw: ImageDraw.ImageDraw,
    x: float,
    cy: float,
    text: str | Sequence[Run],
    size: float,
    fill: RGB = WHITE,
    anchor: str = "l",
) -> float:
    """Text in the game's style at drawing scale: ``fill`` with a thin dark outline and a drop
    shadow, vertically centred on ``cy``; ``anchor`` l, m or r for ``x``. Returns the width."""
    runs = _runs(text, _game_fonts(size)) if isinstance(text, str) else list(text)
    if not runs:
        return 0
    width = _runs_width(runs)
    x -= width if anchor == "r" else width / 2 if anchor == "m" else 0
    base = _baseline(_game_fonts(size)[0], cy)
    stroke = _stroke(size)
    _draw_runs(draw, x, base + max(2, round(_px(size) * 0.08)), runs, INK, stroke, INK)
    _draw_runs(draw, x, base, runs, fill, stroke, INK)
    return width


def _spaced(text: str, size: float) -> str:
    """``text`` with the symbols no font can draw (emoji, mostly) turned into spaces, so names
    like ``KT⚡ISHRAK`` keep their word break where the game shows an emoji."""
    fonts = _game_fonts(size)
    out = []
    for ch in text:
        drawable = ch.isspace() or not _visible(ch) or any(_has_glyph(f, ch) for f in fonts)
        out.append(ch if drawable else " ")
    return " ".join("".join(out).split())


def _fit_game(text: str, size: float, min_size: float, max_w: float) -> tuple[list[Run], float]:
    """Runs for ``text`` shrunk from ``size`` down to ``min_size`` (in half points) to fit
    ``max_w`` (drawing scale), then cut with an ellipsis. Undrawable symbols keep a space."""
    text = _spaced(text, size)
    s = size
    while s >= min_size:
        runs = _runs(text, _game_fonts(s))
        if _runs_width(runs) + 2 * _stroke(s) <= max_w:
            return runs, s
        s -= 0.5
    return fit_runs(text, _game_fonts(min_size), max_w - 2 * _stroke(min_size)), min_size


class _TrophyIcon:
    """The trophy icon at the sizes a render needs, or a drawn cup when there's no image."""

    def __init__(self, data: bytes | None) -> None:
        self.data = data
        self._sizes: dict[int, Image.Image | None] = {}

    def draw(self, canvas: Image.Image, draw: ImageDraw.ImageDraw, cx: float, cy: float, size: float) -> None:
        """Centred on (cx, cy), ``size`` px tall at 1x."""
        px = _px(size)
        if px not in self._sizes:
            self._sizes[px] = _contained_image(self.data, px)
        icon = self._sizes[px]
        if icon is None:
            _trophy(draw, cx, cy + px * 0.04, px * 1.02, INK)
            _trophy(draw, cx, cy, px * 0.86, GOLD_LIGHT)
            return
        canvas.paste(icon, (round(cx - icon.width / 2), round(cy - icon.height / 2)), icon)


def _star_points(cx: float, cy: float, r: float, inner: float = 0.5) -> list[tuple[float, float]]:
    points = []
    for i in range(10):
        angle = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * inner
        points.append((cx + rr * math.cos(angle), cy + rr * math.sin(angle)))
    return points


def _game_star(canvas: Image.Image, draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, earned: bool):
    """A chunky in-game star (drawing scale): silver-white with a thin dark outline when earned,
    mid grey with a soft grey outline when not."""
    size = math.ceil(2 * r) + 4
    x0, y0 = round(cx - size / 2), round(cy - size / 2)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).polygon(_star_points(cx - x0, cy - y0, r, 0.55), fill=255)
    top, bottom = STAR_EARNED if earned else STAR_GREY
    canvas.paste(_gradient((size, size), ((0.25, top), (0.85, bottom)), vertical=True), (x0, y0), mask)
    points = _star_points(cx, cy, r, 0.55)
    line = INK if earned else STAR_GREY_LINE
    draw.line([*points, points[0], points[1]], fill=line, width=max(2, round(r * 0.15)), joint="curve")


@lru_cache(maxsize=24)
def _bar_image(w: int, h: int, style: str, tile: bool = False) -> Image.Image:
    """A row, tile or bar as the game draws it (drawing scale): rounded, a soft vertical gradient,
    a rim (thicker at the foot), a light line along the top and, except for empty slots, a drop
    shadow under it. The image is taller than ``h`` by the shadow."""
    spec = (TILE_STYLES if tile else ROW_STYLES)[style]
    radius = _px(ROW_R)
    shadow = 0 if style == "empty" else _px(4)
    im = Image.new("RGBA", (w, h + shadow), (0, 0, 0, 0))
    if shadow:
        soft = Image.new("RGBA", (w, h + shadow), (0, 0, 0, 0))
        soft.paste((*GW_SHADOW, 120), (0, shadow, w, h + shadow), _rounded_mask(w, h, radius))
        soft.paste((*GW_SHADOW, 255), (0, shadow // 2, w, h + shadow // 2), _rounded_mask(w, h, radius))
        im.alpha_composite(soft)
    body = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    body.paste((*spec["side"], 255), (0, 0, w, h), _rounded_mask(w, h, radius))
    foot = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    foot.paste((*spec["foot"], 255), (0, 0, w, h), _rounded_mask(w, h, radius))
    body.paste(foot.crop((0, h // 2, w, h)), (0, h // 2), foot.crop((0, h // 2, w, h)))
    sw, fw = max(1, _px(spec["side_w"])), max(1, _px(spec["foot_w"]))
    iw, ih = w - 2 * sw, h - sw - fw
    fill = _gradient((iw, ih), spec["stops"], vertical=True)
    body.paste(fill, (sw, sw), _rounded_mask(iw, ih, max(1, radius - sw)))
    if spec["top"] is not None:
        ImageDraw.Draw(body).line((radius, sw, w - radius, sw), fill=(*spec["top"], 255), width=_px(1))
    im.alpha_composite(body)
    return im


def _paste_bar(
    canvas: Image.Image, box: tuple[float, float, float, float], style: str, tile: bool = False
) -> None:
    x0, y0, x1, y1 = (_px(v) for v in box)
    im = _bar_image(x1 - x0, y1 - y0, style, tile)
    canvas.paste(im, (x0, y0), im)


# -------------------------------------------------------------- window chrome


def _window(canvas: Image.Image, draw: ImageDraw.ImageDraw, height: int) -> None:
    """The window: frame, the four tabs (Battle Log active), the close button and the body."""
    w = _px(WIDTH)
    h = _px(height)
    canvas.paste(GW_FRAME, (0, 0, w, h))
    draw.rectangle((0, 0, w - 1, h - 1), outline=GW_OUTLINE, width=_px(1))

    radius = _px(GW_TAB_R)
    tabs = [GW_TAB_X0 + i * (GW_TAB_W + GW_TAB_GAP) for i in range(len(GW_TABS))]
    for i, tx in enumerate(tabs):
        if i == GW_ACTIVE_TAB:
            continue
        box = (_px(tx), _px(GW_TAB_Y0), _px(tx + GW_TAB_W), _px(GW_BODY_Y) + radius)
        mask = Image.new("L", (box[2] - box[0], box[3] - box[1]), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, mask.width - 1, mask.height - 1),
            radius=radius,
            fill=255,
            corners=(True, True, False, False),
        )
        stops = ((0.0, GW_TAB), (0.45, GW_TAB), (0.85, GW_TAB_FOOT), (1.0, GW_TAB_FOOT))
        canvas.paste(_gradient(mask.size, stops, vertical=True), box[:2], mask)

    bx0, by0, bx1, by1 = _px(GW_EDGE), _px(GW_BODY_Y), _px(WIDTH - GW_EDGE), _px(height - GW_EDGE)
    strip = BODY_STRIP / (height - GW_EDGE - GW_BODY_Y)
    stops = ((0.0, GW_TAB_ACTIVE), (strip, GW_TAB_ACTIVE), (strip * 1.4, GW_BODY), (1.0, GW_BODY))
    body = _gradient((bx1 - bx0, by1 - by0), stops, vertical=True)
    canvas.paste(body, (bx0, by0), _rounded_mask(bx1 - bx0, by1 - by0, _px(GW_BODY_R)))

    tx = tabs[GW_ACTIVE_TAB]
    draw.rounded_rectangle(
        (_px(tx), _px(GW_TAB_Y0), _px(tx + GW_TAB_W), _px(GW_BODY_Y + BODY_STRIP)),
        radius=radius,
        fill=GW_TAB_ACTIVE,
        corners=(True, True, False, False),
    )
    for tx, label in zip(tabs, GW_TABS, strict=True):
        _game_text(draw, _px(tx + GW_TAB_W / 2), _px(29.5), label, 16.5, anchor="m")
    _close_button(canvas, draw)


def _close_button(canvas: Image.Image, draw: ImageDraw.ImageDraw) -> None:
    """The red square close button with a white X."""
    x0, y0, x1, y1 = (_px(v) for v in GW_CLOSE_BOX)
    r = _px(8)
    draw.rounded_rectangle((x0, y0, x1, y1), radius=r, fill=GW_OUTLINE)
    inset = _px(1.5)
    w, h = x1 - x0 - 2 * inset, y1 - y0 - 2 * inset
    face = _gradient((w, h), ((0.0, GW_CLOSE_LIGHT), (0.3, GW_CLOSE), (1.0, GW_CLOSE_DARK)), vertical=True)
    canvas.paste(face, (x0 + inset, y0 + inset), _rounded_mask(w, h, r - inset))
    draw.rounded_rectangle(
        (x0 + inset, y0 + inset, x1 - inset, y1 - inset),
        radius=r - inset,
        outline=GW_CLOSE_DARK,
        width=_px(1.5),
    )
    _game_text(draw, (x0 + x1) / 2, (y0 + y1) / 2 - _px(1), "X", 26, anchor="m")


def _toggle(canvas: Image.Image, draw: ImageDraw.ImageDraw, box: tuple[float, ...], grid: bool) -> None:
    """The list / grid view toggle with the current view on a raised button. In the list view it's
    a grey box beside the player bar (brown button); in the grid view it sits in the banner, a
    dark maroon pill (lighter tan button)."""
    x0, y0, x1, y1 = (_px(v) for v in box)
    r = _px(12)
    if grid:
        draw.rounded_rectangle((x0, y0, x1, y1), radius=r, fill=GW_TOGGLE_BAND_RIM)
        draw.rounded_rectangle((x0, y0, x1, y1 - _px(1.5)), radius=r, fill=GW_TOGGLE_BAND)
        on, on_light, bars = GW_TOGGLE_ON_BAND, GW_TOGGLE_ON_BAND_LIGHT, GW_ICON
    else:
        draw.rounded_rectangle((x0, y0, x1, y1), radius=r, fill=GW_TOGGLE_RIM)
        draw.rounded_rectangle((x0, y0, x1, y1 - _px(2)), radius=r, fill=GW_TOGGLE_SHADE)
        draw.rounded_rectangle((x0, y0 + _px(2.5), x1, y1 - _px(2)), radius=r, fill=GW_TOGGLE)
        on, on_light, bars = GW_TOGGLE_ON, GW_TOGGLE_ON_LIGHT, WHITE
    half = (x1 - x0) / 2
    for i, is_grid in enumerate((False, True)):
        cx = x0 + half * (i + 0.5)
        cy = (y0 + y1) / 2
        if is_grid == grid:
            bw, bh = _px(38), _px(39)
            b = (cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)
            draw.rounded_rectangle(b, radius=_px(8), fill=GW_TOGGLE_LINE)
            inner = (b[0] + _px(2), b[1] + _px(2), b[2] - _px(2), b[3] - _px(2.5))
            draw.rounded_rectangle(inner, radius=_px(6.5), fill=on)
            draw.rounded_rectangle(
                (inner[0] + _px(2.5), inner[1] + _px(2.5), inner[2] - _px(2.5), inner[3] - _px(2.5)),
                radius=_px(5),
                fill=on_light,
            )
        if is_grid:
            s, g = _px(10.5), _px(4)
            for dx in (-1, 1):
                for dy in (-1, 1):
                    sx = cx + dx * (g / 2 + s / 2)
                    sy = cy + dy * (g / 2 + s / 2)
                    draw.rounded_rectangle(
                        (sx - s / 2, sy - s / 2, sx + s / 2, sy + s / 2),
                        radius=_px(2),
                        fill=WHITE if grid else GW_ICON,
                        outline=GW_TOGGLE_LINE,
                        width=_px(1.5),
                    )
        else:
            bw, bh = _px(25), _px(6.5)
            for k in (-1, 0, 1):
                by = cy + k * _px(9)
                draw.rounded_rectangle(
                    (cx - bw / 2, by - bh / 2, cx + bw / 2, by + bh / 2),
                    radius=_px(3),
                    fill=bars,
                    outline=GW_TOGGLE_LINE,
                    width=_px(1.5),
                )


def _band(canvas: Image.Image, draw: ImageDraw.ImageDraw, y0: float) -> None:
    """The maroon banner, with only a very faint diamond texture."""
    x0, x1 = DAY_BAND[0], DAY_BAND[2]
    w, h = _px(x1 - x0), _px(BAND_H)
    band = _gradient(
        (w, h),
        ((0.0, GW_BAND_TOP), (0.3, GW_BAND_TOP), (0.6, GW_BAND_FOOT), (1.0, GW_BAND_FOOT)),
        vertical=True,
    )
    lattice = ImageDraw.Draw(band, "RGBA")  # on the RGB image, so the faint strokes blend
    step = _px(46)
    for k in range(-h // step - 1, w // step + 2):
        lattice.line((k * step, 0, k * step + h, h), fill=(0, 0, 0, 22), width=_px(1.5))
        lattice.line((k * step + h, 0, k * step, h), fill=(0, 0, 0, 22), width=_px(1.5))
        lattice.line(
            (k * step + _px(1.5), 0, k * step + h + _px(1.5), h), fill=(255, 255, 255, 9), width=_px(1)
        )
    _panel(
        canvas,
        (_px(x0), _px(y0), _px(x1), _px(y0 + BAND_H)),
        _px(8),
        band.convert("RGB"),
        GW_BAND_EDGE,
        _px(1),
    )


def _band_totals(canvas, draw, trophy: _TrophyIcon, y0: float, offense: int, defense: int) -> None:
    """The banner's lower line: the darker "Attacks" and "Defenses" panels with their totals."""
    sy0, sy1 = y0 + SUB_Y[0], y0 + SUB_Y[1]
    for (cx0, cx1), label, value in zip(SUB_COLS, ("Attacks", "Defenses"), (offense, defense), strict=True):
        w, h = _px(cx1 - cx0), _px(sy1 - sy0)
        fill = _gradient((w, h), ((0.0, GW_SUB_TOP), (1.0, GW_SUB_FOOT)), vertical=True)
        canvas.paste(fill, (_px(cx0), _px(sy0)), _rounded_mask(w, h, _px(7)))
        cy = _px((sy0 + sy1) / 2)
        _game_text(draw, _px(cx0 + 6), cy, label, 17)
        icon_cx = cx1 - 25.5
        trophy.draw(canvas, draw, _px(icon_cx), cy, 27)
        _game_text(draw, _px(icon_cx - 20), cy, f"{value:+d}", 14.5, anchor="r")


# -------------------------------------------------------------- Legend I: the list view


def render_legend_day(
    *,
    player_name: str,
    day: str | date,
    today: bool,
    trophies: int | None,
    cap: int | None,
    attacks: Sequence[ArmyBattle],
    defenses: Sequence[ArmyBattle],
    trophy_icon: bytes | None = None,
    rank: int | None = None,
    eod: int | None = None,
    eod_rank: int | None = None,
) -> bytes:
    """PNG of a legend day as the game's Legend League battle log (list view).

    ``trophies``: the player's trophies now (the "Total", shown for today); ``eod``: the trophies
    at the end of the previous day for today (the day's start), at the end of that day for a past
    day; ``eod_rank``: the global rank at that end of day; ``rank``: the player's global rank now
    (each shown only when known); ``cap``: the daily attack/defense cap (None for tiers without
    one); ``attacks``/``defenses``: chronological; ``trophy_icon``: image bytes for the trophy (a
    cup is drawn without it)."""
    with _RENDER_LOCK:
        battles = list(attacks), list(defenses)
        image = _render_day(
            player_name, day, today, trophies, cap, *battles, trophy_icon, rank, eod, eod_rank
        )
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def _render_day(
    player_name: str,
    day: str | date,
    today: bool,
    trophies: int | None,
    cap: int | None,
    attacks: list[ArmyBattle],
    defenses: list[ArmyBattle],
    trophy_icon: bytes | None,
    rank: int | None,
    eod: int | None = None,
    eod_rank: int | None = None,
) -> Image.Image:
    rows = legend_day_rows(len(attacks), len(defenses), cap)
    height = legend_day_height(rows)
    canvas = Image.new("RGB", (_px(WIDTH), _px(height)), GW_FRAME)
    draw = ImageDraw.Draw(canvas, "RGBA")
    trophy = _TrophyIcon(trophy_icon)
    _window(canvas, draw, height)
    try:
        d = day if isinstance(day, date) else date.fromisoformat(day)
        day_short = f"{d:%b %d}"
    except ValueError:
        day_short = str(day)
    right = ("Total:", str(trophies) if trophies is not None else "\u2014") if today else ("Day:", day_short)
    _day_player_bar(canvas, draw, trophy, player_name, rank, right, eod, eod_rank)
    _toggle(canvas, draw, DAY_TOGGLE, grid=False)
    offense = sum(b.trophies for b in attacks)
    defense = sum(b.trophies for b in defenses)
    title = "Current League Day" if today else f"League Day · {day_short}"
    _day_band(canvas, draw, trophy, title, offense, defense)

    for side, (kind, battles) in enumerate((("attack", attacks), ("defense", defenses))):
        shown = battles[-rows:]
        for i in range(rows):
            _day_slot(canvas, draw, trophy, day_slot_box(side, i), kind, shown[i] if i < len(shown) else None)
    return canvas.resize((WIDTH, height), Image.Resampling.LANCZOS)


def _day_player_bar(
    canvas,
    draw,
    trophy: _TrophyIcon,
    name: str,
    rank: int | None,
    right: tuple[str, str],
    eod: int | None,
    eod_rank: int | None,
) -> None:
    """The player bar: ``#rank`` (when known) in white and the name in pale yellow on the left;
    ``EOD: 5062 (#2451)`` in the middle (the rank only when known); ``right`` (``Total: 4931``
    for today, ``Day: Nov 07`` for a past day) and a big trophy on the right. Numbers without
    thousands separators, as in the game; the name shrinks, then is cut, to fit."""
    _paste_bar(canvas, DAY_BAR, "grey")
    x0, y0, x1, y1 = DAY_BAR
    cy = _px((y0 + y1) / 2 - 1)
    icon_cx = 838.5
    trophy.draw(canvas, draw, _px(icon_cx), cy, 35)
    label, value = right
    value_w = _game_text(draw, _px(icon_cx - 22), cy, value, 14, PALE_YELLOW, anchor="r")
    label_right = _px(icon_cx - 22) - value_w - _px(8)
    limit = label_right - _game_text(draw, label_right, cy, label, 19, anchor="r")

    if eod is not None:
        parts = [("EOD:", 19, WHITE, 0), (str(eod), 14, PALE_YELLOW, _px(8))]
        if eod_rank:
            parts.append((f"(#{eod_rank})", 14, WHITE, _px(6)))
        widths = [_runs_width(_runs(t, _game_fonts(sz))) + gap for t, sz, _, gap in parts]
        group = sum(widths)
        x = min(_px(480) - group / 2, limit - _px(24) - group)
        limit = x
        for (text, sz, fill, gap), w in zip(parts, widths, strict=True):
            _game_text(draw, x + gap, cy, text, sz, fill)
            x += w

    x = _px(x0 + 13)
    if rank:
        x += _game_text(draw, x, cy, f"#{rank}", 20) + _px(5)
    room = limit - _px(20) - x
    runs, size = _fit_game(name, 20, 12, room)
    if not runs:
        runs, size = _fit_game("?", 20, 20, room)
    _game_text(draw, x, cy, runs, size, PALE_YELLOW)


def _day_band(canvas, draw, trophy: _TrophyIcon, title: str, offense: int, defense: int) -> None:
    y0 = DAY_BAND[1]
    _band(canvas, draw, y0)
    cy = _px(y0 + 33)
    title_end = _px(DAY_BAND[0] + 9) + _game_text(draw, _px(DAY_BAND[0] + 9), cy, title, 22, PALE_YELLOW)
    net = str(offense + defense)  # the game shows the day's total without a plus sign
    net_w = _runs_width(_runs(net, _game_fonts(22)))
    icon_cx = max(468.5, (title_end + _px(20) + net_w) / SCALE + 23)
    trophy.draw(canvas, draw, _px(icon_cx), cy + _px(1), 37)
    _game_text(draw, _px(icon_cx - 19), cy, net, 22, (251, 250, 206), anchor="r")
    _band_totals(canvas, draw, trophy, y0, offense, defense)


def _day_slot(canvas, draw, trophy: _TrophyIcon, box, kind: str, battle: ArmyBattle | None) -> None:
    style = "empty" if battle is None else slot_style(battle, kind)
    _paste_bar(canvas, box, style)
    if battle is None:
        return
    x0, y0 = box[0], box[1]
    cy = _px(y0 + 16)
    trophy.draw(canvas, draw, _px(x0 + 440.5), _px(y0 + 17), 27)
    _game_text(draw, _px(x0 + 418), cy, f"{battle.trophies:+d}", 14, anchor="r")
    name_x = _px(x0 + 6)
    label = "Attack" if kind == "attack" else "Defense"
    if battle.autofill or not battle.detailed:
        _game_text(draw, name_x, cy, "Auto-filled" if battle.autofill else label, 14)
        return
    for i in range(3):
        _game_star(
            canvas, draw, _px(x0 + 288.25 + 21 * i), _px(y0 + 15.5), _px(11.2), i < (battle.stars or 0)
        )
    pct_right = _px(x0 + 272.5)
    pct = f"~{battle.destruction or 0}%" if battle.estimated else f"{battle.destruction or 0}%"
    pct_w = _game_text(draw, pct_right, cy, pct, 14.5, anchor="r")
    if battle.estimated:  # stars and % estimated from the trophies: no opponent known
        _game_text(draw, name_x, cy, label, 14, ESTIMATE_TEXT)
        return
    room = pct_right - max(pct_w, _px(40)) - _px(10) - name_x
    runs, size = _fit_game(battle.opponent or "?", 14.5, 11, room)
    if not runs:
        runs, size = _fit_game("?", 14.5, 14.5, room)
    _game_text(draw, name_x, cy, runs, size)


# -------------------------------------------------------------- below Legend I: the grid view


def ranked_week_rows(attacks: int, defenses: int, cap: int | None) -> int:
    """Grid rows: the weekly cap's (30 -> 5, 24 -> 4), or the battles' without a cap; more when
    there are more battles than the cap; at least 1, at most MAX_WEEK_ROWS."""
    most = max(cap or 0, attacks, defenses)
    return max(1, min(MAX_WEEK_ROWS, math.ceil(most / GRID_PER_SIDE)))


def grid_tile_box(side: int, index: int) -> tuple[float, float, float, float]:
    """(x0, y0, x1, y1) at 1x of a side's ``index``-th tile (0 attacks, 1 defenses): six per row,
    attacks on the left half and defenses on the right, filled left to right, then top to bottom."""
    row, col = divmod(index, GRID_PER_SIDE)
    x0 = GRID_X[side] + col * GRID_PITCH_X
    y0 = GRID_TOP + row * GRID_PITCH_Y
    return x0, y0, x0 + GRID_TILE_W, y0 + GRID_TILE_H


def ranked_week_height(rows: int) -> int:
    return round(grid_tile_box(0, (rows - 1) * GRID_PER_SIDE)[3] + 8 + GW_EDGE)


def render_ranked_week(
    *,
    player_name: str,
    trophies: int | None,
    cap: int | None,
    attacks: Sequence[ArmyBattle],
    defenses: Sequence[ArmyBattle],
    trophy_icon: bytes | None = None,
    rank: int | None = None,
) -> bytes:
    """PNG of a weekly ranked tournament as the game's battle log grid view.

    ``trophies``: the player's tournament trophies (the "Total"); ``cap``: the weekly attack cap
    (None when unknown); ``attacks``/``defenses``: the week's battles, chronological;
    ``trophy_icon``: image bytes for the trophy; ``rank``: when known."""
    with _RENDER_LOCK:
        image = _render_week(player_name, trophies, cap, list(attacks), list(defenses), trophy_icon, rank)
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def _render_week(
    player_name: str,
    trophies: int | None,
    cap: int | None,
    attacks: list[ArmyBattle],
    defenses: list[ArmyBattle],
    trophy_icon: bytes | None,
    rank: int | None,
) -> Image.Image:
    rows = ranked_week_rows(len(attacks), len(defenses), cap)
    height = ranked_week_height(rows)
    canvas = Image.new("RGB", (_px(WIDTH), _px(height)), GW_FRAME)
    draw = ImageDraw.Draw(canvas, "RGBA")
    trophy = _TrophyIcon(trophy_icon)
    _window(canvas, draw, height)
    offense, defense = sum(b.trophies for b in attacks), sum(b.trophies for b in defenses)
    _week_band(canvas, draw, trophy, player_name, rank, trophies, offense, defense)
    slots = rows * GRID_PER_SIDE
    for side, (kind, battles) in enumerate((("attack", attacks), ("defense", defenses))):
        shown = battles[-slots:]
        for i in range(slots):
            _grid_tile(
                canvas, draw, trophy, grid_tile_box(side, i), kind, shown[i] if i < len(shown) else None
            )
    return canvas.resize((WIDTH, height), Image.Resampling.LANCZOS)


def _week_band(
    canvas,
    draw,
    trophy: _TrophyIcon,
    name: str,
    rank: int | None,
    total: int | None,
    offense: int,
    defense: int,
) -> None:
    """The grid view's banner: ``#rank`` and the name on the left, ``Total: 975`` and a big trophy
    in the middle, the view toggle (grid highlighted) on the right, then the attack and defense
    totals."""
    y0 = WEEK_BAND_Y
    _band(canvas, draw, y0)
    _toggle(canvas, draw, (881.0, y0 + 9, 975.0, y0 + 55), grid=True)
    cy = _px(y0 + 33)

    value = str(total) if total is not None else "—"
    size = 22
    label_w = _runs_width(_runs("Total:", _game_fonts(size)))
    value_w = _runs_width(_runs(value, _game_fonts(size)))
    big = 38
    width = label_w + _px(8) + value_w + _px(6 + big)
    x = _px(474) - width / 2
    _game_text(draw, x, cy, "Total:", size)
    _game_text(draw, x + label_w + _px(8), cy, value, size, PALE_YELLOW)
    trophy.draw(canvas, draw, x + width - _px(big / 2), cy, big)

    lx = _px(DAY_BAND[0] + 13)
    if rank:
        lx += _game_text(draw, lx, cy, f"#{rank}", 22) + _px(7)
    room = x - _px(24) - lx
    runs, name_size = _fit_game(name, 18, 12, room)
    if not runs:
        runs, name_size = _fit_game("?", 18, 18, room)
    _game_text(draw, lx, cy, runs, name_size)
    _band_totals(canvas, draw, trophy, y0, offense, defense)


def _grid_tile(canvas, draw, trophy: _TrophyIcon, box, kind: str, battle: ArmyBattle | None) -> None:
    style = "empty" if battle is None else slot_style(battle, kind)
    _paste_bar(canvas, box, style, tile=True)
    if battle is None:
        return
    x0, y0, x1, y1 = box
    cx = (x0 + x1) / 2
    top_cy = _px(y0 + 16)
    text = f"{battle.trophies:+d}" if battle.trophies else "0"
    size, icon = 16, 23
    text_w = _runs_width(_runs(text, _game_fonts(size)))
    width = text_w + _px(3 + icon)
    left = _px(cx + 4) - width / 2
    _game_text(draw, left, top_cy, text, size)
    trophy.draw(canvas, draw, left + width - _px(icon / 2), top_cy, icon)
    if battle.stars is None:  # inferred from a trophy change, or auto-filled: no stars to show
        return
    for i in range(3):
        _game_star(canvas, draw, _px(cx + (i - 1) * 21.5), _px(y0 + 38.5), _px(11), i < battle.stars)


# ------------------------------------------------------------------ legend season card


@dataclass(frozen=True)
class StarCounts:
    """Battles that ended with 0, 1, 2 and 3 stars (``counts[n]``); ``missing``: battles without
    star data (inferred from trophy changes)."""

    counts: tuple[int, int, int, int] = (0, 0, 0, 0)
    missing: int = 0

    @property
    def total(self) -> int:
        return sum(self.counts)

    def share(self, stars: int) -> float:
        return self.counts[stars] / self.total if self.total else 0.0

    @property
    def triple_rate(self) -> float | None:
        return self.share(3) if self.total else None


def star_counts(stars: Iterable[int | None]) -> StarCounts:
    counts = [0, 0, 0, 0]
    missing = 0
    for s in stars:
        if s is None:
            missing += 1
        else:
            counts[max(0, min(3, s))] += 1
    return StarCounts((counts[0], counts[1], counts[2], counts[3]), missing)


@dataclass(frozen=True)
class SeasonPoint:
    """One legend day of the season: end-of-day trophies (None when the day wasn't tracked) and
    the day's net trophies (None without battles)."""

    day: date
    end: int | None = None
    net: int | None = None


@dataclass(frozen=True)
class SeasonCard:
    """What the season card shows. ``days``: every legend day of the season window, in order;
    ``today``: the index of today in ``days`` while the season runs, None once it's over;
    ``trophies``: now (or final); ``start``: when the season started; the per-day averages are
    over the days with battles of that kind."""

    player_name: str
    player_tag: str
    season: str
    days: tuple[SeasonPoint, ...]
    today: int | None = None
    trophies: int | None = None
    start: int | None = None
    start_label: str = "this season"  # "since Nov 5" when tracking began after the season started
    offense_per_day: float | None = None
    defense_per_day: float | None = None
    net_per_day: float | None = None
    attacks: int = 0
    defenses: int = 0
    attack_stars: StarCounts = StarCounts()
    defense_stars: StarCounts = StarCounts()
    clan_name: str | None = None  # the player's clan, shown top right with its badge
    clan_badge: bytes | None = None

    @property
    def played_days(self) -> int:
        return sum(1 for p in self.days if p.net is not None)


# Star breakdown ramps, 0★ to 3★: one hue each, lighter = more stars (validated as ordinal ramps
# on the panel surface: monotone lightness, visible steps, the darkest step still clears 2:1).
OFFENSE_RAMP: tuple[RGB, ...] = ((107, 86, 52), (156, 122, 63), (211, 155, 70), (246, 207, 126))
DEFENSE_RAMP: tuple[RGB, ...] = ((127, 58, 66), (168, 58, 61), (214, 74, 60), (242, 130, 108))
GRID: RGB = (38, 38, 48)  # one step off the panel surface
PANEL_SURFACE: RGB = (26, 26, 34)

SEASON_ROW_Y = 160
HERO_W = 336
TILE_H = 110
CHART_Y = SEASON_ROW_Y + TILE_H + 14
CHART_H = 368
STARS_Y = CHART_Y + CHART_H + 14
STARS_H = 148
SEASON_H = STARS_Y + STARS_H + BOTTOM_PAD
PLOT_L = PAD + 76  # y-axis labels sit in the gutter left of this
PLOT_R = WIDTH - PAD - 26
PLOT_TOP = CHART_Y + 54
PLOT_H = 186
NET_TOP = PLOT_TOP + PLOT_H + 22
NET_H = 64
AXIS_Y = NET_TOP + NET_H + 18


def render_legend_season(card: SeasonCard) -> bytes:
    """PNG of a player's legend season: trophies (hero figure) and per-day averages, the trophy
    line over the whole season window with daily net bars under it, and star breakdowns."""
    with _RENDER_LOCK:
        image = _render_season(card)
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def _render_season(card: SeasonCard) -> Image.Image:
    canvas = Image.new("RGB", (_px(WIDTH), _px(SEASON_H)), BG)
    draw = ImageDraw.Draw(canvas, "RGBA")
    _backdrop(canvas)
    _season_header(canvas, draw, card)
    _season_figures(canvas, draw, card)
    _season_chart(canvas, draw, card)
    half = (WIDTH - 2 * PAD - 12) / 2
    _stars_panel(canvas, draw, PAD, PAD + half, "OFFENSE", "triples", card.attack_stars, OFFENSE_RAMP)
    _stars_panel(
        canvas,
        draw,
        WIDTH - PAD - half,
        WIDTH - PAD,
        "DEFENSE",
        "triples against",
        card.defense_stars,
        DEFENSE_RAMP,
    )
    return canvas.resize((WIDTH, SEASON_H), Image.Resampling.LANCZOS)


def _gradient_runs(canvas: Image.Image, x: int, baseline: int, runs: Sequence[Run]) -> None:
    """Runs filled with the cream to gold title gradient."""
    if not runs:
        return
    boxes = []
    cx = 0.0
    for s, font in runs:
        x0, y0, x1, y1 = font.getbbox(s, anchor="ls")
        boxes.append((cx + x0, y0, cx + x1, y1))
        cx += font.getlength(s)
    left = min(0, min(b[0] for b in boxes))
    top = min(b[1] for b in boxes)
    right = max(cx, max(b[2] for b in boxes))
    bottom = max(b[3] for b in boxes)
    mask = Image.new("L", (math.ceil(right - left) + 2, math.ceil(bottom - top) + 2), 0)
    _draw_runs(ImageDraw.Draw(mask), -left, -top, runs, 255)
    gradient = _gradient(mask.size, TITLE_GRADIENT, vertical=True)
    canvas.paste(gradient, (round(x + left), round(baseline + top)), mask)


CLAN_BADGE = 56
CLAN_NAME_W = 260


def _season_clan(canvas: Image.Image, draw: ImageDraw.ImageDraw, card: SeasonCard) -> int:
    """The player's clan, right-aligned in the header: its badge with the name to its left (the
    name alone without a badge). Returns the block's left edge (drawing scale)."""
    right = _px(WIDTH - PAD)
    cy = _px(HEADER_TOP + LOGO_SIZE / 2)
    badge = _contained_image(card.clan_badge, _px(CLAN_BADGE))
    if badge is not None:
        bx = right - badge.width
        canvas.paste(badge, (bx, cy - badge.height // 2), badge)
        right = bx - _px(12)
    fonts = _text_fonts(18, 600)
    runs = fit_runs(card.clan_name or "", fonts, _px(CLAN_NAME_W))
    if not runs:
        return right
    width = _runs_width(runs)
    _draw_runs(draw, right - width, _baseline(fonts[0], cy), runs, TEXT)
    return round(right - width)


def _season_header(canvas: Image.Image, draw: ImageDraw.ImageDraw, card: SeasonCard) -> None:
    _draw_logo(canvas, draw)
    x = _px(PAD + LOGO_SIZE + 24)
    max_w = _px(WIDTH - PAD) - x
    if card.clan_name:
        max_w = min(max_w, _season_clan(canvas, draw, card) - _px(28) - x)
    total = len(card.days)
    if card.today is not None:
        eyebrow = f"LEGEND SEASON · DAY {card.today + 1} / {total}"
    else:
        eyebrow = f"LEGEND SEASON · FINAL · {total} DAYS"
    _tracked(draw, x, _px(HEADER_TOP + 18), eyebrow, _inter(13, 600), GOLD, _px(1.9), "left")

    name = card.player_name.strip() or card.player_tag
    runs: list[Run] = []
    for size in range(34, 23, -1):
        runs = _runs(name, (_cinzel(size), *_fallback_fonts(size)))
        if _runs_width(runs) <= max_w:
            break
    else:
        runs = fit_runs(name, (_cinzel(24), *_fallback_fonts(24)), max_w)
    if not runs:
        runs = _runs(card.player_tag, (_cinzel(34),))
    _gradient_runs(canvas, x, _px(HEADER_TOP + 58), runs)

    font = _inter(18, 500)
    base = _px(HEADER_TOP + 88)
    draw.text((x, base), card.player_tag, font=font, fill=MUTED, anchor="ls")
    sx = x + font.getlength(card.player_tag) + _px(10)
    draw.text((sx, base), "·", font=font, fill=DIM, anchor="ls")
    sx += font.getlength("·") + _px(10)
    draw.text((sx, base), card.season, font=_inter(18, 600), fill=TEXT, anchor="ls")


def _fmt_avg(value: float | None) -> str:
    if value is None:
        return "—"
    n = round(value)
    return f"+{n}" if n > 0 else f"−{-n}" if n < 0 else "0"


def _season_figures(canvas: Image.Image, draw: ImageDraw.ImageDraw, card: SeasonCard) -> None:
    """The hero trophies panel (brand-gradient edge) and three compact tiles."""
    y0, y1 = SEASON_ROW_Y, SEASON_ROW_Y + TILE_H
    x0, x1 = PAD, PAD + HERO_W
    w, h = _px(x1 - x0), _px(y1 - y0)
    _panel(
        canvas,
        (_px(x0), _px(y0), _px(x1), _px(y1)),
        _px(RADIUS),
        _highlight_fill(w, h),
        _highlight_border(w, h),
        _px(2),
    )
    label = "Trophies now" if card.today is not None else "Final trophies"
    draw.text((_px(x0 + 22), _px(y0 + 32)), label, font=_inter(14, 600), fill=MUTED, anchor="ls")
    hero = _inter(48, 700)
    cy = _px(y0 + 70)
    _trophy(draw, _px(x0 + 38), cy + _px(2), _px(34), GOLD)
    value = f"{card.trophies:,}" if card.trophies is not None else "—"
    draw.text((_px(x0 + 64), _baseline(hero, cy)), value, font=hero, fill=TEXT, anchor="ls")
    if card.trophies is not None and card.start is not None:
        change = card.trophies - card.start
        delta = _inter(20, 700)
        right = _px(x1 - 22)
        text = f"+{change:,}" if change > 0 else f"\u2212{-change:,}" if change < 0 else "0"
        draw.text((right, _baseline(delta, _px(y0 + 54))), text, font=delta, fill=TEXT, anchor="rs")
        _movement(
            draw,
            right - delta.getlength(text) - _px(14),
            _px(y0 + 54),
            "up" if change > 0 else "down" if change < 0 else "same",
        )
        sub = _inter(13, 500)
        draw.text((right, _px(y0 + 80)), card.start_label, font=sub, fill=MUTED, anchor="rs")
        draw.text((right, _px(y0 + 98)), f"from {card.start:,}", font=sub, fill=DIM, anchor="rs")

    tiles = (
        ("Offense per day", card.offense_per_day, f"{card.attacks} attacks"),
        ("Defense per day", card.defense_per_day, f"{card.defenses} defenses"),
        ("Net per day", card.net_per_day, f"{card.played_days} of {len(card.days)} days played"),
    )
    left = x1 + 12
    tile_w = (WIDTH - PAD - left - 2 * 12) / 3
    for i, (label, value, sub) in enumerate(tiles):
        tx0 = left + i * (tile_w + 12)
        box = (_px(tx0), _px(y0), _px(tx0 + tile_w), _px(y1))
        _panel(canvas, box, _px(RADIUS), _row_fill(box[2] - box[0], box[3] - box[1]), BORDER, _px(1))
        draw.text((_px(tx0 + 18), _px(y0 + 32)), label, font=_inter(14, 600), fill=MUTED, anchor="ls")
        font = _inter(30, 700)
        vy = _px(y0 + 66)
        vx = _px(tx0 + 18)
        if value is not None and round(value) != 0:
            _movement(draw, vx + _px(7), vy, "up" if value > 0 else "down")
            vx += _px(22)
        draw.text((vx, _baseline(font, vy)), _fmt_avg(value), font=font, fill=TEXT, anchor="ls")
        draw.text((_px(tx0 + 18), _px(y0 + 96)), sub, font=_inter(13, 500), fill=DIM, anchor="ls")


def _nice_ticks(lo: float, hi: float, target: int = 4) -> list[int]:
    """Round tick values covering lo..hi, about ``target`` intervals."""
    if hi - lo < 40:
        mid = (lo + hi) / 2
        lo, hi = mid - 20, mid + 20
    span = hi - lo
    step = 5000
    for candidate in (5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000):
        if span / candidate <= target:
            step = candidate
            break
    start = math.floor(lo / step) * step
    end = math.ceil(hi / step) * step
    return list(range(int(start), int(end) + 1, step))


def _season_chart(canvas: Image.Image, draw: ImageDraw.ImageDraw, card: SeasonCard) -> None:
    """End-of-day trophies across the whole season window (gaps where a day wasn't tracked),
    with each day's net as a bar on its own baseline underneath."""
    box = (_px(PAD), _px(CHART_Y), _px(WIDTH - PAD), _px(CHART_Y + CHART_H))
    _panel(canvas, box, _px(RADIUS), _row_fill(box[2] - box[0], box[3] - box[1]), BORDER, _px(1))
    _tracked(draw, _px(PAD + 22), _px(CHART_Y + 30), "TROPHIES", _inter(12, 700), DIM, _px(1.6), "left")

    n = max(1, len(card.days))
    slot = (PLOT_R - PLOT_L) / n

    def x_at(i: int) -> float:
        return PLOT_L + (i + 0.5) * slot

    values = [p.end for p in card.days if p.end is not None]
    tick_font = _inter(12, 500)
    if values:
        span = max(values) - min(values)
        ticks = _nice_ticks(min(values) - span * 0.04, max(values) + span * 0.1)
        lo, hi = ticks[0], ticks[-1]
    else:
        ticks, lo, hi = [], 0, 1

    def y_at(v: float) -> float:
        return PLOT_TOP + PLOT_H - (v - lo) / (hi - lo) * PLOT_H

    for t in ticks:
        y = _px(y_at(t))
        draw.line((_px(PLOT_L), y, _px(PLOT_R), y), fill=GRID, width=_px(1))
        draw.text(
            (_px(PLOT_L - 12), _baseline(tick_font, y)), f"{t:,}", font=tick_font, fill=DIM, anchor="rs"
        )

    if card.today is not None:  # today, and the rest of the season still to play
        tx = _px(x_at(card.today))
        draw.line((tx, _px(PLOT_TOP - 8), tx, _px(NET_TOP + NET_H)), fill=(*GOLD, 70), width=_px(1))
        _tracked(draw, tx, _px(PLOT_TOP - 14), "TODAY", _inter(10, 700), GOLD, _px(1.2), "center")

    # Contiguous runs of tracked days: an area wash and a 2px line each.
    runs: list[list[tuple[float, float]]] = [[]]
    for i, p in enumerate(card.days):
        if p.end is None:
            if runs[-1]:
                runs.append([])
            continue
        runs[-1].append((_px(x_at(i)), _px(y_at(p.end))))
    runs = [r for r in runs if r]
    if runs:
        _season_area(canvas, runs)
        for r in runs:
            if len(r) > 1:
                draw.line(r, fill=GOLD, width=_px(2), joint="curve")

    tracked = [(i, p.end) for i, p in enumerate(card.days) if p.end is not None]
    last = tracked[-1][0] if tracked else None
    for i, v in tracked:
        cx, cy = _px(x_at(i)), _px(y_at(v))
        big = i == last
        r = _px(6 if big else 4)
        ring = _px(2)
        if big:
            draw.ellipse(
                (cx - r - _px(6), cy - r - _px(6), cx + r + _px(6), cy + r + _px(6)), fill=(*GOLD, 40)
            )
        draw.ellipse((cx - r - ring, cy - r - ring, cx + r + ring, cy + r + ring), fill=PANEL_SURFACE)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=GOLD_LIGHT if big else GOLD)

    # Direct labels: the latest value and, when it isn't the latest, the peak.
    label_font = _inter(14, 700)
    if tracked:
        li, lv = tracked[-1]
        if li == card.today and li < len(card.days) - 3:  # beside the point, clear of the TODAY rule
            lx, ly = _px(x_at(li) + 14), _baseline(label_font, _px(y_at(lv)))
            draw.text((lx, ly), f"{lv:,}", font=label_font, fill=TEXT, anchor="ls")
        else:
            _value_label(draw, x_at(li), y_at(lv), f"{lv:,}", label_font, TEXT)
        pi, pv = max(tracked, key=lambda t: (t[1], -t[0]))
        if pi != li and pv > lv:
            _value_label(draw, x_at(pi), y_at(pv), f"Peak {pv:,}", _inter(12, 600), MUTED)
    else:
        msg = "No legend days recorded yet"
        f = _inter(16, 500)
        draw.text(
            (_px((PLOT_L + PLOT_R) / 2), _baseline(f, _px(PLOT_TOP + PLOT_H / 2))),
            msg,
            font=f,
            fill=MUTED,
            anchor="ms",
        )

    _season_net_bars(draw, card, x_at, slot)

    # x axis: weekly date ticks (the first legend day of each week, and the last day).
    axis_font = _inter(12, 500)
    marks = sorted({*range(0, n, 7), n - 1})
    for i in marks:
        if i >= len(card.days):
            continue
        d = card.days[i].day
        color = GOLD if card.today == i else DIM
        draw.text(
            (_px(x_at(i)), _px(AXIS_Y + 10)), f"{d:%b} {d.day}", font=axis_font, fill=color, anchor="ms"
        )


def _value_label(draw, x: float, y: float, text: str, font, fill) -> None:
    """A direct label just above a point, kept inside the plot horizontally."""
    w = font.getlength(text) / SCALE
    cx = min(max(x, PLOT_L + w / 2), PLOT_R - w / 2)
    draw.text((_px(cx), _px(y - 14)), text, font=font, fill=fill, anchor="ms")


def _season_area(canvas: Image.Image, runs: list[list[tuple[float, float]]]) -> None:
    """A soft crimson to gold wash under each run of the line."""
    w = _px(PLOT_R - PLOT_L)
    h = _px(PLOT_H)
    ox, oy = _px(PLOT_L), _px(PLOT_TOP)
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    bottom = h
    for r in runs:
        if len(r) < 2:
            continue
        points = [(x - ox, y - oy) for x, y in r]
        md.polygon([*points, (points[-1][0], bottom), (points[0][0], bottom)], fill=255)
    fade = Image.new("L", (1, 64))
    fade.putdata([round(70 - 62 * i / 63) for i in range(64)])  # strongest near the line
    alpha = ImageChops.multiply(mask, fade.resize((w, h), Image.Resampling.BILINEAR))
    canvas.paste(_gradient((w, h), BRAND_GRADIENT), (ox, oy), alpha)


def _season_net_bars(draw: ImageDraw.ImageDraw, card: SeasonCard, x_at, slot: float) -> None:
    """Each day's net trophies as a bar from a zero baseline: up and green for a gain, down and
    red for a loss (direction carries the sign as well as colour). Best and worst days labelled."""
    nets = [(i, p.net) for i, p in enumerate(card.days) if p.net is not None]
    pad = 13  # room for the labels above and below the bars
    up = max([v for _, v in nets if v > 0], default=0)
    down = -min([v for _, v in nets if v < 0], default=0)
    room = NET_H - 2 * pad
    base = NET_TOP + pad + (room * up / (up + down) if up + down else room / 2)
    draw.line((_px(PLOT_L), _px(base), _px(PLOT_R), _px(base)), fill=GRID, width=_px(1))
    _tracked(
        draw,
        _px(PLOT_L - 12),
        _baseline(_inter(10, 700), _px(base)),
        "NET",
        _inter(10, 700),
        DIM,
        _px(1.2),
        "right",
    )
    if not nets:
        return
    scale = (up + down) or 1
    half = room
    bar_w = min(14.0, slot * 0.55)
    radius = _px(3)
    for i, v in nets:
        cx = x_at(i)
        length = max(2.0, abs(v) / scale * half) if v else 0
        if not length:
            draw.rounded_rectangle(
                (_px(cx - bar_w / 2), _px(base - 1), _px(cx + bar_w / 2), _px(base + 1)),
                radius=_px(1),
                fill=SAME,
            )
            continue
        if v > 0:
            box = (_px(cx - bar_w / 2), _px(base - length), _px(cx + bar_w / 2), _px(base))
            draw.rounded_rectangle(box, radius=radius, fill=UP, corners=(True, True, False, False))
        else:
            box = (_px(cx - bar_w / 2), _px(base), _px(cx + bar_w / 2), _px(base + length))
            draw.rounded_rectangle(box, radius=radius, fill=DOWN, corners=(False, False, True, True))
    font = _inter(11, 700)
    best = max(nets, key=lambda t: t[1])
    worst = min(nets, key=lambda t: t[1])
    for i, v in {best, worst}:
        if not v:
            continue
        length = max(2.0, abs(v) / scale * half)
        y = base - length - 5 if v > 0 else base + length + 13
        draw.text((_px(x_at(i)), _px(y)), _signed(v), font=font, fill=MUTED, anchor="ms")


def _stars_panel(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    x0: float,
    x1: float,
    title: str,
    rate_label: str,
    stars: StarCounts,
    ramp: Sequence[RGB],
) -> None:
    """A star breakdown: a tall segmented bar (3★ to 0★, share printed where it fits), a legend
    with the counts, and the triple rate."""
    y0 = STARS_Y
    box = (_px(x0), _px(y0), _px(x1), _px(y0 + STARS_H))
    _panel(canvas, box, _px(RADIUS), _row_fill(box[2] - box[0], box[3] - box[1]), BORDER, _px(1))
    _tracked(draw, _px(x0 + 22), _px(y0 + 30), title, _inter(12, 700), DIM, _px(1.6), "left")
    rate = stars.triple_rate
    if rate is not None:
        small = _inter(13, 500)
        right = _px(x1 - 22)
        draw.text((right, _px(y0 + 30)), rate_label, font=small, fill=MUTED, anchor="rs")
        big = _inter(20, 700)
        draw.text(
            (right - small.getlength(rate_label) - _px(8), _px(y0 + 31)),
            f"{rate:.0%}",
            font=big,
            fill=TEXT,
            anchor="rs",
        )

    bx0, bx1 = x0 + 22, x1 - 22
    by0, by1 = y0 + 46, y0 + 74
    if not stars.total:
        draw.rounded_rectangle(
            (_px(bx0), _px(by0), _px(bx1), _px(by1)), radius=_px(4), fill=(255, 255, 255, 8)
        )
        f = _inter(13, 500)
        msg = "No battles with star data yet"
        draw.text(
            (_px((bx0 + bx1) / 2), _baseline(f, _px((by0 + by1) / 2))), msg, font=f, fill=DIM, anchor="ms"
        )
    else:
        parts = [(s, stars.counts[s]) for s in (3, 2, 1, 0) if stars.counts[s]]
        gap = 2
        width = bx1 - bx0 - gap * (len(parts) - 1)
        x = bx0
        label_font = _inter(13, 700)
        for k, (s, c) in enumerate(parts):
            seg = width * c / stars.total
            end = bx1 if k == len(parts) - 1 else x + seg
            color = ramp[s]
            draw.rounded_rectangle((_px(x), _px(by0), _px(end), _px(by1)), radius=_px(4), fill=color)
            text = f"{c / stars.total:.0%}"
            if label_font.getlength(text) + _px(16) <= _px(end - x):
                lum = 0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]
                ink = (24, 18, 12) if lum > 140 else (255, 255, 255)
                draw.text(
                    (_px(x + 8), _baseline(label_font, _px((by0 + by1) / 2))),
                    text,
                    font=label_font,
                    fill=ink,
                    anchor="ls",
                )
            x = end + gap

    # Legend: swatch, star count, battles.
    ly = y0 + 100
    col_w = (bx1 - bx0) / 4
    count_font = _inter(15, 700)
    for k, s in enumerate((3, 2, 1, 0)):
        lx = bx0 + k * col_w
        draw.rounded_rectangle((_px(lx), _px(ly - 5), _px(lx + 10), _px(ly + 5)), radius=_px(2), fill=ramp[s])
        sx = lx + 18
        digit = _inter(14, 600)
        draw.text((_px(sx), _baseline(digit, _px(ly))), str(s), font=digit, fill=MUTED, anchor="ls")
        sx += digit.getlength(str(s)) / SCALE + 8
        _star(draw, _px(sx), _px(ly), _px(6), MUTED)
        draw.text(
            (_px(sx + 14), _baseline(count_font, _px(ly))),
            str(stars.counts[s]),
            font=count_font,
            fill=TEXT,
            anchor="ls",
        )
    if stars.missing:
        noun = "battle" if stars.missing == 1 else "battles"
        note = f"{stars.missing} {noun} without star data (inferred from trophy changes)"
        draw.text((_px(bx0), _px(y0 + 132)), note, font=_inter(12, 500), fill=DIM, anchor="ls")


# ------------------------------------------------------------------ badge downloads

BADGE_HOSTS = frozenset({"api-assets.clashofclans.com"})
MAX_BADGE_BYTES = 1024 * 1024


class BadgeCache:
    """Downloads clan badge images with a small LRU cache. Badge URLs are content-addressed, so
    entries never go stale. Every failure (timeout, HTTP error, other host) gives ``None``."""

    def __init__(self, *, max_entries: int = 256, timeout: float = 4.0) -> None:
        self.max_entries = max_entries
        self.timeout = timeout
        self._cache: OrderedDict[str, bytes] = OrderedDict()

    @staticmethod
    def allowed(url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme == "https" and (parsed.hostname or "") in BADGE_HOSTS

    async def fetch(self, urls: Iterable[str | None]) -> dict[str, bytes | None]:
        """``{url: image bytes or None}`` for every distinct non-empty URL."""
        out: dict[str, bytes | None] = {}
        missing = []
        for url in dict.fromkeys(u for u in urls if u):
            if url in self._cache:
                self._cache.move_to_end(url)
                out[url] = self._cache[url]
            elif self.allowed(url):
                missing.append(url)
            else:
                out[url] = None
        if missing:
            for url, data in zip(missing, await self._download_all(missing), strict=True):
                out[url] = data
                if data is not None:
                    self._cache[url] = data
                    while len(self._cache) > self.max_entries:
                        self._cache.popitem(last=False)
        return out

    async def _download_all(self, urls: list[str]) -> list[bytes | None]:
        import aiohttp

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout)) as session:
                return list(await asyncio.gather(*(self._safe(session, u) for u in urls)))
        except Exception:
            log.debug("Badge downloads failed", exc_info=True)
            return [None] * len(urls)

    async def _safe(self, session, url: str) -> bytes | None:
        try:
            return await self._download(session, url)
        except Exception as exc:  # timeouts, HTTP errors, oversized bodies: no badge
            log.debug("Badge download failed for %s: %r", url, exc)
            return None

    async def _download(self, session, url: str) -> bytes:
        async with session.get(url) as resp:
            resp.raise_for_status()
            if (resp.content_length or 0) > MAX_BADGE_BYTES:
                raise ValueError("badge image too large")
            data = await resp.read()
            if len(data) > MAX_BADGE_BYTES:
                raise ValueError("badge image too large")
            return data


badge_cache = BadgeCache()


async def fetch_badges(urls: Iterable[str | None]) -> dict[str, bytes | None]:
    return await badge_cache.fetch(urls)
