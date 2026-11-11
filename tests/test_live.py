from datetime import UTC, datetime, timedelta

import discord

from clashhero.cogs import _live_embeds as L
from clashhero.services.emojis import EMOJIS

WAR = {
    "war_type": "cwl",
    "cwl_round": 2,
    "state": "inWar",
    "opponent_name": "Opp",
    "opponent_tag": "#OPP",
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
MEMBERS = [
    {"player_tag": "#A", "name": "A", "town_hall": 16, "is_opponent": 0},
    {"player_tag": "#X", "name": "X", "town_hall": 15, "is_opponent": 1},
]
ATTACKS = [
    {
        "attack_order": 1,
        "attacker_tag": "#A",
        "defender_tag": "#X",
        "defender_pos": 1,
        "stars": 3,
        "new_stars": 3,
        "destruction": 100.0,
        "is_opponent": 0,
    }
]
NAMES = {"#A": "A", "#X": "X"}


def test_war_embed_states():
    e = L.war_embed(WAR, "Clan", MEMBERS, ATTACKS, NAMES, clan_tag="#C", badge_url="https://badge/c.png")
    assert e.title == "CWL round 2 vs Opp" and e.url.endswith("tag=OPP")
    assert e.author.name == "Clan" and e.author.icon_url == "https://badge/c.png"
    assert e.description.startswith("**Battle day** · 2 vs 2\nEnds <t:")
    assert e.color == discord.Color.gold()
    assert [(f.name, f.inline) for f in e.fields[:2]] == [("Clan", True), ("Opp", True)]
    assert e.fields[0].value == "★ **3** · 50.00%\n⚔️ **1**/2 attacks"
    assert (e.fields[2].name, e.fields[2].value) == ("Our lineup", "TH16×1")
    assert (e.fields[3].name, e.fields[3].value) == ("Their lineup", "TH15×1")
    assert e.fields[4].value == "⚔️ A → **1.** X ★★★ **100%**"
    assert L.th_breakdown(MEMBERS, False) == "TH16×1"

    prep = L.war_embed({**WAR, "state": "preparation"}, "Clan", MEMBERS, [], NAMES)
    assert "**Preparation day**" in prep.description and "Battle day starts" in prep.description
    assert [f.name for f in prep.fields] == ["Our lineup", "Their lineup"]  # no score before battle day

    ended = L.war_embed({**WAR, "state": "warEnded", "result": "win"}, "Clan", MEMBERS, [], {})
    assert ended.description.startswith("▲ **Victory**") and ended.color == discord.Color.green()
    tie = L.war_embed({**WAR, "state": "warEnded", "result": "tie"}, "Clan", MEMBERS, [], {})
    assert "**Tie**" in tie.description and tie.color == discord.Color.light_grey()

    idle = L.war_embed(None, "Clan", [], [], {}, clan_tag="#C", badge_url="https://badge/c.png")
    assert idle.title == "Not in war" and idle.author.name == "Clan" and idle.author.url.endswith("tag=C")


def test_war_embed_with_game_icons(monkeypatch):
    big = 10**18
    icons = {
        name: f"<:{name}:{big + i}>"
        for i, name in enumerate(
            (
                "ui_sword",
                "ui_shield",
                "ui_war_star",
                "ui_star_new",
                "ui_star_old",
                "ui_star_empty",
                "th16",
                "th15",
            )
        )
    }
    monkeypatch.setattr(EMOJIS, "_by_name", icons)
    war = {**WAR, "clan_badge_url": "https://badge/c.png", "opponent_badge_url": "https://badge/o.png"}
    attacks = [
        {**ATTACKS[0], "attack_order": n, "is_opponent": n % 2, "stars": 2, "new_stars": 1}
        for n in range(1, 21)
    ]
    e = L.war_embed(war, "Clan", MEMBERS, attacks, NAMES)
    assert e.thumbnail.url == "https://badge/o.png" and e.author.icon_url == "https://badge/c.png"
    assert e.fields[0].value.startswith(icons["ui_war_star"])
    assert e.fields[2].value == f"{icons['th16']}×1"
    recent = e.fields[4].value.split("\n")
    assert len(e.fields[4].value) <= 1024 and 1 < len(recent) <= 8
    stars = icons["ui_star_old"] + icons["ui_star_new"] + icons["ui_star_empty"]
    assert recent[0] == f"{icons['ui_sword']} A → **1.** X {stars} **100%**"  # attack 20, ours
    assert recent[1].startswith(icons["ui_shield"])  # attack 19, theirs
    assert len(e) <= 6000


def test_clan_games_embed():
    rows = [
        {"player_tag": "#A", "name": "A", "points": 4000},
        {"player_tag": "#B", "name": None, "points": 3600},
    ]
    end = datetime.now(UTC) + timedelta(days=2)
    e = L.clan_games_embed("Clan", rows, end, window_start=end - timedelta(days=6), clan_tag="#C")
    assert e.title == "Clan Games" and e.author.name == "Clan"
    head, board = e.description.split("\n\n")
    assert head.split("\n")[0] == "**7,600** clan points · tier **2**/6"
    assert "**4,400** to tier 3" in head and "Ends <t:" in head
    assert "4,000 ✅" in board and "#B" in board and board.startswith("```")
    soon = L.clan_games_embed("Clan", [], end, window_start=end - timedelta(hours=1))
    assert "Starts <t:" in soon.description and "No points recorded yet." in soon.description


def test_games_summary_lines():
    assert L.games_summary_lines(0)[1].endswith("**3,000** to tier 1")
    assert L.games_summary_lines(60_000) == [
        "**60,000** clan points · tier **6**/6",
        "▲ All 6 reward tiers unlocked",
    ]


def test_fit_table_cuts_rows_to_the_limit():
    rows = [(i, f"Player {i}", 1000) for i in range(50)]
    text = L.fit_table(rows, ["#", "PLAYER", "POINTS"], 500)
    assert len(text) <= 500 and text.endswith("more") and text.count("```") == 2
    assert L.fit_names(["Alice", "Bob", "Carla"], 16) == "Alice and 2 more"
    assert L.fit_names(["Alice", "Bob", "Carl"]) == "Alice, Bob, Carl"
    assert L.fit_names(["Alice", "Bob", "Carl"], 10) == "3 players"


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
        clan_tag="#C",
        badge_url="https://badge/c.png",
    )
    assert e.title == "Last seen" and e.author.icon_url == "https://badge/c.png"
    assert "NAME" in e.description and "A" in e.description


