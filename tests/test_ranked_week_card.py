"""The weekly-tournament grid card (tiers below Legend I): renderer, /legends week and /legends day."""

import io
import logging
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace as NS

import pytest
from PIL import Image

from clashhero.cogs import legends as cog
from clashhero.services import cards
from clashhero.services import icons as unit_icons

B = cards.ArmyBattle
TAG = "#2PQ8VLYC0"
WEEK = date(2026, 11, 2)  # a Monday: the ranked week Nov 2 05:00 UTC - Nov 9 05:00 UTC
SEASON = "2026-11-02"


def png(color=(250, 200, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (74, 78), (*color, 255)).save(buf, "PNG")
    return buf.getvalue()


def open_png(data: bytes) -> Image.Image:
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


def hit(trophies, stars):
    return B(trophies=trophies, opponent="Opp", stars=stars, destruction=None if stars is None else 80)


def render(attacks=(), defenses=(), cap=30, **kw):
    args = dict(player_name="海神", trophies=975, cap=cap, trophy_icon=png())
    return cards.render_ranked_week(**{**args, **kw}, attacks=list(attacks), defenses=list(defenses))


# ------------------------------------------------------------------ renderer


@pytest.mark.parametrize(
    ("attacks", "defenses", "cap", "rows"),
    [
        (24, 21, 30, 5),  # Legend II
        (10, 3, 24, 4),  # Legend III
        (0, 0, 18, 3),  # Electro: 18 a week
        (8, 3, None, 2),  # unknown tier: as many rows as the battles need
        (0, 0, None, 1),
        (31, 2, 30, 6),  # more battles than the cap: another row
        (500, 0, None, cards.MAX_WEEK_ROWS),
    ],
)
def test_rows(attacks, defenses, cap, rows):
    assert cards.ranked_week_rows(attacks, defenses, cap) == rows


@pytest.mark.parametrize(("cap", "rows"), [(30, 5), (24, 4), (None, 2)])
def test_render_is_a_png_of_the_expected_size(cap, rows):
    im = open_png(render([hit(40, 3)] * 8, [hit(0, 3)] * 5, cap=cap))
    assert im.format == "PNG"
    assert im.size == (cards.WIDTH, cards.ranked_week_height(rows))


def test_tiles_fill_left_to_right_then_down_with_the_halves_apart():
    a0, a5, a6 = (cards.grid_tile_box(0, i) for i in (0, 5, 6))
    d0 = cards.grid_tile_box(1, 0)
    assert a0[1] == a5[1] and a5[0] > a0[0]  # same row
    assert a6[0] == a0[0] and a6[1] > a0[1]  # next row
    gap = cards.grid_tile_box(0, 1)[0] - a0[2]
    assert d0[0] - a5[2] > gap  # a wider gap between attacks and defenses
    assert cards.INNER_L < a0[0] and d0[0] > cards.WIDTH / 2 and cards.grid_tile_box(1, 5)[2] < cards.INNER_R


def _tile_pixel(im: Image.Image, side: int, index: int) -> tuple[int, int, int]:
    """A pixel near a tile's left edge, clear of its text and stars."""
    x0, y0, x1, y1 = cards.grid_tile_box(side, index)
    return im.convert("RGB").getpixel((round(x0 + 5), round((y0 + y1) / 2)))


def test_tile_colours():
    attacks = [hit(40, 3), hit(1, 0), hit(28, 2)]
    defenses = [hit(0, 3), hit(26, 1)]
    im = open_png(render(attacks, defenses))
    r, g, b = _tile_pixel(im, 0, 0)  # 3-star attack: gold
    assert r > 210 and g > 150 and b < 140
    for side, index in ((0, 1), (1, 0)):  # 0-star attack, and a defense the attacker 3-starred: red
        r, g, b = _tile_pixel(im, side, index)
        assert r > 190 and r - g > 35 and r - b > 40
    for side, index in ((0, 2), (1, 1), (0, 4), (1, 4)):  # grey, and pale empty slots
        r, g, b = _tile_pixel(im, side, index)
        assert 180 < r < 240 and abs(r - g) < 18 and abs(r - b) < 28


def test_inferred_tiles_have_no_stars():
    with_stars = open_png(render([hit(30, 2)])).convert("RGB")
    inferred = open_png(render([hit(30, None)])).convert("RGB")
    x0, y0, x1, y1 = cards.grid_tile_box(0, 0)
    stars = (round(x0 + 8), round(y0 + (y1 - y0) * 0.55), round(x1 - 8), round(y1 - 6))
    assert with_stars.crop(stars).tobytes() != inferred.crop(stars).tobytes()
    colours = inferred.crop(stars).getcolors(maxcolors=100_000)
    assert len(colours) < 40  # just the tile's own gradient
    top = (round(x0 + 4), round(y0 + 4), round(x1 - 4), round(y0 + (y1 - y0) * 0.42))
    assert with_stars.crop(top).tobytes() == inferred.crop(top).tobytes()  # the same trophies


def test_overflow_cjk_and_missing_values():
    im = open_png(render([hit(30, 2)] * 33, [hit(0, 3)] * 2, player_name="전설의 용사 海神 with a long name"))
    assert im.height == cards.ranked_week_height(6)
    assert open_png(render(trophies=None, player_name="", trophy_icon=None, rank=43)).width == 1000


# ------------------------------------------------------------------ commands


async def _insert(db, day, kind, trophies, stars=None):
    await db.execute(
        "INSERT INTO legend_events (player_tag, legend_day, season_id, kind, trophies, trophies_after, "
        "occurred_at, stars, destruction) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)",
        (TAG, day, SEASON, kind, trophies, f"{day}T10:00:00+00:00", stars, None if stars is None else 80),
    )


async def _setup(db, monkeypatch, league):
    await db.execute(
        "INSERT INTO players (player_tag, name, town_hall, trophies, league, clan_tag, updated_at) "
        "VALUES (?, 'Amy', 17, 975, ?, '#C', 'x')",
        (TAG, league),
    )
    for i, (kind, t, s) in enumerate(
        [("attack", 40, 3), ("attack", 1, 0), ("defense", 0, 3), ("defense", 26, 1)]
    ):
        day = (WEEK + timedelta(days=i % 2)).isoformat()
        await _insert(db, day, kind, t, s)
    await db.execute(
        "INSERT INTO legend_days (player_tag, legend_day, season_id, start_trophies, end_trophies) "
        "VALUES (?, ?, ?, 908, 975)",
        (TAG, WEEK.isoformat(), SEASON),
    )
    monkeypatch.setattr(cog, "_now", lambda: datetime(2026, 11, 3, 18, tzinfo=UTC))

    async def trophy():
        return png()

    monkeypatch.setattr(unit_icons, "fetch_trophy_icon", trophy)


class FakeResponse:
    def __init__(self):
        self.deferred = False
        self.edits = []

    async def defer(self, **kwargs):
        self.deferred = True

    async def edit_message(self, **kwargs):
        self.edits.append(kwargs)


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)


