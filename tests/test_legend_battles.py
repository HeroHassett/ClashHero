"""Battle-log details (opponent, stars, destruction, army) in legend alerts, cards and summaries."""

from datetime import UTC, datetime
from types import SimpleNamespace as NS

import coc
import discord

from clashhero.cogs import _legend_embeds as E
from clashhero.cogs import legends as cog
from clashhero.events import LegendBattle
from clashhero.repos import legends as repo
from clashhero.services.battlelog import army_link
from clashhero.utils.fmt import player_link
from clashhero.utils.style import FAILURE

DAY = "2026-11-07"
SEASON = "2026-11-30"
ARMY = "u10x0-2x3s1x9-3x2"


def row(kind, trophies, at="2026-11-07T10:00:00+00:00", tag="#A", **battle):
    """A legend_events-shaped dict; pass opponent_*/stars/destruction for a battle-log battle."""
    return {
        "player_tag": tag,
        "kind": kind,
        "trophies": trophies,
        "legend_day": DAY,
        "occurred_at": at,
        **battle,
    }


def logged(kind, trophies, stars, pct, at="2026-11-07T10:00:00+00:00", tag="#A", opp="Opp", th=17):
    return row(
        kind,
        trophies,
        at,
        tag,
        opponent_tag="#OPP",
        opponent_name=opp,
        opponent_th=th,
        stars=stars,
        destruction=pct,
        army_share_code=ARMY,
    )


async def insert(db, r, after=5000, season=SEASON):
    """Insert a row()/logged() dict into legend_events; returns the event id."""
    data = {**r, "season_id": season, "trophies_after": after}
    cols = ", ".join(data)
    marks = ", ".join("?" * len(data))
    return await db.execute(f"INSERT INTO legend_events ({cols}) VALUES ({marks})", list(data.values()))


async def insert_player(db, tag="#A", name="Amy", trophies=5000, clan="#C"):
    await db.execute(
        "INSERT INTO players (player_tag, name, town_hall, trophies, league, clan_tag, updated_at) "
        "VALUES (?, ?, 17, ?, 'Legend I', ?, 'x')",
        (tag, name, trophies, clan),
    )


# ------------------------------------------------------------------ alert embed + buttons


def test_alert_embed_with_battle_log_details():
    stats = repo.aggregate_day([logged("attack", 32, 2, 85)])["#A"]
    e = E.alert_embed(
        name="Amy",
        tag="#A",
        kind="attack",
        trophies=32,
        trophies_after=5032,
        stats=stats,
        occurred_at=datetime(2026, 11, 7, tzinfo=UTC),
        opponent_tag="#OPP",
        opponent_name="Opp_1",
        opponent_th=17,
        stars=2,
        destruction=85,
        battle_type="legend",
    )
    assert e.title == "Amy (#A)" and e.url.endswith("tag=A")
    first, second = e.description.split("\n")
    assert first == "⚔️ **Attack** · ▲ **+32** → 🏆 **5,032**"
    assert second.startswith("★★☆ **85%** vs [Opp\\_1](")  # name markdown-escaped, linked to the profile
    assert second.endswith(" TH17")
    fields = {f.name: f.value for f in e.fields}
    assert fields["Attacks"] == "**1/8** · ▲ **+32**" and fields["Net today"] == "▲ **+32**"
    assert e.footer.text == "Legend I battle"

    d = E.alert_embed(
        name="Amy",
        tag="#A",
        kind="defense",
        trophies=-40,
        trophies_after=4992,
        stats=stats,
        opponent_tag="#OPP",
        opponent_name="Opp",
        opponent_th=16,
        stars=3,
        destruction=100,
        battle_type="ranked",
    )
    assert d.description.startswith("🛡️ **Defense** · ▼ **-40** → 🏆 **4,992**")
    assert "★★★ **100%** by [Opp]" in d.description
    assert d.footer.text == "Ranked battle"