def test_clan_showcase(monkeypatch):
    clan = {
        "clan_tag": "#C",
        "name": "Clan",
        "badge_url": None,
        "level": 10,
        "member_count": 2,
        "war_league": "Gold League I",
        "capital_league": None,
    }
    e = L.clan_showcase_embed(clan, [{"town_hall": 16}, {"town_hall": 16}], "desc")
    assert e.fields[-1].value == "TH16×2"
    assert e.fields[2].value == "Gold League I"
    assert L.clan_showcase_embed(None, [], None).title
    monkeypatch.setattr(EMOJIS, "_by_name", {"th16": "<:th16:1>", "cwl_gold_i": "<:cwl_gold_i:2>"})
    e = L.clan_showcase_embed(clan, [{"town_hall": 16}, {"town_hall": 16}], "desc")
    assert e.fields[-1].value == "<:th16:1>×2"
    assert e.fields[2].value == "<:cwl_gold_i:2> Gold League I"


def test_clan_header_keeps_the_maintenance_notice():
    e = discord.Embed(title="Raid weekend")
    L.clan_header(e, "Clan", "#C", "https://badge/c.png")
    assert e.author.name == "Clan" and e.author.icon_url == "https://badge/c.png"
    e = discord.Embed(title="Raid weekend").set_author(name="API maintenance")
    L.clan_header(e, "Clan", "#C", "https://badge/c.png")
    assert e.author.name == "API maintenance"
    assert e.thumbnail.url == "https://badge/c.png" and e.title == "Clan: Raid weekend"
    e = L.clan_row_header(discord.Embed(title="x"), None, "#C")
    assert e.author.name == "#C" and e.author.icon_url is None


def test_live_line():
    from clashhero.cogs.live import live_line

    assert (
        live_line({"kind": "war_embed", "scope": "#A", "channel_id": 5}, "Main")
        == "⚔️ **Current war** · **Main** (#A) → <#5>"
    )
    assert live_line({"kind": "clan_games", "scope": "#A", "channel_id": 5}) == (
        "🎯 **Clan Games leaderboard** · #A → <#5>"
    )
    assert live_line({"kind": "other", "scope": "", "channel_id": 5}) == "🏰 **other** · this server → <#5>"


async def test_render_reads_database_rows(db):
    from types import SimpleNamespace as NS

    from clashhero.cogs.live import Live

    await db.execute(
        "INSERT INTO clans (clan_tag, name, badge_url, level, member_count, war_league, updated_at) "
        "VALUES ('#C', 'Main', 'https://badge/c.png', 10, 2, 'Gold League I', 'x')"
    )
    await db.execute(
        "INSERT INTO players (player_tag, name, town_hall, clan_tag, updated_at) VALUES "
        "('#A', 'Alice', 16, '#C', 'x'), ('#B', 'Bob', 15, '#C', 'x')"
    )
    war_id = await db.execute(
        "INSERT INTO wars (clan_tag, opponent_tag, opponent_name, war_type, state, team_size, "
        "attacks_per_member, preparation_start, end_time, clan_stars, clan_attacks, opponent_badge_url) "
        "VALUES ('#C', '#O', 'Opp', 'random', 'inWar', 1, 2, '2026-11-06', ?, 3, 1, 'https://badge/o.png')",
        ("2026-11-08T00:00:00+00:00",),
    )
    await db.execute(
        "INSERT INTO war_members (war_id, player_tag, name, town_hall, map_position, is_opponent) VALUES "
        "(?, '#A', 'Alice', 16, 1, 0), (?, '#X', 'Xena', 15, 1, 1)",
        (war_id, war_id),
    )
    await db.execute(
        "INSERT INTO war_attacks (war_id, attack_order, attacker_tag, defender_tag, stars, destruction, "
        "is_fresh, new_stars, defender_pos, is_opponent, seen_at) "
        "VALUES (?, 1, '#A', '#X', 3, 100, 1, 3, 1, 0, 'x')",
        (war_id,),
    )
    live = Live(NS(db=db))
    e = await live.render("war_embed", "#C")
    assert e.title == "War vs Opp" and e.author.name == "Main" and e.thumbnail.url == "https://badge/o.png"
    assert e.fields[0].value == "★ **3** · 0.00%\n⚔️ **1**/2 attacks"
    assert e.fields[-1].value == "⚔️ Alice → **1.** Xena ★★★ **100%**"
    cwl = await live.render("cwl_embed", "#C")
    assert cwl.title == "Not in war" and cwl.author.icon_url == "https://badge/c.png"
    games = await live.render("clan_games", "#C")
    assert games.author.name == "Main" and "clan points" in games.description
    seen = await live.render("last_seen", "#C")
    assert "Alice" in seen.description and seen.author.name == "Main"
    clan = await live.render("clan_embed", "#C", {"description": "Hi"})
    assert clan.fields[-1].value == "TH16×1 TH15×1" and clan.thumbnail.url == "https://badge/c.png"
