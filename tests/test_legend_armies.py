"""/legends armies: the image card of a legend day's armies, and its text fallback."""

import io
import logging
from datetime import UTC, datetime
from types import SimpleNamespace as NS

import discord
import pytest
from PIL import Image

from clashhero.cogs import _legend_embeds as E
from clashhero.cogs import legends as cog
from clashhero.repos import legends as repo
from clashhero.services import cards
from clashhero.services import icons as unit_icons

DAY = "2026-11-07"
TAG = "#2PQ8VLYC0"
SEASON = "2026-11-30"
DEFENSE = (
    "h0p10e32_14-1p16e16_48-2p7e4_19-7p4e57_53i1x64-1x147d1x53-1x98"
    "u2x1-2x58-5x7-3x28-1x23-4x5-5x80-1x97-2x12-1x135-1x82-1x6s1x10-3x35-1x120-2x2-1x5-1x9"
)
ATTACK = (
    "h1p9e17_48-2m1p16e34_4-6p17e49_43-7p4e52_60i1x188-1x63-1x76d1x2-1x70"
    "u6x57-1x63-5x65-8x8-2x10s3x109-2x98-1x9-2x120-1x5"
)


async def insert(db, kind, trophies, at, after, **battle):
    data = {
        "player_tag": TAG,
        "legend_day": DAY,
        "season_id": SEASON,
        "kind": kind,
        "trophies": trophies,
        "trophies_after": after,
        "occurred_at": at,
        **battle,
    }
    cols = ", ".join(data)
    await db.execute(
        f"INSERT INTO legend_events ({cols}) VALUES ({', '.join('?' * len(data))})", list(data.values())
    )


@pytest.fixture
async def day(db, monkeypatch):
    """Amy's legend day: two logged defenses with armies, one inferred, one auto-filled; one
    logged attack."""
    await db.execute(
        "INSERT INTO players (player_tag, name, town_hall, trophies, league, clan_tag, updated_at) "
        "VALUES ('#2PQ8VLYC0', 'Amy', 17, 5400, 'Legend I', '#C', 'x')"
    )
    await db.execute(
        "INSERT INTO legend_days (player_tag, legend_day, season_id, start_trophies, end_trophies) "
        "VALUES ('#2PQ8VLYC0', ?, ?, 5500, 5421)",
        (DAY, SEASON),
    )
    logged = dict(opponent_tag="#OPP", opponent_th=17, battle_type="legend")
    defense_1 = dict(opponent_name="ドラゴン使い", stars=3, destruction=100, army_share_code=DEFENSE)
    attack = dict(opponent_name="Zed", stars=3, destruction=100, army_share_code=ATTACK)
    defense_2 = dict(opponent_name="Raider", stars=1, destruction=63, army_share_code=ATTACK)
    await insert(db, "defense", -40, "2026-11-07T06:00:00+00:00", 5460, **defense_1, **logged)
    await insert(db, "attack", 40, "2026-11-07T07:00:00+00:00", 5500, **attack, **logged)
    await insert(db, "defense", -16, "2026-11-07T09:00:00+00:00", 5484, **defense_2, **logged)
    await insert(db, "defense", -24, "2026-11-07T12:00:00+00:00", 5460)  # inferred
    await insert(db, "defense", -39, "2026-11-08T04:59:59+00:00", 5421, battle_type="autofill")
    monkeypatch.setattr(cog, "_now", lambda: datetime(2026, 11, 7, 18, 0, tzinfo=UTC))
    return db


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)


class FakeResponse:
    def __init__(self):
        self.deferred = False

    async def defer(self, **kwargs):
        self.deferred = True


async def run(db, side=None, player=TAG):
    bot = NS(db=db)
    interaction = NS(
        client=bot,
        guild_id=None,
        user=NS(id=1),
        response=FakeResponse(),
        followup=FakeFollowup(),
    )
    choice = {"defense": cog.SIDES[0], "attack": cog.SIDES[1]}.get(side)
    await cog.Legends.legends_armies.callback(cog.Legends(bot), interaction, player, None, 0, choice)
    assert interaction.response.deferred
    [msg] = interaction.followup.sent
    return msg


@pytest.fixture
def spy(monkeypatch):
    seen = {}

    async def fake_icons(names):
        seen["names"] = set(names)
        return {}

    real = cards.render_legend_armies

    def render(**kwargs):
        seen["render"] = kwargs
        return real(**kwargs)

    monkeypatch.setattr(unit_icons, "fetch_icons", fake_icons)
    monkeypatch.setattr(cards, "render_legend_armies", render)
    return seen


