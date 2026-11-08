from datetime import UTC, datetime

import pytest

from clashhero.cogs import _log_embeds as E
from clashhero.cogs.logs import parse_color
from clashhero.services.snapshots import MemberSnap
from clashhero.utils.resolve import ResolveError

M = MemberSnap(
    tag="#A",
    name="Alice",
    role="admin",
    town_hall=16,
    trophies=5000,
    league="Legend League",
    donations=10,
    received=5,
)
WAR = {
    "id": 1,
    "war_type": "random",
    "opponent_name": "Opp",
    "team_size": 2,
    "cwl_round": None,
    "clan_stars": 3,
    "opponent_stars": 1,
    "clan_destruction": 70.0,
    "opponent_destruction": 30.0,
    "start_time": "2026-11-06T00:00:00+00:00",
    "end_time": "2026-11-07T00:00:00+00:00",
    "result": "win",
    "attacks_per_member": 2,
}


def test_parse_color():
    assert parse_color("#00ff88") == 0x00FF88
    assert parse_color("0xFF0000") == 0xFF0000
    assert parse_color(None) is None
    with pytest.raises(ResolveError):
        parse_color("nope")


def test_missed_attacks():
    members = [
        {"player_tag": "#A", "name": "A", "map_position": 1, "is_opponent": 0},
        {"player_tag": "#B", "name": "B", "map_position": 2, "is_opponent": 0},
        {"player_tag": "#C", "name": "C", "map_position": 3, "is_opponent": 0},
        {"player_tag": "#X", "name": "X", "map_position": 1, "is_opponent": 1},
    ]
    attacks = [
        {"attacker_tag": "#A", "is_opponent": 0},
        {"attacker_tag": "#A", "is_opponent": 0},
        {"attacker_tag": "#B", "is_opponent": 0},
        {"attacker_tag": "#X", "is_opponent": 1},
    ]
    missed = E.missed_attacks(members, attacks, 2)
    assert [(m["player_tag"], used) for m, used in missed] == [("#B", 1), ("#C", 0)]
    embed = E.missed_attacks_embed(WAR, missed, 2)
    assert [f.name for f in embed.fields] == ["2 missed", "1 missed"]
    assert E.missed_attacks_embed(WAR, [], 2).description.startswith("Everyone")


def test_embed_builders_smoke():
    attack = {
        "is_opponent": 0,
        "attacker_pos": 1,
        "defender_pos": 2,
        "attacker_th": 16,
        "defender_th": 15,
        "new_stars": 2,
        "is_fresh": 1,
        "stars": 2,
        "destruction": 85.0,
    }
    builders = [
        E.clan_attribute_embed("Clan", "#C", "level", 10, 11),
        E.clan_attribute_embed("Clan", "#C", "war_league", "Gold I", "Crystal III"),
        E.member_join_embed("Clan", M, 40, {"town_hall": 16, "heroes_total": 300, "war_stars": 900}),
        E.member_join_embed("Clan", M, 40, None),
        E.member_leave_embed("Clan", M, 39, 1200),
        E.role_change_embed("Clan", "Alice", "#A", "member", "admin"),
        E.name_change_embed("#A", "Old", "New"),
        E.town_hall_embed("Alice", "#A", 15, 16),
        E.war_preference_embed("Alice", "#A", False),
        E.league_change_embed("Alice", "#A", None, "Legend League", 5000),
        E.donations_embed("Clan", [("Alice", 20, 0), ("Bob", 0, 15)]),
        E.clan_games_progress_embed("Clan", [("Alice", 200, 1200)]),
        E.war_attack_embed(WAR, attack, "Alice", "Xeno"),
        E.war_attack_embed(
            {**WAR, "war_type": "cwl", "cwl_round": 3}, {**attack, "is_opponent": 1}, "X", "A"
        ),
        E.war_state_embed(WAR, "preparation"),
        E.war_state_embed(WAR, "inWar"),
        E.war_state_embed(WAR, "warEnded"),
        E.cwl_lineup_embed("2026-11", ["Alice (TH16)"], []),
        E.capital_contribution_embed("Alice", "#A", 5000),
        E.raid_attack_embed("Alice", 2, 4000, 4, 6),
        E.raid_summary_embed(
            "Clan",
            {"total_loot": 1, "attack_count": 2},
            [{"name": "Alice", "attacks": 6, "attack_limit": 6, "loot": 25000}],
        ),
        E.maintenance_embed(True, datetime(2026, 11, 7, tzinfo=UTC)),
        E.maintenance_embed(False, datetime(2026, 11, 7, tzinfo=UTC)),
    ]
    for embed in builders:
        assert embed.to_dict()
    assert "promoted" in E.role_change_embed("C", "A", "#A", "member", "admin").description
    assert "demoted" in E.role_change_embed("C", "A", "#A", "coLeader", "admin").description
