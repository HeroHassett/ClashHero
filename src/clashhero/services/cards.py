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


def _draw_runs(draw: ImageDraw.ImageDraw, x: float, baseline: float, runs: Sequence[Run], fill) -> None:
    for s, font in runs:
        draw.text((x, baseline), s, font=font, fill=fill, anchor="ls")
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
    """One battle on the armies card.

    ``army``: the army used (the attacker's on a defense, the player's own on an attack), None
    when no army code was recorded. ``stars`` and ``destruction`` are None for battles inferred
    from trophy changes. ``autofill``: a defense the game filled in at the daily reset."""

    trophies: int
    opponent: str | None = None
    opponent_th: int | None = None
    occurred_at: datetime | str | None = None
    stars: int | None = None
    destruction: int | None = None
    army: Army | None = None
    autofill: bool = False

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
