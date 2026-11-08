from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import discord
import pytest

from clashhero.cogs._war_embeds import (
    history_embed,
    lineup_embed,
    period_bounds,
    th_matchup,
    war_overview_embed,
)
from clashhero.repos import war as wr

NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)


async def add_war(
    db, clan="#C", *, war_type="random", state="warEnded", days_ago=1, per=2, season=None, rnd=None, opp="Opp"
):
    prep = (NOW - timedelta(days=days_ago)).isoformat()
    return await db.execute(
        "INSERT INTO wars (clan_tag, opponent_tag, opponent_name, war_type, cwl_season, cwl_round, "
        "state, team_size, attacks_per_member, preparation_start) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (clan, "#O" + opp, opp, war_type, season, rnd, state, 2, per, prep),
    )


async def add_member(db, war_id, tag, name, th, pos, opp=False):
    await db.execute(
        "INSERT INTO war_members (war_id, player_tag, name, town_hall, map_position, is_opponent) "
        "VALUES (?,?,?,?,?,?)",
        (war_id, tag, name, th, pos, int(opp)),
    )


async def add_attack(
    db, war_id, order, att, dfn, stars, dest, att_th, def_th, *, opp=False, fresh=True, new_stars=None
):
    await db.execute(
        "INSERT INTO war_attacks (war_id, attack_order, attacker_tag, defender_tag, stars, "
        "destruction, is_fresh, new_stars, attacker_th, defender_th, is_opponent, seen_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            war_id,
            order,
            att,
            dfn,
            stars,
            dest,
            int(fresh),
            stars if new_stars is None else new_stars,
            att_th,
            def_th,
            int(opp),
            NOW.isoformat(),
        ),
    )


@pytest.fixture
async def seeded(db):
    """One ended regular war: Alice (TH16) 3★ up + 2★ even; Bob missed both; enemy tripled Bob."""
    w = await add_war(db)
    await add_member(db, w, "#A", "Alice", 16, 1)
    await add_member(db, w, "#B", "Bob", 15, 2)
    await add_member(db, w, "#X", "Xeno", 17, 1, opp=True)
    await add_member(db, w, "#Y", "Yan", 16, 2, opp=True)
    await add_attack(db, w, 1, "#A", "#X", 3, 100.0, 16, 17)
    await add_attack(db, w, 2, "#A", "#Y", 2, 80.0, 16, 16)
    await add_attack(db, w, 3, "#Y", "#B", 3, 100.0, 16, 15, opp=True)
    await add_attack(db, w, 4, "#X", "#A", 1, 50.0, 17, 16, opp=True)
    return w


def test_th_bucket():
    assert wr.th_bucket(15, 16) == "up"
    assert wr.th_bucket(16, 16) == "even"
    assert wr.th_bucket(16, 14) == "down"
    assert wr.th_bucket(None, 14) == "even"


def test_summarize_attacks_and_buckets():
    rows = [
        {"stars": 3, "destruction": 100, "is_fresh": 1, "attacker_th": 16, "defender_th": 17},
        {"stars": 2, "destruction": 80, "is_fresh": 0, "attacker_th": 16, "defender_th": 16},
        {"stars": 0, "destruction": 20, "is_fresh": 1, "attacker_th": 16, "defender_th": 15},
    ]
    s = wr.summarize_attacks(rows, missed=1)
    assert (s.attacks, s.triples, s.zeros, s.missed, s.possible) == (3, 1, 1, 1, 4)
    assert s.avg_stars == pytest.approx(5 / 3)
    assert s.fresh_triple_rate == 0.5
    assert s.bucket_rate("up") == (1, 1.0)
    assert s.bucket_rate("down") == (1, 0.0)


def test_summarize_defenses():
    d = wr.summarize_defenses([{"stars": 3, "destruction": 100}, {"stars": 1, "destruction": 40}])
    assert d.hold_rate == 0.5 and d.avg_stars == 2


def test_remaining_attacks():
    members = [{"tag": "#A", "name": "A", "map_position": 2}, {"tag": "#B", "name": "B", "map_position": 1}]
    left = wr.remaining_attacks(members, [{"attacker_tag": "#A"}, {"attacker_tag": "#A"}], 2)
    assert [(m["tag"], n) for m, n in left] == [("#B", 2)]


async def test_missed_attacks(db, seeded):
    missed = await wr.missed_attacks(db, seeded)
    assert [(m["player_tag"], m["used"], m["allowed"]) for m in missed] == [("#B", 0, 2)]


