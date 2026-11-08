"""The /legends day image card, drawn like the game's Legend League battle log."""

import io
import logging
from datetime import UTC, datetime
from types import SimpleNamespace as NS

import discord
import pytest
from PIL import Image

from clashhero.cogs import legends as cog
from clashhero.services import cards
from clashhero.services import icons as unit_icons

B = cards.ArmyBattle
DAY = "2026-11-07"
SEASON = "2026-11-30"
TAG, ALT = "#2PQ8VLYC0", "#2YYLQ"


def png(color=(250, 200, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (74, 78), (*color, 255)).save(buf, "PNG")
    return buf.getvalue()


def open_png(data: bytes) -> Image.Image:
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


def hit(trophies, opp="Opp", stars=2, pct=80):
    return B(trophies=trophies, opponent=opp, opponent_th=17, stars=stars, destruction=pct)


def render(attacks=(), defenses=(), cap=8, **kw):
    args = dict(player_name="JoNico", day=DAY, today=True, trophies=5062, cap=cap, trophy_icon=png())
    return cards.render_legend_day(**{**args, **kw}, attacks=list(attacks), defenses=list(defenses))


# ------------------------------------------------------------------ renderer


def test_render_is_a_png_of_the_expected_size():
    im = open_png(render([hit(40, stars=3, pct=100)] * 8, [hit(-40, stars=3, pct=100)] * 8))
    assert im.format == "PNG"
    assert im.size == (cards.WIDTH, cards.legend_day_height(8))


@pytest.mark.parametrize(
    ("attacks", "defenses", "cap", "rows"),
    [
        (0, 4, 8, 8),  # Legend I: 8 slots, unplayed ones empty
        (3, 2, 7, 7),  # first Legend I day after promotion
        (5, 3, None, 5),  # no daily cap: as many as played
        (0, 0, None, 1),
        (9, 2, 8, 9),  # never hide a battle
        (80, 0, None, cards.MAX_DAY_ROWS),
    ],
)
def test_slot_counts(attacks, defenses, cap, rows):
    assert cards.legend_day_rows(attacks, defenses, cap) == rows


def test_height_follows_the_slot_count():
    heights = [open_png(render(cap=c)).height for c in (7, 8)]
    assert heights == [cards.legend_day_height(7), cards.legend_day_height(8)]
    assert heights[0] < heights[1]
    lower = open_png(render([hit(30)] * 5, [hit(12)] * 3, cap=None))
    assert lower.height == cards.legend_day_height(5)


def test_slot_styles():
    assert cards.slot_style(hit(40, stars=3), "attack") == "gold"
    assert cards.slot_style(hit(32, stars=3), "attack") == "gold"  # a triple in a lower tier
    assert cards.slot_style(hit(30, stars=2), "attack") == "grey"
    assert cards.slot_style(B(trophies=40), "attack") == "gold"  # inferred: max trophies
    assert cards.slot_style(B(trophies=24), "attack") == "grey"
    assert cards.slot_style(hit(-40, stars=3), "defense") == "red"
    assert cards.slot_style(B(trophies=-40), "defense") == "red"
    assert cards.slot_style(hit(-25, stars=2), "defense") == "grey"
    assert cards.slot_style(hit(12, stars=3), "defense") == "grey"  # below Legend I: a gain
    assert cards.slot_style(B(trophies=-40, autofill=True), "defense") == "grey"


def _slot_pixel(im: Image.Image, col: int, row: int) -> tuple[int, int, int]:
    """A pixel inside a slot, left of its text."""
    x = cards.INNER_L + col * (cards.COL_W + cards.COL_GAP) + 8
    y = cards.SLOTS_Y + row * (cards.SLOT_H + cards.SLOT_GAP) + cards.SLOT_H * 0.65
    return im.convert("RGB").getpixel((round(x), round(y)))


def test_row_colours():
    attacks = [hit(40, stars=3, pct=100), hit(23, stars=2, pct=73)]
    defenses = [hit(-40, stars=3, pct=100), hit(-12, stars=1, pct=70)]
    im = open_png(render(attacks, defenses))
    r, g, b = _slot_pixel(im, 0, 0)  # 3-star attack: gold
    assert r > 210 and g > 170 and b < 140
    r, g, b = _slot_pixel(im, 1, 0)  # -40 defense: pinkish red
    assert r > 200 and r - g > 45 and r - b > 50
    for col, row in ((0, 1), (1, 1), (0, 5)):  # others grey; unplayed slots pale
        r, g, b = _slot_pixel(im, col, row)
        assert 190 < r < 240 and abs(r - g) < 16 and abs(r - b) < 26


def test_autofill_and_inferred_rows():
    attacks = [B(trophies=40), B(trophies=24, occurred_at="2026-11-07T11:00:00+00:00")]
    defenses = [B(trophies=-21), B(trophies=-31, autofill=True)]
    im = open_png(render(attacks, defenses, cap=7))
    assert im.size == (1000, cards.legend_day_height(7))
    assert _slot_pixel(im, 0, 0)[2] < 140  # an inferred +40 is still a gold row
    with_stars = open_png(render(attacks, [hit(-21), hit(-31)], cap=7))
    assert (
        im.crop((500, 0, 1000, im.height)).tobytes() != with_stars.crop((500, 0, 1000, im.height)).tobytes()
    )


def test_cjk_and_long_names():
    names = ["龍の騎士団の団長", "전설의 용사", "Supercalifragilistic Expialidocious", "⚡🔥", ""]
    im = open_png(
        render(
            [hit(40, n, 3, 100) for n in names], [hit(-30, n) for n in names], player_name="龍の騎士 JoNico"
        )
    )
    assert im.size == (1000, cards.legend_day_height(8))
    fonts = cards._game_fonts(20)
    runs, size = cards._fit_game("Supercalifragilistic Expialidocious", 20, 14, cards._px(150))
    assert size == 14 and runs[-1][0] == "…"
    if not any(cards._has_glyph(f, "龍") for f in fonts):
        pytest.skip("no CJK font on this host")
    runs, _ = cards._fit_game("龍の騎士", 20, 14, cards._px(400))
    assert "".join(s for s, _ in runs) == "龍の騎士" and runs[0][1] not in fonts[:2]


def test_renders_without_the_game_font(monkeypatch, tmp_path):
    with_font = render([hit(40, stars=3)], [hit(-40, stars=3)])
    monkeypatch.setenv(unit_icons.FONT_DIR_ENV, str(tmp_path))  # an empty folder: no Clash font
    assert cards._game_fonts(20)[0].path.endswith("Inter-Variable.ttf")
    im = open_png(render([hit(40, stars=3)], [hit(-40, stars=3)]))
    assert im.size == (1000, cards.legend_day_height(8))
    if (unit_icons.DEFAULT_FONT_DIR / cards.GAME_FONT).is_file():
        assert render([hit(40, stars=3)], [hit(-40, stars=3)]) != with_font


def test_a_broken_font_file_falls_back(monkeypatch, tmp_path, caplog):
    (tmp_path / cards.GAME_FONT).write_bytes(b"not a font")
    monkeypatch.setenv(unit_icons.FONT_DIR_ENV, str(tmp_path))
    with caplog.at_level(logging.WARNING):
        assert open_png(render([hit(40)])).width == 1000
    assert cards._game_fonts(20)[0].path.endswith("Inter-Variable.ttf")


def test_without_a_trophy_icon_a_cup_is_drawn():
    assert render(trophy_icon=None) != render()
    assert open_png(render(trophy_icon=b"garbage")).width == 1000


def test_past_days_and_ranks():
    today = render([hit(40)], today=True)
    past = render([hit(40)], today=False)
    assert today != past  # "Current League Day" vs "League Day · Nov 07"
    assert render([hit(40)], rank=2451) != today
    assert open_png(render(trophies=None)).width == 1000


# ------------------------------------------------------------------ /legends day


async def _insert(db, kind, trophies, at, after, tag=TAG, **battle):
    data = {
        "player_tag": tag,
        "legend_day": DAY,
        "season_id": SEASON,
        "kind": kind,
        "trophies": trophies,
        "trophies_after": after,
        "occurred_at": at,
        **battle,
    }
    await db.execute(
        f"INSERT INTO legend_events ({', '.join(data)}) VALUES ({', '.join('?' * len(data))})",
        list(data.values()),
    )


@pytest.fixture
async def day(db, monkeypatch):
    for tag, name, trophies in ((TAG, "Amy", 5062), (ALT, "Bob", 4900)):
        await db.execute(
            "INSERT INTO players (player_tag, name, town_hall, trophies, league, clan_tag, updated_at) "
            "VALUES (?, ?, 17, ?, 'Legend I', '#C', 'x')",
            (tag, name, trophies),
        )
    await db.execute(
        "INSERT INTO legend_days (player_tag, legend_day, season_id, start_trophies, end_trophies) "
        "VALUES (?, ?, ?, 5000, 5060)",
        (TAG, DAY, SEASON),
    )
    logged = dict(opponent_tag="#OPP", opponent_th=17, battle_type="legend")
    await _insert(
        db,
        "attack",
        40,
        "2026-11-07T06:00:00+00:00",
        5040,
        opponent_name="Zed",
        stars=3,
        destruction=100,
        **logged,
    )
    await _insert(
        db,
        "defense",
        -20,
        "2026-11-07T07:00:00+00:00",
        5020,
        opponent_name="Raider",
        stars=2,
        destruction=88,
        **logged,
    )
    await _insert(db, "attack", 40, "2026-11-07T08:00:00+00:00", 5060)  # inferred
    await _insert(db, "defense", -25, "2026-11-07T09:00:00+00:00", 4875, tag=ALT)
    await db.link_player(TAG, 1)
    await db.link_player(ALT, 1)
    monkeypatch.setattr(cog, "_now", lambda: datetime(2026, 11, 7, 18, 0, tzinfo=UTC))
    return db


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
        client=NS(db=db),
        guild_id=None,
        user=NS(id=1),
        response=FakeResponse(),
        followup=FakeFollowup(),
    )


