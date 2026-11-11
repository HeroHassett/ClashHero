"""/legends logs "today" text view, and the persistent view buttons shared with /legends armies."""

import re
from datetime import UTC, datetime
from types import SimpleNamespace as NS

import discord
import pytest

from clashhero.cogs import _legend_embeds as E
from clashhero.cogs import legends as cog
from clashhero.repos.legends import Hit
from clashhero.services import cards
from clashhero.services import icons as unit_icons
from clashhero.services.emojis import EMOJIS
from clashhero.utils.fmt import display_width
from clashhero.utils.style import FAILURE

TAG = "#2PQ8VLYC0"
DAY = "2026-11-07"
SEASON = "2026-11-02"
ARMY = "h0p10e32_14u2x1-5x7s1x10"


def logged(kind, trophies, stars, pct, name):
    return Hit(kind, trophies, None, None, "#OPP", name, 17, stars, pct)


ATTACKS = [
    logged("attack", 28, 2, 86, "메롱하다"),
    logged("attack", 40, 3, 100, "GT EDUARDO ^_^"),
    Hit("attack", 29),  # inferred from a trophy change
    logged("attack", 40, 3, 100, "江上✨叶落 with a name far too long to fit"),
]
DEFENSES = [logged("defense", -40, 3, 100, "lost_messiboy"), Hit("defense", -25, autofill=True)]


def today_embed(**kw):
    args = dict(
        author="Legend I · Today",
        name="STUTS",
        tag=TAG,
        town_hall=18,
        trophies=5252,
        attacks=ATTACKS,
        defenses=DEFENSES,
        cap=8,
        start=4945,
        end=5021,
        footer="Nov 07",
        now=datetime(2026, 11, 7, 18, tzinfo=UTC),
    )
    return E.legends_today_embed(**{**args, **kw})


def code_blocks(text: str) -> list[list[str]]:
    return [b.strip("\n").split("\n") for b in re.findall(r"```\n(.*?)```", text, re.S)]


# ------------------------------------------------------------------ the text


def test_today_embed_layout():
    e = today_embed()
    assert e.author.name == "LEGEND I · TODAY"
    assert e.title == f"STUTS {TAG}" and e.url.endswith("2PQ8VLYC0")
    first = e.description.split("\n")[0]
    assert first == "Town Hall 18 · **5,252** trophies"  # no emojis uploaded: plain text
    summary, attacks, defenses = code_blocks(e.description)
    assert summary == [
        "ATTACK     +137   4/8",
        "DEFENSE     -65   2/8",
        "NET         +72",
        "TROPHIES   4945 → 5021",
    ]
    assert "⚔️ **Attacks 4/8 (+137)**" in e.description and "🛡️ **Defenses 2/8 (-65)**" in e.description
    assert attacks[2] == "+29  ···   —    inferred from trophies"
    assert defenses[1] == "-25  ···   —    auto-filled (no defense received)"
    assert e.footer.text == "Nov 07 · Net +72 · some battles inferred from trophy changes"
    assert e.timestamp == datetime(2026, 11, 7, 18, tzinfo=UTC)


def test_battle_lines_stay_aligned_with_cjk_names():
    lines = E.battle_lines(ATTACKS)
    assert lines[0] == "+28  ★★☆   86%  메롱하다"
    assert lines[1] == "+40  ★★★  100%  GT EDUARDO ^_^"
    starts = {line.index(h.opponent_name[:3]) for line, h in zip(lines, ATTACKS, strict=True) if h.detailed}
    assert starts == {16}  # every name starts in the same column
    long = lines[3]
    assert long.endswith("…") and display_width(long[16:]) <= E.NAME_COLUMNS


def test_counts_with_and_without_a_cap():
    no_cap = today_embed(cap=None, start=None, author="Electro League 33 · Today")
    summary = code_blocks(no_cap.description)[0]
    assert summary[0].endswith("+137   4") and len(summary) == 3  # no trophies line without a start
    assert "Attacks 4 (+137)" in no_cap.description


def test_empty_day():
    e = today_embed(attacks=[], defenses=[], trophies=None, town_hall=None)
    blocks = code_blocks(e.description)
    assert blocks[1] == ["No attacks yet"] and blocks[2] == ["No defenses yet"]
    assert e.footer.text == "Nov 07 · Net +0"


def test_long_weeks_keep_the_newest_battles():
    many = [logged("attack", i, 2, 80, f"Player {i:02d}") for i in range(1, 61)]
    e = today_embed(attacks=many, defenses=[], cap=30, limit=1500)
    assert len(e.description) <= 1500
    attacks = code_blocks(e.description)[1]
    assert attacks[0].startswith("… ") and attacks[0].endswith(" earlier")
    dropped = int(attacks[0].split()[1])
    assert len(attacks) - 1 + dropped == 60
    assert attacks[-1].endswith("Player 60")  # the newest is kept


