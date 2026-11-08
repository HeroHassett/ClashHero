from dataclasses import replace

from clashhero.services.snapshots import (
    ClanSnap,
    MemberSnap,
    PlayerSnap,
    counter_delta,
    diff_clan,
    has_activity,
    infer_battles,
)


def member(tag="#P1", **kw):
    base = dict(
        tag=tag,
        name="Alice",
        role="member",
        town_hall=16,
        trophies=5000,
        league="Legend League",
        donations=0,
        received=0,
    )
    return MemberSnap(**{**base, **kw})


def player(**kw):
    base = dict(
        tag="#P1",
        name="Alice",
        town_hall=16,
        exp_level=200,
        trophies=5000,
        best_trophies=5500,
        league="Legend League",
        attack_wins=10,
        defense_wins=3,
        war_stars=1000,
        war_opted_in=True,
        clan_tag="#C",
        clan_role="member",
        games_points=10000,
        capital_contributions=500,
        heroes_total=300,
        donations=0,
        received=0,
    )
    return PlayerSnap(**{**base, **kw})


def clan(members, **kw):
    base = dict(tag="#C", name="Clan", level=10, war_league="Gold I", capital_league="Gold", capital_hall=8)
    return ClanSnap(**{**base, **kw}, members={m.tag: m for m in members})


def test_diff_clan_join_leave_role_donations():
    old = clan([member("#A"), member("#B", donations=10)])
    new = clan([member("#B", donations=25, role="admin"), member("#C")], level=11)
    d = diff_clan(old, new)
    assert [m.tag for m in d.joined] == ["#C"]
    assert [m.tag for m in d.left] == ["#A"]
    assert [(a.role, b.role) for a, b in d.role_changes] == [("member", "admin")]
    assert [(m.tag, don, rec) for m, don, rec in d.donations] == [("#B", 15, 0)]
    assert d.clan_changes == [("level", 10, 11)]


def test_counter_reset():
    assert counter_delta(100, 30) == 30
    assert counter_delta(30, 100) == 70


def test_clan_snapshot_json_roundtrip():
    c = clan([member()])
    assert ClanSnap.from_json(c.to_json()) == c


def test_infer_single_attack_and_defense():
    old = player()
    assert [
        (b.kind, b.trophies) for b in infer_battles(old, replace(old, trophies=5032, attack_wins=11))
    ] == [("attack", 32)]
    assert [(b.kind, b.trophies) for b in infer_battles(old, replace(old, trophies=4975))] == [
        ("defense", -25)
    ]


def test_infer_splits_multiple_hits():
    old = player()
    out = infer_battles(old, replace(old, trophies=5070, attack_wins=12))
    assert [b.trophies for b in out] == [35, 35]
    out = infer_battles(old, replace(old, trophies=4930))
    assert sum(b.trophies for b in out) == -70 and len(out) == 2


def test_infer_ignores_resets_and_non_legend():
    old = player()
    assert infer_battles(old, replace(old, trophies=3000)) == []
    other = player(league="Crystal League I")
    assert infer_battles(other, replace(other, trophies=5030)) == []


def test_infer_zero_trophy_attack():
    old = player()
    out = infer_battles(old, replace(old, attack_wins=11))
    assert [(b.kind, b.trophies) for b in out] == [("attack", 0)]


def test_activity():
    old = player()
    assert has_activity(old, replace(old, donations=5))
    assert has_activity(old, replace(old, trophies=5030))
    assert not has_activity(old, replace(old, trophies=4970))