async def run_day(db, player=TAG):
    interaction = _interaction(db)
    await cog.Legends.legends_day.callback(cog.Legends(NS(db=db)), interaction, player, None, 0)
    assert interaction.response.deferred
    [msg] = interaction.followup.sent
    return msg


@pytest.fixture
def spy(monkeypatch):
    seen = {}

    async def trophy():
        return png()

    real = cards.render_legend_day

    def render_spy(**kwargs):
        seen["render"] = kwargs
        return real(**kwargs)

    monkeypatch.setattr(unit_icons, "fetch_trophy_icon", trophy)
    monkeypatch.setattr(cards, "render_legend_day", render_spy)
    return seen


async def test_day_card_is_attached(day, spy):
    msg = await run_day(day)
    embed, file = msg["embed"], msg["file"]
    assert embed.image.url == "attachment://legend-day.png"
    assert file.filename == "legend-day.png"
    assert open_png(file.fp.read()).size == (1000, cards.legend_day_height(8))
    assert "view" not in msg  # a tag was given: no account switcher

    kw = spy["render"]
    assert (kw["player_name"], kw["day"], kw["today"], kw["cap"]) == ("Amy", DAY, True, 8)
    assert kw["trophies"] == 5062  # today: the player's current trophies
    assert [(b.trophies, b.stars) for b in kw["attacks"]] == [(40, 3), (40, None)]
    assert [(b.opponent, b.trophies) for b in kw["defenses"]] == [("Raider", -20)]
    assert kw["trophy_icon"] == png()

    assert embed.title == f"Amy · Legend day {DAY}"
    assert embed.description == "**5000** → **5060** · Net **+60** · ⚔️ 2/8 (+80) · 🛡️ 1/8 (-20)"
    assert not embed.fields