def test_game_symbols_when_uploaded(monkeypatch):
    monkeypatch.setattr(
        EMOJIS,
        "_by_name",
        {
            "ui_sword": "<:ui_sword:1>",
            "ui_shield": "<:ui_shield:2>",
            "res_trophy": "<:res_trophy:3>",
            "th18": "<:th18:4>",
        },
    )
    e = today_embed()
    assert e.description.startswith("<:th18:4> Town Hall 18 · <:res_trophy:3> **5,252** trophies")
    assert "<:ui_sword:1> **Attacks 4/8 (+137)**" in e.description
    assert "<:ui_shield:2> **Defenses 2/8 (-65)**" in e.description


# ------------------------------------------------------------------ the buttons


def test_custom_id_round_trip():
    for view in cog.LEGEND_VIEWS:
        cid = cog.legend_view_id(view, TAG, DAY)
        assert len(cid) < 100
        assert cog.parse_legend_view_id(cid) == (view, TAG, DAY, False)
        assert cog.LegendViewButton.__discord_ui_compiled_template__.fullmatch(cid)
    assert cog.legend_view_id("season", "#2pq8vlyc0", DAY) == "lg:v:season:2PQ8VLYC0:2026-11-07"
    assert cog.parse_legend_view_id("lg:v:week:2PQ8VLYC0:2026-11-07") is None
    assert cog.parse_legend_view_id("pl:units:2PQ8VLYC0") is None
    with pytest.raises(ValueError):
        cog.legend_view_id("week", TAG, DAY)


def _buttons(view: discord.ui.View) -> list[discord.ui.Button]:
    return [child.item for child in view.children]


@pytest.mark.parametrize("current", cog.LEGEND_VIEWS)
def test_the_current_view_is_highlighted(current):
    buttons = _buttons(cog.legend_view_row(current, TAG, DAY))
    assert [b.label for b in buttons] == [
        "Refresh",
        "View Historical Data",
        "Defense Armies",
        "Attack Armies",
    ]
    for b, view in zip(buttons, cog.LEGEND_VIEWS, strict=True):
        is_current = view == current
        assert (b.style is discord.ButtonStyle.primary) == is_current
        assert b.disabled == (is_current and view != "day")  # Refresh always works
        assert b.custom_id == cog.legend_view_id(view, TAG, DAY)


def test_button_emojis(monkeypatch):
    plain = _buttons(cog.legend_view_row("day", TAG, DAY))
    assert [str(b.emoji) for b in plain] == ["🔄", "📈", "🛡️", "⚔️"]
    trophy, shield = 1234567890123456789, 1234567890123456780
    monkeypatch.setattr(
        EMOJIS,
        "_by_name",
        {"res_trophy": f"<:res_trophy:{trophy}>", "ui_shield": f"<:ui_shield:{shield}>", "ui_sword": "bad"},
    )
    game = _buttons(cog.legend_view_row("day", TAG, DAY))
    assert (game[0].emoji.name, game[0].emoji.id) == ("res_trophy", trophy)
    assert (game[2].emoji.name, game[2].emoji.id) == ("ui_shield", shield)
    assert str(game[3].emoji) == "⚔️"  # a malformed registry entry falls back


async def _insert(db, kind, trophies, stars=None, army=None, day=DAY):
    await db.execute(
        "INSERT INTO legend_events (player_tag, legend_day, season_id, kind, trophies, trophies_after, "
        "occurred_at, opponent_tag, opponent_name, opponent_th, stars, destruction, army_share_code) "
        "VALUES (?, ?, ?, ?, ?, 0, ?, '#OPP', 'Raider', 17, ?, ?, ?)",
        (TAG, day, SEASON, kind, trophies, f"{day}T10:00:00+00:00", stars, 90 if stars else None, army),
    )


@pytest.fixture
async def day(db, monkeypatch):
    await db.execute(
        "INSERT INTO players (player_tag, name, town_hall, trophies, league, clan_tag, updated_at) "
        "VALUES (?, 'Amy', 18, 5252, 'Legend I', '#C', 'x')",
        (TAG,),
    )
    await db.execute(
        "INSERT INTO legend_days (player_tag, legend_day, season_id, start_trophies, end_trophies) "
        "VALUES (?, ?, ?, 5200, 5240)",
        (TAG, DAY, SEASON),
    )
    await _insert(db, "attack", 40, 3, ARMY)
    await _insert(db, "defense", -20, 2, ARMY)
    await _insert(db, "attack", 20)
    monkeypatch.setattr(cog, "_now", lambda: datetime(2026, 11, 7, 18, tzinfo=UTC))

    async def no_icons(names):
        return {}

    async def no_trophy():
        return None

    monkeypatch.setattr(unit_icons, "fetch_icons", no_icons)
    monkeypatch.setattr(unit_icons, "fetch_trophy_icon", no_trophy)
    return db