def test_alert_embed_inferred_battle_has_no_battle_details():
    stats = repo.DayStats("#A", [40])
    e = E.alert_embed(
        name="Amy",
        tag="#A",
        kind="attack",
        trophies=40,
        trophies_after=5040,
        stats=stats,
        clan_name="Aurora",
        badge_url="b",
    )
    assert e.description == "⚔️ **Attack** · ▲ **+40** → 🏆 **5,040**"
    assert (e.footer.text, e.footer.icon_url) == ("Aurora", "b")


def _event(**kw):
    base = dict(
        player_tag="#A",
        name="Amy",
        clan_tag="#C",
        kind="attack",
        trophies=40,
        trophies_after=5040,
        occurred_at=datetime(2026, 11, 7, tzinfo=UTC),
        legend_day=DAY,
    )
    return LegendBattle(**{**base, **kw})


def _buttons(view):
    return [(getattr(c, "custom_id", None), getattr(c, "url", None)) for c in view.children]


async def test_alert_view_buttons():
    full = _event(event_id=7, opponent_tag="#OPP", stars=3, destruction=100, army_share_code=ARMY)
    assert _buttons(cog.alert_view(full, 3)) == [
        ("lg:army:7", None),
        (None, army_link(ARMY)),
        ("lg:mute:3", None),
    ]
    assert _buttons(cog.alert_view(full)) == [("lg:army:7", None), (None, army_link(ARMY))]
    inferred = _event()
    assert cog.alert_view(inferred) is None
    assert _buttons(cog.alert_view(inferred, 3)) == [("lg:mute:3", None)]
    no_army = _event(event_id=8, opponent_tag="#OPP", stars=1, destruction=50)
    assert _buttons(cog.alert_view(no_army, 3)) == [("lg:mute:3", None)]


async def test_listener_sends_details_and_buttons(db, monkeypatch):
    await insert_player(db)
    eid = await insert(db, logged("attack", 40, 3, 100), after=5040)
    sid = await repo.add_subscription(
        db, guild_id=1, user_id=10, player_tag="#A", channel_id=55, created_by=10
    )
    sent = []

    async def fake_clan_log(bot, clan_tag, log_type, embed, *, content=None, view=None):
        sent.append(("clan", embed, view))

    async def fake_send(bot, channel_id, *, content=None, embed=None, view=None, **kw):
        sent.append((channel_id, embed, view))

    monkeypatch.setattr(cog, "send_clan_log", fake_clan_log)
    monkeypatch.setattr(cog, "send_to_channel", fake_send)
    legends = cog.Legends(NS(db=db))
    await legends.on_clash_legend_battle(
        _event(
            event_id=eid,
            battle_type="legend",
            opponent_tag="#OPP",
            opponent_name="Opp",
            opponent_th=17,
            stars=3,
            destruction=100,
            army_share_code=ARMY,
        )
    )
    (where1, clan_embed, clan_view), (where2, sub_embed, sub_view) = sent
    assert where1 == "clan" and where2 == 55
    assert "★★★" in clan_embed.description and "Opp" in sub_embed.description
    assert [b[0] for b in _buttons(clan_view)] == [f"lg:army:{eid}", None]
    assert [b[0] for b in _buttons(sub_view)] == [f"lg:army:{eid}", None, f"lg:mute:{sid}"]


# ------------------------------------------------------------------ View army lookup


async def test_battle_lookup(db):
    await insert_player(db)
    eid = await insert(db, logged("attack", 40, 3, 100))
    plain = await insert(db, row("defense", -20))
    battle = await repo.get_battle(db, eid)
    assert battle["player_name"] == "Amy" and battle["opponent_name"] == "Opp"
    assert battle["army_share_code"] == ARMY
    plain_row = await repo.get_battle(db, plain)
    assert plain_row["stars"] is None and plain_row["army_share_code"] is None
    assert await repo.get_battle(db, 999) is None


class FakeResponse:
    def __init__(self):
        self.sent = []

    async def send_message(self, content=None, *, embed=None, view=None, ephemeral=False):
        self.sent.append(NS(content=content, embed=embed, view=view, ephemeral=ephemeral))