def _interaction(db):
    return NS(
        client=NS(db=db), guild_id=None, user=NS(id=1), response=FakeResponse(), followup=FakeFollowup()
    )


async def run(db, command, *args):
    interaction = _interaction(db)
    await command.callback(cog.Legends(NS(db=db)), interaction, TAG, None, *args)
    assert interaction.response.deferred
    [msg] = interaction.followup.sent
    return msg


@pytest.fixture
def spy(monkeypatch):
    seen = {}
    real = cards.render_ranked_week

    def render_spy(**kwargs):
        seen["week"] = kwargs
        return real(**kwargs)

    monkeypatch.setattr(cards, "render_ranked_week", render_spy)
    return seen


async def test_week_attaches_the_grid(db, monkeypatch, spy):
    await _setup(db, monkeypatch, "Legend II")
    msg = await run(db, cog.Legends.legends_week, 0)
    embed, file = msg["embed"], msg["file"]
    assert embed.image.url == "attachment://ranked-week.png" and file.filename == "ranked-week.png"
    assert open_png(file.fp.read()).size == (1000, cards.ranked_week_height(5))
    assert embed.fields  # the text embed and its day table stay
    kw = spy["week"]
    assert (kw["player_name"], kw["trophies"], kw["cap"]) == ("Amy", 67, 30)  # the week's battles
    assert [(b.trophies, b.stars) for b in kw["attacks"]] == [(40, 3), (1, 0)]
    assert [(b.trophies, b.stars) for b in kw["defenses"]] == [(0, 3), (26, 1)]


async def test_week_is_text_only_for_legend_one(db, monkeypatch, spy):
    await _setup(db, monkeypatch, "Legend I")
    msg = await run(db, cog.Legends.legends_week, 0)
    assert "file" not in msg and msg["embed"].image.url is None
    assert "week" not in spy


async def test_week_falls_back_to_text(db, monkeypatch, caplog):
    await _setup(db, monkeypatch, "Legend III")

    def broken(**kwargs):
        raise RuntimeError("no fonts")

    monkeypatch.setattr(cards, "render_ranked_week", broken)
    with caplog.at_level(logging.ERROR):
        msg = await run(db, cog.Legends.legends_week, 0)
    assert "file" not in msg and msg["embed"].fields
    assert any("ranked week card" in r.getMessage() for r in caplog.records)


async def test_day_shows_the_week_grid_below_legend_one(db, monkeypatch, spy):
    await _setup(db, monkeypatch, "Legend II")
    msg = await run(db, cog.Legends.legends_day, 0)
    assert "embed" not in msg and msg["file"].filename == "ranked-week.png"  # just the image
    kw = spy["week"]
    assert (kw["cap"], kw["trophies"]) == (30, 67)
    assert [(b.trophies, b.stars) for b in kw["attacks"]] == [(40, 3), (1, 0)]


async def test_day_keeps_the_list_for_legend_one(db, monkeypatch, spy):
    await _setup(db, monkeypatch, "Legend I")
    msg = await run(db, cog.Legends.legends_day, 1)  # yesterday: Nov 2
    assert msg["file"].filename == "legend-day.png"
    assert "week" not in spy


async def test_day_list_for_unknown_league(db, monkeypatch, spy):
    await _setup(db, monkeypatch, None)
    msg = await run(db, cog.Legends.legends_day, 1)
    assert msg["file"].filename == "legend-day.png" and "week" not in spy


async def test_day_grid_falls_back_to_the_day_text(db, monkeypatch):
    await _setup(db, monkeypatch, "Legend II")

    def broken(**kwargs):
        raise RuntimeError("no fonts")

    monkeypatch.setattr(cards, "render_ranked_week", broken)
    msg = await run(db, cog.Legends.legends_day, 1)
    assert "file" not in msg
    assert msg["embed"].title == "Amy · Legend day 2026-11-02" and msg["embed"].fields


async def test_day_grid_on_switching_accounts(db, monkeypatch, spy):
    await _setup(db, monkeypatch, "Legend II")
    view = cog.AltSelectView(1, [(TAG, "Amy")], lambda t: cog._day_reply(NS(db=db), t, "2026-11-03", None))
    switch = _interaction(db)
    view.select._values = [TAG]
    await view._on_select(switch)
    [edit] = switch.response.edits
    [file] = edit["attachments"]
    assert file.filename == "ranked-week.png"