async def test_defenses_card_is_attached(day, spy):
    msg = await run(day)
    embed, file = msg["embed"], msg["file"]
    assert embed.image.url == "attachment://armies.png"
    assert file.filename == "armies.png"
    im = Image.open(io.BytesIO(file.fp.read()))
    assert im.format == "PNG" and im.width == 1000

    kw = spy["render"]
    assert (kw["kind"], kw["player_name"], kw["player_tag"], kw["day"]) == ("defense", "Amy", TAG, DAY)
    assert (kw["trophies"], kw["total"], kw["count"]) == (5421, -119, "4/8")
    battles = kw["battles"]
    assert im.height == cards.armies_card_height(battles)
    assert [(b.opponent, b.trophies, b.autofill) for b in battles] == [
        ("ドラゴン使い", -40, False),
        ("Raider", -16, False),
        (None, -24, False),
        (None, -39, True),
    ]
    assert [bool(b.army) for b in battles] == [True, True, False, False]
    assert battles[0].army.heroes[3].hero.name == "Dragon Duke"
    assert {"Barbarian King", "Super Yeti", "Revive Spell", "Dragon Duke", "Sky Wagon"} <= spy["names"]

    assert embed.title == f"Amy ({TAG})" and embed.url.endswith(TAG[1:])
    assert embed.description.split("\n") == [
        "🛡️ **Defense armies** · Legend day Nov 7",
        "🏆 **5,421** · **4/8** defenses · ▼ **-119**",  # the day's total with a red down arrow
    ]
    assert not embed.fields  # the battles are on the card
    assert embed.footer.text == "some battles inferred from trophy changes"


async def test_attacks_side(day, spy):
    msg = await run(day, side="attack")
    kw = spy["render"]
    assert (kw["kind"], kw["total"], kw["count"]) == ("attack", 40, "1/8")
    assert [b.opponent for b in kw["battles"]] == ["Zed"]
    assert spy["names"] == kw["battles"][0].army.names()
    assert msg["embed"].title == f"Amy ({TAG})"
    assert msg["embed"].description.startswith("⚔️ **Attack armies** · Legend day Nov 7")
    assert msg["embed"].description.endswith("**1/8** attacks · ▲ **+40**")
    assert msg["file"].filename == "armies.png"


async def test_falls_back_to_text_when_the_card_fails(day, monkeypatch, caplog):
    async def no_icons(names):
        return {}

    def broken(**kwargs):
        raise RuntimeError("no fonts")

    monkeypatch.setattr(unit_icons, "fetch_icons", no_icons)
    monkeypatch.setattr(cards, "render_legend_armies", broken)
    with caplog.at_level(logging.ERROR):
        msg = await run(day)

    assert "file" not in msg
    embed = msg["embed"]
    assert embed.image.url is None
    [field] = embed.fields
    assert field.name == "Defenses"
    lines = field.value.split("\n")
    assert len(lines) == 4
    assert "★★★ 100% by ドラゴン使い TH17" in lines[0]
    assert "auto-filled" in lines[3]
    assert any("armies card" in r.getMessage() for r in caplog.records)


async def test_icon_failures_also_fall_back_to_text(day, monkeypatch):
    async def offline(names):
        raise OSError("offline")

    monkeypatch.setattr(unit_icons, "fetch_icons", offline)
    msg = await run(day)
    assert "file" not in msg and msg["embed"].fields


async def test_a_day_without_battles(db, monkeypatch, spy):
    monkeypatch.setattr(cog, "_now", lambda: datetime(2026, 11, 7, 18, 0, tzinfo=UTC))
    msg = await run(db, player="#2YYLQ")
    kw = spy["render"]
    assert (kw["battles"], kw["trophies"], kw["total"]) == ([], None, 0)
    assert msg["embed"].title == "#2YYLQ"  # no stored name: just the tag
    assert msg["file"].filename == "armies.png"


def test_side_choices():
    assert [(c.name, c.value) for c in cog.SIDES] == [("Defenses", "defense"), ("Attacks", "attack")]
    params = {p.name: p for p in cog.Legends.legends_armies.parameters}
    assert list(params) == ["player", "user", "day_offset", "side"]
    assert (
        params["day_offset"].max_value == 60
        and params["day_offset"].type is discord.AppCommandOptionType.integer
    )


def test_army_battles_and_text_embed():
    hits = [
        repo.Hit("defense", -30, "2026-11-07T06:00:00+00:00", None, "#OPP", None, 16, 2, 80, DEFENSE),
        repo.Hit("defense", -20),
    ]
    battles = cog.army_battles(hits)
    assert battles[0].opponent == "#OPP"  # no name recorded: the tag
    assert battles[0].army.troops[0].name == "Archer" and battles[1].army is None
    e = E.armies_embed(name="Amy", tag=TAG, day=DAY, kind="defense", hits=[], cap=8, trophies=None)
    assert e.description == "🛡️ **Defense armies** · Legend day Nov 7\n**0/8** defenses · **0**"
    assert e.fields[0].value == "None recorded for this day." and e.footer.text is None