def _interaction(db):
    client = coc.Client()
    client._load_static()
    return NS(client=NS(db=db, coc=client), guild_id=None, response=FakeResponse())


async def test_view_army_button(db):
    await insert_player(db)
    eid = await insert(db, logged("defense", -30, 2, 80, opp="Raider"))
    inter = _interaction(db)
    await cog.ViewArmyButton(eid).callback(inter)
    (msg,) = inter.response.sent
    assert msg.ephemeral
    assert msg.embed.title == "Raider's army"  # on a defense the army is the attacker's
    assert msg.embed.url == player_link("#OPP")
    assert msg.embed.description.split("\n") == [
        "🛡️ **Defense** · ▼ **-30**",  # from the tracked player's side, as in the alerts
        f"★★☆ **80%** by [Raider]({player_link('#OPP')}) TH17",
    ]
    assert msg.embed.footer.text == "Amy (#A)"
    names = [f.name for f in msg.embed.fields]
    assert names == ["Troops (20 space)", "Spells (7 space)"]
    assert "Barbarian" in msg.embed.fields[0].value
    (link,) = msg.view.children
    assert link.url == army_link(ARMY) and link.style is discord.ButtonStyle.link


async def test_view_army_button_without_army(db):
    plain = await insert(db, row("attack", 40))
    for event_id in (plain, 999):
        inter = _interaction(db)
        await cog.ViewArmyButton(event_id).callback(inter)
        (msg,) = inter.response.sent
        assert msg.ephemeral and msg.embed.color == FAILURE and "No army" in msg.embed.description


async def test_view_army_button_bad_code(db):
    await insert_player(db)
    bad = logged("attack", 40, 3, 100)
    bad["army_share_code"] = "zzz"
    eid = await insert(db, bad)
    inter = _interaction(db)
    await cog.ViewArmyButton(eid).callback(inter)
    (msg,) = inter.response.sent
    assert msg.embed.color == FAILURE and "Couldn't read" in msg.embed.description


def test_view_army_template_round_trip():
    assert cog.ViewArmyButton.__discord_ui_compiled_template__.fullmatch("lg:army:42")
    assert cog.ViewArmyButton(42).custom_id == "lg:army:42"


# ------------------------------------------------------------------ day card, boards, season


def test_day_embed_mixed_rows():
    rows = [
        logged("attack", 40, 3, 100, at="2026-11-07T08:00:00+00:00", opp="Bob*"),
        row("attack", 30, at="2026-11-07T09:00:00+00:00"),
        row("defense", -20, at="2026-11-07T10:00:00+00:00"),
    ]
    stats = repo.aggregate_day(rows)["#A"]
    e = E.day_embed(name="Amy", tag="#A", day=DAY, stats=stats, start=5000, end=5050)
    assert e.title == "Amy (#A)" and e.url == player_link("#A")
    assert e.description.split("\n") == ["**Legend day · Nov 7**", "🏆 **5,000** → **5,050** · ▲ **+50**"]
    fields = {f.name: f.value for f in e.fields}
    assert fields["⚔️ Attacks 2/8 · ▲ +70"].split("\n") == ["` +40` ★★★ 100% vs Bob\\* TH17", "` +30`"]
    assert fields["🛡️ Defenses 1/8 · ▼ -20"] == "-20"  # no battle-log defenses: compact trophy list
    assert fields["Triples"] == "**1/2**" and fields["Attacks left"] == "**6**"
    assert e.footer.text == "some battles inferred from trophy changes"


def test_day_embed_inferred_only_is_unchanged():
    stats = repo.aggregate_day([row("attack", 40), row("attack", 30), row("defense", -20)])["#A"]
    e = E.day_embed(name="Amy", tag="#A", day=DAY, stats=stats, start=5000, end=5050)
    fields = {f.name: f.value for f in e.fields}
    assert fields["⚔️ Attacks 2/8 · ▲ +70"] == "+40 +30"
    assert "Triples" not in fields
    assert e.footer.text == "battles are inferred from trophy changes"