class FakeResponse:
    def __init__(self):
        self.deferred = False

    async def defer(self, **kwargs):
        self.deferred = True

    def is_done(self):
        return self.deferred


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)


class FakeInteraction:
    def __init__(self, db):
        self.client = NS(db=db)
        self.guild_id = None
        self.user = NS(id=1)
        self.command = None
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.edits = []

    async def edit_original_response(self, **kwargs):
        self.edits.append(kwargs)


async def press(db, view, day=DAY):
    interaction = FakeInteraction(db)
    await cog.LegendViewButton(view, TAG, day).callback(interaction)
    assert interaction.response.deferred
    [edit] = interaction.edits
    return edit


def _current(edit) -> list[str]:
    return [b.label for b in _buttons(edit["view"]) if b.style is discord.ButtonStyle.primary]


async def test_refresh_shows_the_day(day):
    edit = await press(day, "day")
    e = edit["embed"]
    assert e.author.name == "LEGEND I · TODAY" and e.title == f"Amy {TAG}"
    assert e.description.startswith("Town Hall 18 · **5,252** trophies")
    assert "Attacks 2/8 (+60)" in e.description and "TROPHIES   5200 → 5240" in e.description
    [file] = edit["attachments"]
    assert file.filename == "legend-season.png" and e.image.url == "attachment://legend-season.png"
    assert _current(edit) == ["Refresh"]


async def test_historical_data(day):
    edit = await press(day, "season")
    e = edit["embed"]
    assert e.title == f"Amy ({TAG})" and "Legend Season Logs (2 Nov – 30 Nov 2026)" in e.description
    assert "DAY" in e.description and "+60²" in e.description  # the day-by-day table
    assert [f.filename for f in edit["attachments"]] == ["legend-season.png"]
    assert _current(edit) == ["View Historical Data"]


@pytest.mark.parametrize(("view", "title"), [("def", "Amy · Defense armies"), ("att", "Amy · Attack armies")])
async def test_armies(day, view, title):
    edit = await press(day, view)
    assert edit["embed"].title == title and edit["embed"].image.url == "attachment://armies.png"
    assert [f.filename for f in edit["attachments"]] == ["armies.png"]
    assert _current(edit) == [{"def": "Defense Armies", "att": "Attack Armies"}[view]]


async def test_a_past_day(day):
    edit = await press(day, "day", day="2026-11-05")
    assert edit["embed"].author.name == "LEGEND I · NOV 05"
    assert edit["embed"].footer.text.startswith("Nov 05 · Net +0")


async def test_weekly_tiers_list_the_week(day):
    await day.execute("UPDATE players SET league = 'Legend II' WHERE player_tag = ?", (TAG,))
    await _insert(day, "attack", 31, 2, day="2026-11-03")
    edit = await press(day, "day")
    e = edit["embed"]
    assert e.author.name == "LEGEND II · WEEK NOV 2 – NOV 9"
    assert "Attacks 3/30 (+91)" in e.description and e.footer.text.startswith("Nov 2 – Nov 9 · Net")


async def test_failures_answer_with_an_error(day, monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("database is gone")

    monkeypatch.setattr(cog, "render_legend_view", broken)
    interaction = FakeInteraction(day)
    await cog.LegendViewButton("season", TAG, DAY).callback(interaction)
    assert interaction.edits == []
    [msg] = interaction.followup.sent
    assert msg["ephemeral"] and msg["embed"].color == FAILURE


async def test_text_alone_when_the_season_card_fails(day, monkeypatch):
    def broken(card):
        raise RuntimeError("no fonts")

    monkeypatch.setattr(cards, "render_legend_season", broken)
    edit = await press(day, "day")
    assert edit["attachments"] == [] and edit["embed"].image.url is None
    assert "ATTACK" in edit["embed"].description


async def test_armies_command_gets_the_row(day):
    interaction = FakeInteraction(day)
    attack = cog.SIDES[1]
    await cog.Legends.legends_armies.callback(cog.Legends(NS(db=day)), interaction, TAG, None, 0, attack)
    [msg] = interaction.followup.sent
    assert msg["file"].filename == "armies.png"
    assert [b.label for b in _buttons(msg["view"]) if b.disabled] == ["Attack Armies"]


async def test_day_command_has_no_row(day):
    interaction = FakeInteraction(day)
    await cog.Legends.legends_day.callback(cog.Legends(NS(db=day)), interaction, TAG, None, 0)
    [msg] = interaction.followup.sent
    assert set(msg) == {"file"} and msg["file"].filename == "legend-day.png"


async def test_the_buttons_are_registered():
    registered = []
    bot = NS(
        add_dynamic_items=lambda *items: registered.extend(items),
        collector=NS(register_player_source=lambda source: None),
    )
    legends = cog.Legends(bot)
    legends.board_loop = NS(start=lambda: None)  # no background loops in a test
    legends.daily_loop = NS(start=lambda: None)
    await legends.cog_load()
    assert cog.LegendViewButton in registered
