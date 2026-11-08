from datetime import UTC, datetime, timedelta

from clashhero.cogs import _live_embeds as L


def test_war_embed_states():
    war = {
        "war_type": "cwl",
        "cwl_round": 2,
        "state": "inWar",
        "opponent_name": "Opp",
        "start_time": None,
        "end_time": "2026-11-07T00:00:00+00:00",
        "result": None,
        "attacks_per_member": 1,
        "team_size": 2,
        "clan_stars": 3,
        "opponent_stars": 2,
        "clan_destruction": 50.0,
        "opponent_destruction": 40.0,
        "clan_attacks": 1,
        "opponent_attacks": 1,
    }
    members = [
        {"player_tag": "#A", "name": "A", "town_hall": 16, "is_opponent": 0},
        {"player_tag": "#X", "name": "X", "town_hall": 15, "is_opponent": 1},
    ]
    attacks = [
        {
            "attack_order": 1,
            "attacker_tag": "#A",
            "defender_pos": 1,
            "stars": 3,
            "destruction": 100.0,
            "is_opponent": 0,
        }
    ]
    e = L.war_embed(war, "Clan", members, attacks, {"#A": "A"})
    assert "round 2" in e.title and "Battle day" in e.description
    assert L.th_breakdown(members, False) == "TH16×1"
    assert L.war_embed(None, "Clan", [], [], {}).title.endswith("not in war")
    ended = L.war_embed({**war, "state": "warEnded", "result": "win"}, "Clan", members, [], {})
    assert "WIN" in ended.description


def test_clan_games_embed():
    rows = [
        {"player_tag": "#A", "name": "A", "points": 4000},
        {"player_tag": "#B", "name": None, "points": 3600},
    ]
    e = L.clan_games_embed("Clan", rows, datetime(2026, 11, 28, 8, tzinfo=UTC))
    assert "7,600" in e.description and "tier 2/6" in e.description


def test_last_seen_and_ago():
    now = datetime.now(UTC)
    assert L._ago((now - timedelta(minutes=5)).isoformat(), now) == "5m"
    assert L._ago((now - timedelta(hours=3)).isoformat(), now) == "3h"
    assert L._ago(None) == "—"
    e = L.last_seen_embed(
        "Clan",
        [
            {"name": "A", "last_seen": now.isoformat(), "day": 3, "week": 9},
            {"name": "B", "last_seen": None, "day": None, "week": None},
        ],
    )
    assert "A" in e.description


def test_clan_showcase():
    clan = {
        "clan_tag": "#C",
        "name": "Clan",
        "badge_url": None,
        "level": 10,
        "member_count": 2,
        "war_league": "Gold I",
        "capital_league": None,
    }
    e = L.clan_showcase_embed(clan, [{"town_hall": 16}, {"town_hall": 16}], "desc")
    assert "TH16×2" in e.fields[-1].value
    assert L.clan_showcase_embed(None, [], None).title


def test_live_line():
    from clashhero.cogs.live import live_line

    assert (
        live_line({"kind": "war_embed", "scope": "#A", "channel_id": 5}) == "📡 **Current war** · `#A` → <#5>"
    )
    assert live_line({"kind": "other", "scope": "", "channel_id": 5}) == "📡 **other** · `server` → <#5>"