def test_day_embed_without_a_start_and_zero_totals():
    stats = repo.aggregate_day([row("defense", 0)])["#A"]
    e = E.day_embed(name="#A", tag="#A", day=DAY, stats=stats, start=None, end=None)
    assert e.title == "#A"  # no stored name: just the tag
    assert e.description.split("\n") == ["**Legend day · Nov 7**", "Net **0**"]
    assert [f.name for f in e.fields][:2] == ["⚔️ Attacks 0/8 · +0", "🛡️ Defenses 1/8 · +0"]


def test_day_embed_battle_log_only_and_long_lists():
    rows = [logged("attack", 10, 3, 100, opp=f"Opponent{i:02d}") for i in range(30)]
    stats = repo.aggregate_day(rows)["#A"]
    stats.cap = None
    e = E.day_embed(name="Amy", tag="#A", day=DAY, stats=stats, start=5000, end=5300)
    attack_fields = [f for f in e.fields if "Attacks" in f.name and "left" not in f.name]
    assert len(attack_fields) > 1 and attack_fields[1].name == "Attacks (cont.)"
    assert all(len(f.value) <= 1024 for f in e.fields) and len(e) <= 6000
    assert sum(f.value.count("\n") + 1 for f in attack_fields) == 30
    assert e.footer.text == "from the battle log"


def test_day_embed_with_game_icons_stays_within_discord_limits(monkeypatch):
    """With the app emojis loaded a battle line is ~150 characters; a busy day below Legend I keeps
    the newest battles that fit and says how many earlier ones are left out."""
    from clashhero.services.emojis import EMOJIS

    names = ["ui_star_new", "ui_star_empty", "ui_star_old", "th17", "res_trophy", "ui_sword", "ui_shield"]
    names += ["ui_arrow_up", "ui_arrow_down"]
    monkeypatch.setattr(EMOJIS, "_by_name", {n: f"<:{n}:{1234567890123456789}>" for n in names})
    rows = [logged("attack", 10, 2, 90, opp=f"Opponent{i:02d}") for i in range(40)]
    rows += [logged("defense", -10, 2, 90, opp=f"Raider{i:02d}") for i in range(40)]
    stats = repo.aggregate_day(rows)["#A"]
    stats.cap = None
    e = E.day_embed(name="Amy", tag="#A", day=DAY, stats=stats, start=5000, end=5000)
    assert len(e) <= 6000 and all(len(f.value) <= 1024 for f in e.fields)
    attacks = [f for f in e.fields if "Attacks" in f.name]
    assert len(attacks) == 2 and attacks[0].name.startswith("<:ui_sword:")
    lines = "\n".join(f.value for f in attacks).split("\n")
    assert lines[0].startswith("… ") and lines[0].endswith(" earlier")
    assert int(lines[0].split()[1]) + len(lines) - 1 == 40
    assert lines[-1].endswith("Opponent39 <:th17:1234567890123456789>")  # the newest is kept
    assert "<:ui_star_new:" in lines[-1]

    side = E.armies_embed(
        name="Amy", tag="#A", day=DAY, kind="defense", hits=stats.hits("defense"), cap=None, trophies=5000
    )
    assert len(side) <= 6000 and len(side.fields) <= 4


def test_hits_built_from_bare_lists_fall_back_to_trophies():
    stats = repo.DayStats("#A", [40, 25], [-10])
    assert [h.trophies for h in stats.hits("attack")] == [40, 25]
    assert not stats.detailed and stats.triples == 1


