from datetime import UTC, datetime, timedelta

from clashhero.cogs._info_embeds import (
    UnitLevel,
    activity_histogram,
    ago,
    render_histogram,
    rushed_progress,
    th_distribution,
    upgrade_progress,
)
from clashhero.cogs.summary import missed_by_clan, missed_by_player, win_rate
from clashhero.repos import stats

NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)


def test_ago():
    assert ago(None, NOW) == "—"
    assert ago(NOW - timedelta(minutes=5), NOW) == "5m"
    assert ago((NOW - timedelta(hours=3)).isoformat(), NOW) == "3h"
    assert ago(NOW - timedelta(days=2, hours=1), NOW) == "2d"


def test_activity_histogram_with_offset():
    rows = [("2026-11-06T23", 2), ("2026-11-07T00", 3), ("2026-11-05T23", 1)]
    assert activity_histogram(rows)[23] == 3
    shifted = activity_histogram(rows, offset_minutes=60)  # UTC+1
    assert shifted[0] == 3 and shifted[1] == 3
    text = render_histogram(shifted)
    assert text.count("\n") == 25 and "00:00" in text


def test_th_distribution():
    assert th_distribution([16, 15, 16, None, 14]) == [(16, 2), (15, 1), (14, 1)]


def units():
    return [
        UnitLevel("Barbarian", "troops", 10, 12, 11),  # rushed by 1, 2 remaining
        UnitLevel("Archer Queen", "heroes", 90, 95, 85),  # fine, 5 remaining
        UnitLevel("New Troop", "troops", 1, 3, None),  # new at this TH, not rushable
        UnitLevel("Giant Gauntlet", "equipment", 5, 18, 15),  # equipment excluded from rush
    ]


def test_rushed_progress():
    rushed, prog = rushed_progress(units())
    assert [u.name for u in rushed] == ["Barbarian"]
    assert (prog.done, prog.total) == (1, 11 + 85)
    assert round(prog.percent, 2) == round(100 / 96, 2)


def test_upgrade_progress():
    pending, prog = upgrade_progress(units())
    assert [u.name for u in pending] == ["Barbarian", "Archer Queen", "New Troop", "Giant Gauntlet"]
    assert prog.total == 12 + 95 + 3 + 18
    assert prog.done == 10 + 90 + 1 + 5


def test_summary_aggregations():
    rows = [
        {"clan_tag": "#A", "war_id": 1, "player_tag": "#P1", "name": "Ann", "missed": 2},
        {"clan_tag": "#A", "war_id": 2, "player_tag": "#P1", "name": "Ann", "missed": 1},
        {"clan_tag": "#A", "war_id": 2, "player_tag": "#P2", "name": "Bob", "missed": 1},
    ]
    assert missed_by_clan(rows) == {"#A": {"attacks": 4, "players": 2, "wars": 2}}
    assert missed_by_player(rows)[0] == ("Ann", "#P1", 3, 2)
    assert win_rate(3, 4) == "75%" and win_rate(0, 0) == "—"


async def seed_wars(db):
    await db.execute(
        "INSERT INTO wars (id, clan_tag, opponent_name, war_type, state, team_size, attacks_per_member, "
        "preparation_start, end_time, result) VALUES "
        "(1, '#C', 'Opp', 'random', 'warEnded', 2, 2, '2026-11-03T00:00:00+00:00', "
        "'2026-11-05T00:00:00+00:00', 'win'), "
        "(2, '#C', 'Opp2', 'random', 'inWar', 2, 2, '2026-11-06T00:00:00+00:00', NULL, NULL)"
    )
    await db.executemany(
        "INSERT INTO war_members (war_id, player_tag, name, town_hall, map_position, is_opponent) "
        "VALUES (?, ?, ?, 16, ?, ?)",
        [(1, "#A", "Ann", 1, 0), (1, "#B", "Bob", 2, 0), (1, "#X", "Xeno", 1, 1)],
    )
    await db.executemany(
        "INSERT INTO war_attacks (war_id, attack_order, attacker_tag, defender_tag, stars, destruction, "
        "is_fresh, new_stars, is_opponent, seen_at) VALUES (?, ?, ?, ?, ?, 90, 1, ?, ?, 'x')",
        [(1, 1, "#A", "#X", 3, 3, 0), (1, 2, "#X", "#A", 2, 2, 1)],
    )


async def test_stats_wars(db):
    await seed_wars(db)
    start = datetime(2026, 11, 2, 5, tzinfo=UTC)
    end = datetime(2026, 11, 30, 5, tzinfo=UTC)
    missed = await stats.missed_attacks(db, ["#C"], start, end)
    assert sorted((r["player_tag"], r["missed"]) for r in missed) == [("#A", 1), ("#B", 2)]
    results = await stats.war_results(db, ["#C"], start, end)
    assert (results[0]["wins"], results[0]["total"]) == (1, 1)
    current = await stats.current_wars(db, ["#C"])
    assert [r["id"] for r in current] == [2]
    leaders = await stats.war_star_leaders(db, ["#C"], start, end)
    assert [(r["player_tag"], r["value"]) for r in leaders] == [("#A", 3)]
    attacks = await stats.attack_rows(db, ["#C"], start, end)
    assert [(r["attacker_name"], r["defender_name"]) for r in attacks] == [("Ann", "Xeno")]


async def test_stats_season_and_activity(db):
    await db.executemany(
        "INSERT INTO players (player_tag, name, town_hall, trophies, clan_tag, last_seen, updated_at) "
        "VALUES (?, ?, 16, ?, '#C', ?, 'x')",
        [("#A", "Ann", 5000, NOW.isoformat()), ("#B", "Bob", 4000, None)],
    )
    await db.executemany(
        "INSERT INTO season_stats (season_id, player_tag, clan_tag, donations, received, activity) "
        "VALUES ('s1', ?, '#C', ?, ?, ?)",
        [("#A", 100, 10, 5), ("#B", 300, 0, 1)],
    )
    await db.executemany(
        "INSERT INTO activity (player_tag, hour, clan_tag, count) VALUES (?, ?, '#C', ?)",
        [("#A", "2026-11-07T10", 2), ("#A", "2026-11-01T10", 9)],
    )
    don = await stats.clan_donations(db, "#C", "s1")
    assert [r["name"] for r in don] == ["Bob", "Ann"]
    seen = await stats.lastseen_rows(db, "#C", NOW)
    assert [(r["name"], r["day_activity"]) for r in seen] == [("Ann", 2), ("Bob", 0)]
    hours = await stats.activity_hours(db, ["#C"], NOW - timedelta(days=1))
    assert hours == [("2026-11-07T10", 2)]
    top = await stats.top_season_players(db, ["#C"], "s1", "donations", 1)
    assert top[0]["name"] == "Bob"
    trophies = await stats.clan_trophy_summary(db, ["#C"])
    assert (trophies[0]["members"], trophies[0]["total"]) == (2, 9000)


async def test_membership_history(db):
    await db.execute(
        "INSERT INTO clan_membership_events (clan_tag, player_tag, kind, occurred_at) VALUES "
        "('#C', '#A', 'join', '2026-11-01'), ('#C', '#A', 'leave', '2026-11-03')"
    )
    rows = await stats.membership_history(db, "#A")
    assert [r["kind"] for r in rows] == ["leave", "join"]
    assert await stats.join_dates(db, "#C") == {"#A": "2026-11-01"}