async def test_attack_leaderboard_includes_missers(db, seeded):
    rows = dict(await wr.attack_leaderboard(db, ["#C"]))
    assert rows["#A"].attacks == 2 and rows["#A"].triples == 1 and rows["#A"].missed == 0
    assert rows["#B"].attacks == 0 and rows["#B"].missed == 2
    assert await wr.attack_leaderboard(db, ["#C"], war_type="cwl") == []
    th15 = await wr.attack_leaderboard(db, ["#C"], town_hall=15)
    assert th15 == []  # TH filter applies to attacks; missers aren't listed


async def test_defense_leaderboard(db, seeded):
    rows = dict(await wr.defense_leaderboard(db, ["#C"]))
    assert rows["#B"].triples_allowed == 1 and rows["#B"].hold_rate == 0
    assert rows["#A"].hold_rate == 1


async def test_player_war_history(db, seeded):
    h = await wr.player_war_history(db, ["#A"], since=NOW - timedelta(days=30))
    assert h.wars == 1 and h.offense.attacks == 2 and h.defense.defenses == 1
    assert h.recent[0]["attack_order"] == 2  # most recent first
    old = await wr.player_war_history(db, ["#A"], since=NOW)
    assert old.wars == 0 and old.offense.attacks == 0


async def test_period_filter_excludes_old_wars(db, seeded):
    since, until, _ = period_bounds("7d", NOW)
    w = await add_war(db, days_ago=40)
    await add_member(db, w, "#A", "Alice", 16, 1)
    await add_attack(db, w, 1, "#A", "#Z", 0, 10.0, 16, 16)
    rows = dict(await wr.attack_leaderboard(db, ["#C"], since=since, until=until))
    assert rows["#A"].attacks == 2


def test_period_bounds():
    since, until, label = period_bounds("30d", NOW)
    assert since == NOW - timedelta(days=30) and until is None
    s, u, _ = period_bounds("last_season", NOW)
    assert s < u <= NOW
    assert period_bounds("all", NOW)[:2] == (None, None)


def test_cwl_standings_win_bonus_and_prep():
    wars = [
        dict(
            clan_tag="#A",
            clan_name="A",
            clan_stars=20,
            clan_destruction=80.0,
            clan_attacks=15,
            opp_tag="#B",
            opp_name="B",
            opp_stars=18,
            opp_destruction=90.0,
            opp_attacks=15,
            team_size=15,
            state="warEnded",
        ),
        dict(
            clan_tag="#C",
            clan_name="C",
            clan_stars=25,
            clan_destruction=70.0,
            opp_tag="#D",
            opp_name="D",
            opp_stars=25,
            opp_destruction=70.0,
            team_size=15,
            state="warEnded",
        ),
        dict(
            clan_tag="#A",
            clan_name="A",
            clan_stars=5,
            clan_destruction=30.0,
            opp_tag="#C",
            opp_name="C",
            opp_stars=3,
            opp_destruction=20.0,
            team_size=15,
            state="inWar",
        ),
        dict(clan_tag="#E", clan_name="E", opp_tag="#E", opp_name="E", state="preparation"),
    ]
    rows = wr.cwl_standings(wars)
    by = {s.tag: s for s in rows}
    assert by["#A"].total_stars == 20 + 5 + 10 and by["#A"].wins == 1
    assert by["#B"].losses == 1 and by["#C"].ties == 1 and by["#D"].ties == 1
    assert by["#C"].total_stars == 28  # in-progress war counts stars but no bonus
    assert rows[0].tag == "#A" and by["#E"].total_stars == 0


def test_war_result():
    assert wr.war_result(10, 10, 80.0, 70.0) == "win"
    assert wr.war_result(9, 10, 99.0, 1.0) == "lose"
    assert wr.war_result(10, 10, 50.0, 50.0) == "tie"


async def test_cwl_member_stats_and_history(db):
    w1 = await add_war(db, war_type="cwl", season="2026-11", rnd=1, per=1, days_ago=5, opp="R1")
    w2 = await add_war(db, war_type="cwl", season="2026-11", rnd=2, per=1, days_ago=4, opp="R2")
    for w in (w1, w2):
        await add_member(db, w, "#A", "Alice", 16, 1)
        await add_member(db, w, "#E", "Enemy", 16, 1, opp=True)
    await add_attack(db, w1, 1, "#A", "#E", 3, 100.0, 16, 16)
    await add_attack(db, w1, 2, "#E", "#A", 2, 70.0, 16, 16, opp=True)
    stats = await wr.cwl_member_stats(db, "#C", "2026-11")
    a = stats[0]
    assert (a.rounds, a.attacks, a.missed, a.stars, a.triples, a.stars_allowed) == (2, 1, 1, 3, 1, 2)
    hist = await wr.player_cwl_history(db, ["#A"], "2026-11")
    assert [h["cwl_round"] for h in hist] == [2, 1]
    assert hist[1]["attack"]["stars"] == 3 and hist[1]["defense"]["stars"] == 2
    assert hist[0]["attack"] is None


