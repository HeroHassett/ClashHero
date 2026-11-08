from datetime import UTC, datetime

from clashhero.services.battlelog import (
    Battle,
    Cursor,
    army_link,
    assign_trophies,
    parse_battles,
    parse_timestamp,
    take_new,
)


def item(ts, attack=True, kind="legend", opp="#O", stars=3, pct=100):
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


def b(ts, attack=True, kind="legend", opp="#O"):
    return parse_battles([item(ts, attack, kind, opp)])[0]


def test_parse():
    (x,) = parse_battles([item("20261104T020237.000Z", attack=False, kind="ranked", stars=1, pct=55)])
    assert (x.kind, x.ranked, x.stars, x.destruction, x.opponent_th) == ("defense", True, 1, 55, 17)
    assert x.occurred_at == datetime(2026, 11, 4, 2, 2, 37, tzinfo=UTC)
    assert not b("20261104T020237.000Z", kind="homeVillage").ranked
    assert parse_timestamp("20261104T020237.000Z").tzinfo is UTC


def test_first_look_is_baseline():
    fresh, cur = take_new([b("20261104T020000.000Z"), b("20261104T010000.000Z")], None)
    assert fresh == [] and cur.timestamp == "20261104T020000.000Z"


def test_take_new_oldest_first_and_same_second():
    cur = Cursor("20261104T020000.000Z", [b("20261104T020000.000Z", opp="#A").key])
    log = [
        b("20261104T030000.000Z", opp="#C"),
        b("20261104T020000.000Z", attack=False, opp="#B"),  # same second, different battle
        b("20261104T020000.000Z", opp="#A"),  # already seen
        b("20261104T010000.000Z", opp="#Z"),
    ]
    fresh, cur2 = take_new(log, cur)
    assert [x.opponent_tag for x in fresh] == ["#B", "#C"]
    assert cur2.timestamp == "20261104T030000.000Z"
    assert take_new(log, cur2)[0] == []


def test_assign_trophies():
    att, dfn = b("20261104T010000.000Z"), b("20261104T020000.000Z", attack=False)
    assert assign_trophies([att], 38) == [38]
    assert assign_trophies([dfn], -16) == [-16]
    assert assign_trophies([att, att], 75) == [38, 37]
    assert assign_trophies([att, dfn], 20) == [20, 0]  # net only: approximate
    assert assign_trophies([dfn], 0) == [0]


def test_cursor_json_roundtrip():
    c = Cursor("20261104T010000.000Z", ["k"], -12)
    assert Cursor.from_json(c.to_json()) == c


def test_army_link():
    assert army_link("u1x2").endswith("action=CopyArmy&army=u1x2")
    assert army_link(None) is None


def test_battle_is_hashable():
    assert len({b("20261104T010000.000Z"), b("20261104T010000.000Z")}) == 1 and isinstance(
        b("20261104T010000.000Z"), Battle
    )


# The game's Legend II battle log: attacker earns rate x destruction (0-4 at 0 stars), the defender
# gains what's left of 40 (0 against a triple).
RANKED_CASES = [
    (True, 3, 100, 40),
    (True, 2, 81, 26),
    (True, 1, 75, 13),  # 5 + 74 // 9
    (True, 0, 18, 1),
    (False, 3, 100, 0),
    (False, 1, 60, 29),  # 40 - (5 + 59 // 9)
    (False, 2, 91, 11),
    (False, 0, 49, 36),
]


def test_ranked_trophies_below_legend_one():
    from clashhero.services.battlelog import parse_battles, ranked_trophies, rule_trophies

    for attack, stars, pct, expected in RANKED_CASES:
        (battle,) = parse_battles([item("20261107T120000.000Z", attack, "ranked", stars=stars, pct=pct)])
        assert ranked_trophies(battle) == rule_trophies(battle) == expected, (attack, stars, pct)
    (legend,) = parse_battles([item("20261107T120000.000Z", False, "legend", stars=1, pct=56)])
    assert ranked_trophies(legend) is None and rule_trophies(legend) == -11


async def test_fixes_sql_matches_the_ranked_rule(db):
    """db/fixes.sql re-scores stored lower-tier battles with the same rule."""
    from importlib import resources

    for i, (attack, stars, pct, _) in enumerate(RANKED_CASES):
        await db.execute(
            "INSERT INTO legend_events (player_tag, legend_day, season_id, kind, trophies, "
            "trophies_after, occurred_at, battle_type, stars, destruction) "
            "VALUES ('#A', 'd', 's', ?, 0, 0, ?, 'ranked', ?, ?)",
            ("attack" if attack else "defense", f"t{i:02d}", stars, pct),
        )
    await db.conn.executescript(resources.files("clashhero.db").joinpath("fixes.sql").read_text())
    rows = await db.fetchall("SELECT trophies FROM legend_events ORDER BY occurred_at")
    assert [r["trophies"] for r in rows] == [c[3] for c in RANKED_CASES]


# (attack, stars, %, trophies) from the game's Legend I battle log
LEGEND_GAME_CASES = [
    (True, 2, 83, 27),
    (True, 2, 92, 30),
    (True, 2, 69, 22),
    (True, 2, 97, 31),
    (True, 3, 100, 40),
    (False, 1, 88, -14),
    (False, 2, 86, -28),
    (False, 2, 99, -32),
    (False, 1, 56, -11),
    (False, 3, 100, -40),
]


def test_legend_rule_matches_the_game():
    from clashhero.services.battlelog import legend_trophies, parse_battles

    for attack, stars, pct, expected in LEGEND_GAME_CASES:
        (battle,) = parse_battles([item("20261108T120000.000Z", attack, "legend", stars=stars, pct=pct)])
        assert legend_trophies(battle) == expected, (attack, stars, pct)


async def test_fixes_sql_rescores_legend_battles(db):
    from importlib import resources

    for i, (attack, stars, pct, _) in enumerate(LEGEND_GAME_CASES):
        await db.execute(
            "INSERT INTO legend_events (player_tag, legend_day, season_id, kind, trophies, "
            "trophies_after, occurred_at, battle_type, stars, destruction) "
            "VALUES ('#A', 'd', 's', ?, 99, 0, ?, 'legend', ?, ?)",
            ("attack" if attack else "defense", f"t{i:02d}", stars, pct),
        )
    await db.execute(  # inferred rows (no stars) are left alone
        "INSERT INTO legend_events (player_tag, legend_day, season_id, kind, trophies, "
        "trophies_after, occurred_at) VALUES ('#A', 'd', 's', 'defense', -13, 0, 'z')"
    )
    await db.conn.executescript(resources.files("clashhero.db").joinpath("fixes.sql").read_text())
    rows = await db.fetchall("SELECT trophies FROM legend_events ORDER BY occurred_at")
    assert [r["trophies"] for r in rows] == [c[3] for c in LEGEND_GAME_CASES] + [-13]
