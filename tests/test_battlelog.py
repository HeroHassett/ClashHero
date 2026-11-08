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