async def test_switching_accounts_edits_in_the_new_image(day, spy):
    interaction = _interaction(day)
    await cog.Legends.legends_day.callback(cog.Legends(NS(db=day)), interaction, None, None, 0)
    [msg] = interaction.followup.sent
    view = msg["view"]
    assert isinstance(view, cog.AltSelectView) and msg["file"].filename == "legend-day.png"

    switch = _interaction(day)
    view.select._values = [ALT]
    await view._on_select(switch)
    [edit] = switch.response.edits
    [file] = edit["attachments"]
    assert isinstance(file, discord.File) and file.filename == "legend-day.png"
    assert edit["embed"].title == f"Bob · Legend day {DAY}" and edit["view"] is view
    assert spy["render"]["player_name"] == "Bob" and spy["render"]["trophies"] == 4900


async def test_falls_back_to_the_text_embed(day, monkeypatch, caplog):
    async def trophy():
        return None

    def broken(**kwargs):
        raise RuntimeError("no fonts")

    monkeypatch.setattr(unit_icons, "fetch_trophy_icon", trophy)
    monkeypatch.setattr(cards, "render_legend_day", broken)
    with caplog.at_level(logging.ERROR):
        msg = await run_day(day)
    assert "file" not in msg
    embed = msg["embed"]
    assert embed.image.url is None
    expected = cog.E.day_embed(
        name="Amy",
        tag=TAG,
        day=DAY,
        stats=await cog.repo.day_stats(day, TAG, DAY),
        start=5000,
        end=5060,
        color=embed.color,
    )
    assert embed.to_dict() == expected.to_dict()  # the text embed, unchanged
    assert any("legend day card" in r.getMessage() for r in caplog.records)

    switch = _interaction(day)
    view = cog.AltSelectView(
        1, [(TAG, "Amy"), (ALT, "Bob")], lambda t: cog._day_reply(NS(db=day), t, DAY, None)
    )
    view.select._values = [ALT]
    await view._on_select(switch)
    [edit] = switch.response.edits
    assert edit["attachments"] == []  # the old image is removed
    assert edit["embed"].fields


async def test_icon_failures_fall_back_too(day, monkeypatch):
    async def offline():
        raise OSError("offline")

    monkeypatch.setattr(unit_icons, "fetch_trophy_icon", offline)
    msg = await run_day(day)
    assert "file" not in msg and msg["embed"].fields