async def test_callers(db, seeded):
    await wr.set_caller(db, 1, seeded, 1, player_tag="#A", user_id=5, note="hit me", assigned_by=5)
    await wr.set_caller(db, 1, seeded, 1, player_tag="#B", user_id=6, note=None, assigned_by=6)
    rows = await wr.get_callers(db, 1, seeded)
    assert [(r["position"], r["player_tag"]) for r in rows] == [(1, "#B")]
    assert await wr.clear_caller(db, 1, seeded, 1) == 1
    assert await wr.get_callers(db, 1, seeded) == []


async def test_calls_are_private_to_each_server(db, seeded):
    # Server 1 tracks its own clan; server 2 (the opponent) adds the same clan to snoop.
    await wr.set_caller(db, 1, seeded, 3, player_tag="#A", user_id=5, note="ring base", assigned_by=5)
    assert await wr.get_callers(db, 2, seeded) == []  # can't see it
    assert await wr.clear_caller(db, 2, seeded) == 0  # can't clear it
    assert await wr.clear_caller(db, 2, seeded, 3) == 0
    assert [c["note"] for c in await wr.get_callers(db, 1, seeded)] == ["ring base"]


async def test_old_shared_calls_move_to_the_owning_server(tmp_path):
    from clashhero.db import Database

    path = tmp_path / "old.db"
    db = Database(path)
    await db.connect()
    await db.add_guild_clan(7, "#OURS")
    war_id = await db.execute(
        "INSERT INTO wars (clan_tag, war_type, state, preparation_start) "
        "VALUES ('#OURS', 'cwl', 'inWar', 'x')"
    )
    await db.execute(
        "INSERT INTO war_callers (war_id, position, player_tag, user_id, assigned_by) "
        "VALUES (?, 4, '#A', 5, 5)",
        (war_id,),
    )
    await db.close()
    db = Database(path)  # restart runs the one-time move
    await db.connect()
    assert [(c["position"], c["player_tag"]) for c in await wr.get_callers(db, 7, war_id)] == [(4, "#A")]
    assert await db.fetchval("SELECT COUNT(*) FROM war_callers") == 0
    await db.close()


async def test_current_war_and_names(db, seeded):
    w = await wr.current_war(db, "#C")
    assert w["id"] == seeded
    names = await wr.player_names(db, ["#A", "#X"])
    assert names == {"#A": "Alice", "#X": "Xeno"}


# ------------------------------------------------------------------ embed builders


def fake_war(state="inWar"):
    t = NS(time=NOW)
    ours = NS(
        tag="#C",
        name="Us",
        stars=5,
        destruction=60.0,
        attacks_used=3,
        members=[
            NS(tag="#A", name="Alice", town_hall=16, map_position=1),
            NS(tag="#B", name="Bob", town_hall=15, map_position=2),
        ],
    )
    theirs = NS(
        tag="#O",
        name="Them",
        stars=2,
        destruction=40.0,
        attacks_used=2,
        members=[
            NS(tag="#X", name="Xeno", town_hall=16, map_position=1),
            NS(tag="#Y", name="Yan", town_hall=16, map_position=2),
        ],
    )
    return NS(
        state=state,
        clan=theirs,
        opponent=ours,
        team_size=2,
        attacks_per_member=2,
        is_cwl=False,
        start_time=t,
        end_time=t,
        attacks=[NS(defender_tag="#X", stars=3)],
    )


def test_war_embeds():
    color = discord.Color.blue()
    e = war_overview_embed(fake_war(), "#C", color)
    assert e.title == "Us vs Them"
    assert "5 - 2" in e.fields[0].value and "3/4" in e.fields[0].value
    prep = war_overview_embed(fake_war("preparation"), "#C", color)
    assert all(f.name != "Score" for f in prep.fields)
    lineup = lineup_embed(fake_war(), "#C", color)
    assert "Alice" in lineup.description and "★★★" in lineup.description


def test_th_matchup():
    w = fake_war()
    assert th_matchup(w.opponent.members, w.clan.members) == "TH16: 1 vs 2\nTH15: 1 vs 0"


async def test_history_embed(db, seeded):
    h = await wr.player_war_history(db, ["#A"])
    e = history_embed("Alice", "all time", h, discord.Color.blue())
    assert "Attacks: 2" in e.fields[0].value and "Hitting up: 1 hits, 100%" in e.fields[2].value
