"""Collector tests with fake coc.py objects (no network)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from clashhero.services.collector import Collector

NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)


class FakeBot:
    def __init__(self, db, coc):
        self.db = db
        self.coc = coc
        self.config = NS(
            clan_poll_seconds=60, player_poll_seconds=60, war_poll_seconds=120, raid_poll_seconds=600
        )
        self.events = []

    def dispatch(self, name, event):
        self.events.append((name, event))


def m(tag, name, **kw):
    base = dict(
        tag=tag,
        name=name,
        role=NS(value="member"),
        town_hall=16,
        trophies=5000,
        league=NS(name="Legend League"),
        donations=0,
        received=0,
        exp_level=200,
        builder_base_trophies=0,
    )
    return NS(**{**base, **kw})


def clan_obj(members, level=10):
    return NS(
        tag="#C",
        name="Clan",
        level=level,
        war_league=NS(name="Gold I"),
        capital_league=NS(name="Gold"),
        capital_districts=[],
        badge=NS(url="u"),
        members=members,
    )


def player_obj(trophies=5000, attack_wins=10, **kw):
    base = dict(
        tag="#A",
        name="Alice",
        town_hall=16,
        exp_level=200,
        trophies=trophies,
        best_trophies=5500,
        league=NS(name="Legend League"),
        attack_wins=attack_wins,
        defense_wins=0,
        war_stars=10,
        war_opted_in=True,
        clan=NS(tag="#C"),
        role=NS(value="member"),
        achievements=[NS(name="Games Champion", value=100)],
        clan_capital_contributions=0,
        heroes=[],
        donations=0,
        received=0,
    )
    return NS(**{**base, **kw})


class FakeCoc:
    def __init__(self):
        self.clan = None
        self.player = None
        self.war = None

    async def get_clan(self, tag):
        return self.clan

    async def get_player(self, tag):
        return self.player

    async def get_current_war(self, tag):
        return self.war


@pytest.fixture
async def setup(db):
    coc = FakeCoc()
    bot = FakeBot(db, coc)
    await db.add_guild_clan(1, "#C")
    return bot, coc, Collector(bot)


async def test_clan_poll_baseline_then_events(setup):
    bot, coc, col = setup
    coc.clan = clan_obj([m("#A", "Alice"), m("#B", "Bob", donations=5)])
    await col._poll_clan("#C", NOW)
    assert bot.events == []  # first sight is a baseline
    coc.clan = clan_obj([m("#B", "Bob", donations=20, role=NS(value="admin")), m("#D", "Dan")])
    await col._poll_clan("#C", NOW)
    names = sorted(n for n, _ in bot.events)
    assert names == ["clash_donations", "clash_member_join", "clash_member_leave", "clash_role_change"]
    row = await bot.db.fetchone("SELECT * FROM season_stats WHERE player_tag = '#B'")
    assert row["donations"] == 15
    assert (await bot.db.get_player_row("#A"))["clan_tag"] is None
    assert (await bot.db.get_clan_row("#C"))["member_count"] == 2


async def test_player_poll_legend_battles(setup):
    bot, coc, col = setup
    coc.player = player_obj()
    await col._poll_player("#A", NOW)
    coc.player = player_obj(trophies=5035, attack_wins=11)
    await col._poll_player("#A", NOW + timedelta(minutes=1))
    coc.player = player_obj(trophies=5010, attack_wins=11)
    await col._poll_player("#A", NOW + timedelta(minutes=2))
    battles = [e for n, e in bot.events if n == "clash_legend_battle"]
    assert [(b.kind, b.trophies, b.trophies_after) for b in battles] == [
        ("attack", 35, 5035),
        ("defense", -25, 5010),
    ]
    day = await bot.db.fetchone("SELECT * FROM legend_days WHERE player_tag = '#A'")
    assert (day["start_trophies"], day["end_trophies"]) == (5000, 5010)
    assert (await bot.db.get_player_row("#A"))["last_seen"] is not None


async def test_snapshots_survive_restart(setup):
    bot, coc, col = setup
    coc.player = player_obj()
    await col._poll_player("#A", NOW)
    col2 = Collector(bot)
    for loop in col2._loops:
        loop.start = lambda *a, **k: None
    await col2.start()
    coc.player = player_obj(trophies=5030, attack_wins=11)
    await col2._poll_player("#A", NOW + timedelta(minutes=1))
    assert any(n == "clash_legend_battle" for n, _ in bot.events)


def war_member(tag, name, pos, th=16):
    return NS(tag=tag, name=name, map_position=pos, town_hall=th)


def war_obj(state, attacks):
    ours = NS(
        tag="#C",
        name="Clan",
        stars=sum(a.stars for a in attacks if a.attacker_tag == "#A"),
        destruction=50.0,
        attacks_used=len(attacks),
        members=[war_member("#A", "Alice", 1)],
    )
    theirs = NS(
        tag="#O", name="Opp", stars=0, destruction=0.0, attacks_used=0, members=[war_member("#X", "Xeno", 1)]
    )
    t = NS(time=datetime(2026, 11, 6, tzinfo=UTC))
    return NS(
        state=state,
        is_cwl=False,
        type="random",
        clan=theirs,
        opponent=ours,
        preparation_start_time=t,
        start_time=t,
        end_time=t,
        team_size=1,
        attacks_per_member=2,
        war_tag=None,
        attacks=attacks,
    )


async def test_war_poll_records_attacks_and_states(setup):
    bot, coc, col = setup
    a1 = NS(
        order=1,
        attacker_tag="#A",
        defender_tag="#X",
        stars=2,
        destruction=80.0,
        duration=120,
        is_fresh_attack=True,
    )
    coc.war = war_obj("inWar", [])
    await col._poll_war("#C", NOW)
    coc.war = war_obj("inWar", [a1])
    await col._poll_war("#C", NOW)
    a2 = NS(
        order=2,
        attacker_tag="#A",
        defender_tag="#X",
        stars=3,
        destruction=100.0,
        duration=100,
        is_fresh_attack=False,
    )
    coc.war = war_obj("warEnded", [a1, a2])
    await col._poll_war("#C", NOW)
    attacks = [e for n, e in bot.events if n == "clash_war_attack"]
    assert [(a.attack["stars"], a.attack["new_stars"], a.attack["is_opponent"]) for a in attacks] == [
        (2, 2, 0),
        (3, 1, 0),
    ]
    states = [(e.old_state, e.new_state) for n, e in bot.events if n == "clash_war_state"]
    assert states == [(None, "inWar"), ("inWar", "warEnded")]
    war = await bot.db.fetchone("SELECT * FROM wars")
    assert war["opponent_tag"] == "#O" and war["result"] == "win" and war["clan_stars"] == 5
    members = await bot.db.fetchall("SELECT * FROM war_members ORDER BY is_opponent")
    assert [(r["player_tag"], r["is_opponent"]) for r in members] == [("#A", 0), ("#X", 1)]


async def test_cwl_round_from_league_group(setup):
    bot, coc, col = setup
    war = war_obj("inWar", [])
    war.is_cwl = True
    war.war_tag = "#W2"
    coc.war = war
    ours = NS(tag="#C", members=[NS(tag="#A")])

    async def get_league_group(tag):
        return NS(clans=[ours], rounds=[["#W1"], ["#W2", "#W3"]])

    coc.get_league_group = get_league_group
    await col._poll_war("#C", NOW)
    row = await bot.db.fetchone("SELECT * FROM wars")
    assert (row["war_type"], row["cwl_round"], row["cwl_season"]) == ("cwl", 2, "2026-11")


# ------------------------------------------------------------------ battle log, units, settings


class FakeHttp:
    base_url = "https://api.example"

    def __init__(self):
        self.items = []

    async def request(self, route):
        return {"items": list(self.items)}


def legend_player(trophies, units=None, supers=(), clan="#C"):
    raw = {
        "leagueTier": {"name": "Legend I"},
        "troops": [
            {"name": n, "level": lvl, "village": "home"} for n, lvl in (units or {"Barbarian": 11}).items()
        ]
        + [{"name": s, "level": 11, "village": "home", "superTroopIsActive": True} for s in supers],
    }
    p = player_obj(trophies=trophies, clan=NS(tag=clan, name="Clan") if clan else None)
    p._raw_data = raw
    return p


def battle(ts, attack=True, opp="#O", kind="legend", stars=3, pct=100):
    return {
        "battleTimestamp": ts,
        "battleType": kind,
        "attack": attack,
        "opponentPlayerTag": opp,
        "opponentName": "Opp",
        "opponentTownHallLevel": 17,
        "stars": stars,
        "destructionPercentage": pct,
        "armyShareCode": "u1x2",
    }


async def test_battle_log_records_exact_battles(setup):
    bot, coc, col = setup
    coc.http = FakeHttp()
    coc.http.items = [battle("20261107T110000.000Z", opp="#OLD")]
    coc.player = legend_player(5000)
    await col._poll_player("#A", NOW)  # first sight: old battle backfilled silently, no alert
    assert not [e for n, e in bot.events if n == "clash_legend_battle"]
    old = await bot.db.fetchone("SELECT * FROM legend_events WHERE opponent_tag = '#OLD'")
    assert (old["trophies"], old["trophies_after"]) == (40, 5000)
    coc.http.items = [battle("20261107T120030.000Z", opp="#NEW", stars=2, pct=88)] + coc.http.items
    coc.player = legend_player(5028)
    await col._poll_player("#A", NOW + timedelta(minutes=1))
    (e,) = [e for n, e in bot.events if n == "clash_legend_battle"]
    assert (e.kind, e.trophies, e.trophies_after, e.opponent_tag, e.stars) == ("attack", 28, 5028, "#NEW", 2)
    assert e.from_battle_log and e.event_id
    row = await bot.db.fetchone("SELECT * FROM legend_events WHERE opponent_tag = '#NEW'")
    assert (row["army_share_code"], row["legend_day"]) == ("u1x2", "2026-11-07")
    await col._poll_player("#A", NOW + timedelta(minutes=2))  # re-reading the log adds nothing
    assert await bot.db.fetchval("SELECT COUNT(*) FROM legend_events") == 2


async def test_trophies_before_battle_appears_are_carried(setup):
    bot, coc, col = setup
    coc.http = FakeHttp()
    coc.http.items = [battle("20261107T110000.000Z")]
    coc.player = legend_player(5000)
    await col._poll_player("#A", NOW)
    coc.player = legend_player(4984)  # trophies dropped, battle log not updated yet
    await col._poll_player("#A", NOW + timedelta(minutes=1))
    assert not [e for n, e in bot.events if n == "clash_legend_battle"]
    # A lower-tier ("ranked") battle: no trophy rule, so the carried change is used.
    coc.http.items = [battle("20261107T120000.000Z", attack=False, opp="#D", kind="ranked")] + coc.http.items
    await col._poll_player("#A", NOW + timedelta(minutes=12))  # periodic re-check finds it
    (e,) = [e for n, e in bot.events if n == "clash_legend_battle"]
    assert (e.kind, e.trophies, e.opponent_tag) == ("defense", -16, "#D")


async def test_units_boosts_and_clan_moves(setup):
    bot, coc, col = setup
    coc.player = legend_player(5000, units={"Barbarian": 11})
    await col._poll_player("#A", NOW)
    coc.player = legend_player(
        5000, units={"Barbarian": 12, "Archer": 1}, supers=("Super Minion",), clan="#X"
    )
    await col._poll_player("#A", NOW + timedelta(minutes=1))
    by = {n: e for n, e in bot.events}
    assert set(by["clash_units_upgraded"].changes) == {
        ("Troop", "Barbarian", 11, 12),
        ("Troop", "Archer", None, 1),
    }
    assert by["clash_super_troops_boosted"].troops == ("Super Minion",)
    moved = by["clash_player_clan_changed"]
    assert (moved.old_clan_tag, moved.new_clan_tag) == ("#C", "#X")


async def test_clan_settings_changes(setup):
    bot, coc, col = setup
    c = clan_obj([m("#A", "Alice")])
    c._raw_data = {"description": "old", "requiredTownhallLevel": 14, "labels": []}
    coc.clan = c
    await col._poll_clan("#C", NOW)
    c2 = clan_obj([m("#A", "Alice")])
    c2._raw_data = {"description": "new", "requiredTownhallLevel": 14, "labels": []}
    coc.clan = c2
    await col._poll_clan("#C", NOW)
    (e,) = [e for n, e in bot.events if n == "clash_clan_settings"]
    assert e.changes == (("description", "old", "new"),)