def test_season_triple_rate_prefers_stars():
    rows = [
        logged("attack", 30, 3, 100),  # triple despite < 40 trophies
        logged("attack", 40, 2, 99),  # not a triple despite 40 trophies
        row("attack", 40),  # no stars: max-trophy proxy -> triple
        row("attack", 20),  # no stars: not a triple
        logged("defense", -40, 3, 100),
        logged("defense", -10, 1, 60),
    ]
    s = repo.build_season([], rows)
    assert s.triple_rate == 0.5 and s.estimated_triples == 2
    assert s.avg_attack_stars == 2.5 and s.avg_defense_stars == 2.0
    e = E.season_embed(name="Amy", tag="#A", season_id=SEASON, summary=s)
    fields = {f.name: f.value for f in e.fields}
    assert fields["3★ rate"] == "50% (2 by trophies)"
    assert fields["Avg stars"] == "atk 2.50★ · def 2.00★"
    assert "Max-trophy hits" not in fields

    only_logged = repo.build_season([], [logged("attack", 30, 3, 100), logged("attack", 40, 1, 70)])
    fields = {
        f.name: f.value
        for f in E.season_embed(name="A", tag="#A", season_id=SEASON, summary=only_logged).fields
    }
    assert fields["3★ rate"] == "50%"

    inferred = repo.build_season([], [row("attack", 40), row("attack", 30)])
    fields = {
        f.name: f.value for f in E.season_embed(name="A", tag="#A", season_id=SEASON, summary=inferred).fields
    }
    assert fields["Max-trophy hits"] == "50%" and "Avg stars" not in fields and "3★ rate" not in fields


async def test_season_summary_from_db_mixes_rows(db):
    await insert(db, logged("attack", 30, 3, 100))
    await insert(db, row("attack", 40, at="2026-11-07T11:00:00+00:00"))
    await insert(db, logged("attack", 40, 2, 90, at="2026-11-07T12:00:00+00:00"))
    s = await repo.season_summary(db, "#A", SEASON)
    assert [h.stars for h in s.attack_hits] == [3, None, 2]
    assert round(s.triple_rate, 3) == round(2 / 3, 3)


async def test_board_and_clan_views_with_battles(db):
    await insert_player(db, "#A", "Amy", 5070)
    await insert_player(db, "#B", "Bob", 5100)
    await insert(db, logged("attack", 40, 3, 100, at="2026-11-07T08:00:00+00:00", opp="Zed"))
    await insert(db, logged("defense", -10, 1, 55, at="2026-11-07T09:00:00+00:00", opp="Raider"))
    await insert(db, row("attack", 25, at="2026-11-07T10:00:00+00:00", tag="#B"))
    for tag, start, end in (("#A", 5040, 5070), ("#B", 5075, 5100)):
        await db.execute(
            "INSERT INTO legend_days (player_tag, legend_day, season_id, start_trophies, end_trophies) "
            "VALUES (?, ?, ?, ?, ?)",
            (tag, DAY, SEASON, start, end),
        )
    entries = await repo.board_entries(db, ["#A", "#B"], DAY)
    lines = E.board_lines(entries)
    assert [r[-1] for r in lines] == ["-", 1]  # Bob has no battle-log data, Amy tripled once

    e = E.board_embed(title="t", entries=entries, latest=8)
    assert "3★" in e.description.split("\n")[1]
    latest = next(f.value for f in e.fields if f.name == "Latest battles").split("\n")
    assert len(latest) == 3
    assert "⚔️ **Bob**" in latest[0] and latest[0].endswith("` +25`")  # newest first, trophies only
    assert "🛡️ **Amy**" in latest[1] and "★☆☆ 55% by Raider TH17" in latest[1]
    assert "★★★ 100% vs Zed TH17" in latest[2]
    assert not [f for f in E.board_embed(title="t", entries=entries).fields if f.name == "Latest battles"]

    rows = await repo.clan_day_rows(db, "#C", DAY)
    assert {r["player_tag"]: r["triples"] for r in rows} == {"#A": 1, "#B": None}
    clan = E.clan_day_embed(clan_name="Clan", clan_tag="#C", day=DAY, rows=rows)
    assert "3★" in clan.description


async def test_board_without_battle_log_keeps_columns(db):
    await insert_player(db, "#A", "Amy", 5040)
    await insert(db, row("attack", 40))
    entries = await repo.board_entries(db, ["#A"], DAY)
    assert len(E.board_lines(entries)[0]) == 7
    e = E.board_embed(title="t", entries=entries)
    assert "3★" not in e.description
