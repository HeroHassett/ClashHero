from datetime import UTC, datetime, timedelta

import discord
import pytest

from clashhero import events as ev
from clashhero.cogs import _log_embeds as E
from clashhero.cogs import logs as logs_mod
from clashhero.cogs.logs import Logs, merge_unit_changes, parse_color
from clashhero.services.snapshots import MemberSnap
from clashhero.utils.fmt import player_link
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
STATS = E.JoinStats(
    town_hall=18,
    trophies=4938,
    league="Legend I",
    heroes=(("Barbarian King", 110), ("Archer Queen", 110)),
    war_stars=3221,
    rushed=0.79,
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
    assert [f.name for f in embed.fields] == ["▼ 2 attacks missed", "▼ 1 attack missed"]
    assert embed.fields[0].value == f"**3.** [C]({player_link('#C')})"
    assert "Everyone used all their attacks" in E.missed_attacks_embed(WAR, [], 2).description


CWL_WAR = {
    **WAR,
    "war_type": "cwl",
    "cwl_round": 7,
    "clan_tag": "#2J8VGUCV2",
    "clan_name": "Aurora",
    "clan_badge_url": "https://badge/ours.png",
    "opponent_tag": "#OPP",
    "opponent_name": "VN WAR_PRO",
    "opponent_badge_url": "https://badge/theirs.png",
    "team_size": 15,
    "attacks_per_member": 1,
    "clan_stars": 42,
    "opponent_stars": 39,
    "clan_destruction": 95.333,
    "opponent_destruction": 90.1,
    "clan_attacks": 14,
}


def test_war_attack_post(monkeypatch):
    from clashhero.services.emojis import EMOJIS

    icons = {
        n: f"<:{n}:1>" for n in ("ui_sword", "ui_star_old", "ui_star_new", "ui_star_empty", "ui_war_star")
    }
    monkeypatch.setattr(EMOJIS, "_by_name", {**icons, "th18": "<:th18:1>"})
    attack = {
        "is_opponent": 0,
        "attacker_tag": "#LVL",
        "defender_tag": "#SK",
        "attacker_pos": 15,
        "defender_pos": 13,
        "attacker_th": 18,
        "defender_th": 18,
        "stars": 2,
        "new_stars": 1,
        "is_fresh": 0,
        "destruction": 88.0,
    }
    at = datetime(2026, 11, 11, 6, 37, tzinfo=UTC)
    e = E.war_attack_embed(CWL_WAR, attack, "lvl", "SKING", at=at)
    line, result = e.description.split("\n")
    assert line == (
        f"<:ui_sword:1> **15.** [lvl]({player_link('#LVL')}) <:th18:1>"
        f"  →  **13.** [SKING]({player_link('#SK')}) <:th18:1>"
    )
    assert result == "<:ui_star_old:1><:ui_star_new:1><:ui_star_empty:1> **88%**"  # 1 old, 1 new, 1 missed
    assert (e.author.name, e.author.icon_url) == ("Aurora", "https://badge/ours.png")
    assert [(f.name, f.value) for f in e.fields] == [
        ("Aurora", "<:ui_war_star:1> **42** · 95.33%"),  # ours first, named
        ("VN WAR_PRO", "<:ui_war_star:1> **39** · 90.10%"),
    ]
    assert e.footer.text == "vs VN WAR_PRO · CWL round 7 · 14/15 attacks"
    assert (e.footer.icon_url, e.timestamp, e.color) == ("https://badge/theirs.png", at, E.GREEN)
    fresh = E.war_attack_embed(CWL_WAR, {**attack, "is_fresh": 1, "is_opponent": 1}, "SKING", "lvl")
    assert fresh.description.endswith("· first hit on this base") and fresh.color == E.RED


def test_war_state_posts():
    prep = E.war_state_embed(CWL_WAR, "preparation")
    assert prep.title == "CWL round 7 vs VN WAR_PRO" and prep.url.endswith("tag=OPP")
    assert prep.thumbnail.url == "https://badge/theirs.png" and prep.author.name == "Aurora"
    assert prep.description.startswith("**Preparation day** · 15 vs 15") and not prep.fields
    battle = E.war_state_embed(CWL_WAR, "inWar")
    assert battle.description.startswith("**Battle day**") and len(battle.fields) == 2
    won = E.war_state_embed(CWL_WAR, "warEnded")
    assert "**Victory**" in won.description and won.color == E.GREEN
    lost = E.war_state_embed({**CWL_WAR, "result": "lose"}, "warEnded")
    assert "**Defeat**" in lost.description and lost.color == E.RED


def test_only_fresh_war_days_are_announced():
    started = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)
    war = {**CWL_WAR, "start_time": started.isoformat(), "preparation_start": started.isoformat()}
    assert E.state_is_news(war, "inWar", started + timedelta(minutes=5))
    assert not E.state_is_news(war, "inWar", started + timedelta(hours=22))  # noticed after a restart
    assert not E.state_is_news(war, "preparation", started + timedelta(hours=2))
    assert E.state_is_news(war, "warEnded", started + timedelta(days=3))  # a result is always news
    assert E.state_is_news({**CWL_WAR, "start_time": None}, "inWar", started)


def test_lineup_post():
    e = E.cwl_lineup_embed(
        "2026-11",
        [E.LineupPlayer("Alice", "#A", 16)],
        [E.LineupPlayer("Bob", "#B")],
        clan_name="Aurora",
        clan_tag="#C",
        badge_url="https://badge",
    )
    assert e.author.name == "Aurora" and e.author.icon_url == "https://badge"
    assert [(f.name, f.value) for f in e.fields] == [
        ("▲ Added", f"TH16 [Alice]({player_link('#A')})"),
        ("▼ Removed", f"[Bob]({player_link('#B')})"),
    ]


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
        E.member_join_embed("Clan", M, 40, STATS, badge_url="https://x/b.png"),
        E.member_join_embed("Clan", M, 40, None),
        E.member_leave_embed("Clan", M, 39, 1200, 30),
        E.role_change_embed("Clan", "Alice", "#A", "member", "admin"),
        E.name_change_embed("#A", "Old", "New"),
        E.town_hall_embed("Alice", "#A", 15, 16),
        E.war_preference_embed("Alice", "#A", False),
        E.league_change_embed("Alice", "#A", None, "Legend League", 5000),
        E.donations_embed("Clan", "#C", [E.Donation("Alice", 20, 0), E.Donation("Bob", 0, 15)]),
        E.clan_games_progress_embed("Clan", [E.GamesProgress("Alice", "#A", 200, 1200)]),
        E.war_attack_embed(WAR, attack, "Alice", "Xeno"),
        E.war_attack_embed(
            {**WAR, "war_type": "cwl", "cwl_round": 3}, {**attack, "is_opponent": 1}, "X", "A"
        ),
        E.war_state_embed(WAR, "preparation"),
        E.war_state_embed(WAR, "inWar"),
        E.war_state_embed(WAR, "warEnded"),
        E.cwl_lineup_embed("2026-11", [E.LineupPlayer("Alice", "#A", 16)], []),
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
    assert "Promoted to **Elder**" in E.role_change_embed("C", "A", "#A", "member", "admin").description
    assert "Demoted to **Elder**" in E.role_change_embed("C", "A", "#A", "coLeader", "admin").description


def test_donations_post(monkeypatch):
    from clashhero.services.emojis import EMOJIS

    monkeypatch.setattr(EMOJIS, "_by_name", {f"th{n}": f"<:th{n}:{n}>" for n in (16, 17, 18)})
    at = datetime(2026, 11, 11, 7, 36, tzinfo=UTC)
    entries = [
        E.Donation("H cat", 80, 119, town_hall=17),
        E.Donation("MW Hjale", 154, 119, town_hall=18),
        E.Donation("lvl", 59, 0, town_hall=16, in_clan=False),
        E.Donation("Anurag", 0, 55),
    ]
    e = E.donations_embed(
        "Aurora",
        "#2J8VGUCV2",
        entries,
        numbers={"d": {154: "<:nd_154:1>"}, "r": {}},
        badge_url="https://api-assets.clashofclans.com/badges/200/x.png",
        member_count=42,
        at=at,
    )
    assert e.title == "Aurora (#2J8VGUCV2)" and e.url.endswith("tag=2J8VGUCV2")
    assert e.thumbnail.url == e.footer.icon_url == "https://api-assets.clashofclans.com/badges/200/x.png"
    assert (e.footer.text, e.timestamp) == ("42/50", at)
    donated, received = e.description.split("\n\n")
    assert donated.split("\n") == [
        "▲ **Donated**",
        "<:th18:18> <:nd_154:1> MW Hjale",  # most first; the coloured number when there is one
        "<:th17:17> **80** H cat",
        "<:th16:16> **59** ~~lvl~~",  # gone from the clan
    ]
    assert received.split("\n") == [
        "▼ **Received**",
        "<:th17:17> **119** H cat",  # a tie goes by name
        "<:th18:18> **119** MW Hjale",
        "**55** Anurag",  # no Town Hall known: no icon
    ]
    assert "Received" not in E.donations_embed("C", "#C", [E.Donation("A", 5, 0)]).description
    plain = E.donations_embed("C", "#C", [E.Donation("A", 5, 0)])
    assert plain.footer.text == "C" and plain.thumbnail.url is None


def test_long_donation_lists_are_cut_to_fit():
    many = [E.Donation(f"Player number {i:02d} " + "x" * 40, 100 + i, 100 + i) for i in range(50)]
    e = E.donations_embed("C", "#C", many)
    assert len(e.description) <= 4000 and "more" in e.description
    donated, received = e.description.split("\n\n")
    assert donated.endswith("more") and received.endswith("more")


def test_arrows_are_coloured_emojis_when_uploaded(monkeypatch):
    from clashhero.services.emojis import EMOJIS

    assert E._arrow("up") == "▲" and E._arrow("down") == "▼"
    monkeypatch.setattr(
        EMOJIS, "_by_name", {"ui_arrow_up": "<:ui_arrow_up:1>", "ui_arrow_down": "<:ui_arrow_down:2>"}
    )
    assert E._arrow("up") == "<:ui_arrow_up:1>" and E._arrow("up", "blue") == "▲"
    leave = E.member_leave_embed("Clan", M, 39, 74, 0)
    assert "**74** <:ui_arrow_up:1> **0** <:ui_arrow_down:2>" in leave.description
    assert E.role_change_embed("C", "A", "#A", "admin", "member").description.startswith(
        "<:ui_arrow_down:2> Demoted"
    )


async def test_donations_flush_uses_the_clan_poll(cog, sent, db, monkeypatch):
    from clashhero.services.emojis import EMOJIS
    from clashhero.services.snapshots import ClanSnap

    monkeypatch.setattr(EMOJIS, "_by_name", {"th16": "<:th16:1>", "th12": "<:th12:1>"})
    monkeypatch.setattr(EMOJIS, "loaded", False)  # no uploads from the fake bot

    snap = ClanSnap("#C", "Aurora", 21, None, None, None, members={"#A": M})
    cog.bot.collector = type("Col", (), {"clan_snapshot": lambda self, tag: snap if tag == "#C" else None})()
    await cog.on_clash_donations(ev.DonationsChanged("#C", "Aurora", "#A", "Alice", 20, 5))
    await cog.on_clash_donations(ev.DonationsChanged("#C", "Aurora", "#GONE", "Gone", 7, 0))
    await cog.flush_now()
    assert sent == []  # nobody follows the clan's donations: nothing sent
    await db.set_clan_log(1, "#C", "donations", 100)
    await db.execute(
        "INSERT INTO players (player_tag, name, town_hall, trophies, league, clan_tag, updated_at) "
        "VALUES ('#GONE', 'Gone', 12, 3000, NULL, NULL, 'x')"
    )
    await cog.on_clash_donations(ev.DonationsChanged("#C", "Aurora", "#A", "Alice", 20, 5))
    await cog.on_clash_donations(ev.DonationsChanged("#C", "Aurora", "#GONE", "Gone", 7, 0))
    await cog.flush_now()
    [(_, clan, log_type, embed, _)] = sent
    assert (clan, log_type, embed.title, embed.footer.text) == ("#C", "donations", "Aurora (#C)", "1/50")
    assert embed.description.split("\n")[1:3] == [
        f"<:th{M.town_hall}:1> **20** Alice",  # Town Hall from the clan's poll
        "<:th12:1> **7** ~~Gone~~",  # left: struck through, Town Hall from the stored profile
    ]


def test_role_change_post():
    at = datetime(2026, 11, 11, 7, 23, tzinfo=UTC)
    e = E.role_change_embed(
        "Aurora",
        "Johnny_*",
        "#Q808CLJCJ",
        "member",
        "coLeader",
        town_hall=17,
        trophies=5227,
        league="Legend I",
        badge_url="https://api-assets.clashofclans.com/badges/70/x.png",
        at=at,
    )
    assert e.title == "Johnny\\_\\* (#Q808CLJCJ)" and e.url == player_link("#Q808CLJCJ")
    first, second = e.description.split("\n")
    assert first == "▲ Promoted to **Co-Leader** · was Member"
    assert "**17**" in second and "**5,227**" in second  # Town Hall and trophies
    assert (e.footer.text, e.footer.icon_url, e.timestamp) == (
        "Aurora",
        "https://api-assets.clashofclans.com/badges/70/x.png",
        at,
    )
    assert e.color == E.GREEN
    down = E.role_change_embed("Aurora", "Kim", "#K", "coLeader", "member")
    assert down.description == "▼ Demoted to **Member** · was Co-Leader" and down.color == E.RED
    assert down.footer.icon_url is None and down.timestamp is None  # no badge, no time: left out


async def test_role_changes_post_with_the_player_and_a_profile_button(cog, sent, db):
    await cog.on_clash_role_change(ev.RoleChanged("#C", "Clan", "#A", "Alice", "member", "admin"))
    assert sent == []  # nobody subscribed: nothing looked up or sent
    await db.set_clan_log(1, "#C", "role_change", 100)
    await db.execute(
        "INSERT INTO players (player_tag, name, town_hall, trophies, league, clan_tag, updated_at) "
        "VALUES ('#A', 'Alice', 16, 4900, 'Electro Titan', '#C', 'x')"
    )
    await cog.on_clash_role_change(ev.RoleChanged("#C", "Clan", "#A", "Alice", "member", "admin"))
    [(kind, clan, log_type, embed, view)] = sent
    assert (kind, clan, log_type) == ("clan_log", "#C", "role_change")
    assert embed.title == "Alice (#A)" and "**16**" in embed.description and embed.timestamp is not None
    assert [b.url for b in view.children] == [player_link("#A")]


# ------------------------------------------------------------------ upgrades / boosts / settings


def test_unit_change_text():
    assert E.unit_change_text("Barbarian King", 90, 91) == "Barbarian King 90 → **91**"
    assert E.unit_change_text("Overgrowth Spell", None, 1) == "Overgrowth Spell unlocked"
    assert E.unit_change_text("Electro Owl", None, 3) == "Electro Owl unlocked (level 3)"


def test_merge_unit_changes_keeps_first_old_and_latest_new():
    acc = {}
    merge_unit_changes(acc, [("Hero", "Barbarian King", 90, 91), ("Spell", "Overgrowth Spell", None, 1)])
    merge_unit_changes(acc, [("Hero", "Barbarian King", 91, 92), ("Spell", "Overgrowth Spell", 1, 2)])
    assert acc == {("Hero", "Barbarian King"): [90, 92], ("Spell", "Overgrowth Spell"): [None, 2]}


def test_upgrades_embed_groups_by_player():
    entries = [
        ("Bob", "#B", 15, [("Troop", "Hog Rider", 11, 12)]),
        (
            "Alice",
            "#A",
            16,
            [("Spell", "Overgrowth Spell", None, 1), ("Hero", "Barbarian King", 90, 91)],
        ),
        ("Nobody", "#N", 10, []),
    ]
    [embed] = E.upgrades_embeds("Clan", entries)
    desc = embed.description
    assert embed.title == "Upgrades" and embed.author.name == "Clan"
    # Most upgrades first; heroes before spells inside a player's block.
    assert desc.index("Alice") < desc.index("Bob")
    assert desc.index("Barbarian King 90 → **91**") < desc.index("Overgrowth Spell unlocked")
    assert player_link("#A") in desc
    assert "Nobody" not in desc
    assert embed.footer.text == "3 upgrades by 2 players"
    assert E.upgrades_embeds("Clan", []) == []


def test_upgrades_embeds_split_when_long():
    entries = [
        (f"Player{i}", f"#P{i}", 16, [("Troop", f"Troop {j}", j, j + 1) for j in range(10)])
        for i in range(40)
    ]
    embeds = E.upgrades_embeds("Clan", entries)
    assert len(embeds) > 1
    assert embeds[0].title == f"Upgrades (1/{len(embeds)})"
    joined = "\n".join(e.description for e in embeds)
    for e in embeds:
        assert len(e.description) <= 4000
        assert len(e) <= 6000
    for i in range(40):
        assert f"**[Player{i}]" in joined
    # A player's block isn't split across embeds when it fits.
    for e in embeds:
        for block in e.description.split("\n\n"):
            assert block.startswith("TH16 **[Player")  # Town Hall, then the linked name
            assert block.count("• ") == 10  # units without an icon get a bullet


def test_super_troops_embeds():
    [embed] = E.super_troops_embeds("Clan", [("Bob", "#B", ["Super Archer"]), ("Al", "#A", ["A", "B"])])
    assert embed.description.splitlines() == [
        f"**[Al]({player_link('#A')})** boosted **A**, **B**",
        f"**[Bob]({player_link('#B')})** boosted **Super Archer**",
    ]
    assert embed.title == "Super troop boosts" and embed.author.name == "Clan"
    assert embed.footer.text == "3 boosts"
    assert E.super_troops_embeds("Clan", [("Bob", "#B", [])]) == []


def test_player_posts(monkeypatch):
    from clashhero.services.emojis import EMOJIS

    icons = {"th17": "<:th17:1>", "th18": "<:th18:2>", "ui_arrow_up": "<:up:3>", "ui_arrow_down": "<:down:4>"}
    monkeypatch.setattr(EMOJIS, "_by_name", icons)
    at = datetime(2026, 11, 11, 8, 0, tzinfo=UTC)
    ctx = E.PlayerContext(
        town_hall=17, league="Legend I", trophies=5227, clan_name="Aurora", badge_url="b", at=at
    )
    renamed = E.name_change_embed("#A", "Old_*", "New", ctx)
    assert renamed.title == "New (#A)" and renamed.url == player_link("#A")
    assert renamed.description.startswith("Changed their name from **Old\\_\\***")
    assert "<:th17:1>" in renamed.description  # Town Hall and trophies from the stored profile
    assert (renamed.footer.text, renamed.footer.icon_url, renamed.timestamp) == ("Aurora", "b", at)
    th = E.town_hall_embed("Al", "#A", 17, 18, ctx)
    assert th.description.split("\n")[:2] == [
        "<:up:3> Upgraded to **Town Hall 18** · was 17",
        "<:th17:1> → <:th18:2>",
    ]
    assert th.description.count("<:th17:1>") == 1  # the profile line doesn't repeat the old Town Hall
    assert E.war_preference_embed("Al", "#A", True, ctx).description.startswith("<:up:3> Opted **in**")
    out = E.war_preference_embed("Al", "#A", False)
    assert (
        out.description == "<:down:4> Opted **out** of war" and out.color == E.RED and out.footer.text is None
    )
    up = E.league_change_embed("Al", "#A", "Legend II", "Legend I", 5000, ctx)
    assert up.description.startswith("<:up:3> Promoted to **Legend I**") and up.color == E.GREEN
    assert "was Legend II" in up.description and "**5,000**" in up.description
    down = E.league_change_embed("Al", "#A", "Legend I", "Legend II", 4800)
    assert down.description.startswith("<:down:4> Dropped to") and down.color == E.RED
    assert E.league_change_embed("Al", "#A", None, "Legend I", 5000).description.startswith("Now in")


async def test_player_logs_post_with_context_and_a_profile_button(cog, sent, db):
    await cog.on_clash_name_change(ev.NameChanged("#C", "#A", "Old", "New"))
    assert sent == []  # nobody follows the log
    await db.set_clan_log(1, "#C", "name_change", 100)
    await db.set_clan_log(1, "#C", "town_hall", 100)
    await cog.on_clash_name_change(ev.NameChanged("#C", "#A", "Old", "New"))
    await cog.on_clash_town_hall_upgrade(ev.TownHallUpgraded("#C", "#A", "New", 16, 17))
    await cog.on_clash_war_preference(ev.WarPreferenceChanged("#C", "#A", "New", True))  # not followed
    assert [(c[2], c[3].title, c[4].children[0].url) for c in sent] == [
        ("name_change", "New (#A)", player_link("#A")),
        ("town_hall", "New (#A)", player_link("#A")),
    ]
    assert sent[0][3].footer.text == "#C" and sent[0][3].timestamp is not None


def test_clan_posts(monkeypatch):
    from clashhero.services.emojis import EMOJIS

    icons = {
        "ui_arrow_up": "<:up:1>",
        "ui_arrow_down": "<:down:2>",
        "cwl_crystal_iii": "<:c3:3>",
        "cwl_gold_i": "<:g1:4>",
        "ui_clan_games": "<:cg:5>",
        "th16": "<:th16:6>",
        "res_capital_gold": "<:gold:7>",
        "res_raid_medal": "<:medal:8>",
    }
    monkeypatch.setattr(EMOJIS, "_by_name", icons)
    level = E.clan_attribute_embed("Aurora", "#C", "level", 20, 21, badge_url="b")
    assert level.description == "<:up:1> Clan level: **21**\nwas 20" and level.color == E.GREEN
    assert (level.author.name, level.author.icon_url) == ("Aurora", "b")
    league = E.clan_attribute_embed("Aurora", "#C", "war_league", "Crystal League III", "Gold League I")
    assert (
        league.description
        == "<:down:2> War League: <:g1:4> **Gold League I**\nwas <:c3:3> Crystal League III"
    )
    assert league.color == E.RED

    games = E.clan_games_progress_embed(
        "Aurora",
        [E.GamesProgress("Bo", "#B", 50, 900), E.GamesProgress("Al", "#A", 300, 4000, 16)],
        clan_tag="#C",
    )
    assert games.title == "<:cg:5> Clan Games progress" and games.author.name == "Aurora"
    assert games.description.split("\n") == [
        f"<:th16:6> **+300** [Al]({player_link('#A')}) · 4,000/4,000 <:up:1>",  # maxed
        f"**+50** [Bo]({player_link('#B')}) · 900/4,000",
    ]

    gave = E.capital_contribution_embed("Al", "#A", 12_000)
    assert gave.title == "Al (#A)" and gave.description == "Contributed <:gold:7> **12,000** Capital Gold"
    raid = E.raid_attack_embed("Al", 2, 4000, 6, 6, tag="#A")
    assert raid.title == "Al (#A)" and "**2** attacks · <:gold:7> **4,000** looted" in raid.description
    summary = E.raid_summary_embed(
        "Aurora",
        {
            "total_loot": 1_234_567,
            "attack_count": 150,
            "districts_destroyed": 50,
            "offensive_reward": 1200,
            "defensive_reward": 300,
        },
        [{"name": "Al", "loot": 24_000, "attacks": 6, "attack_limit": 6}],
        clan_tag="#C",
    )
    assert summary.description.split("\n")[0] == "<:gold:7> **1,234,567** Capital Gold looted"
    assert summary.fields[0].value == "**1.** Al · <:gold:7> **24,000** · 6/6"


def test_clan_settings_embed():
    long_desc = "x" * 2000
    embed = E.clan_settings_embed(
        "Clan",
        "#C",
        [
            ("description", "Old *rules*", long_desc),
            ("type", "open", "inviteOnly"),
            ("war_frequency", "always", "oncePerWeek"),
            ("required_town_hall", 12, 14),
            ("required_trophies", 1000, 2400),
            ("public_war_log", True, False),
            ("location", None, "International"),
            ("labels", ["Clan Wars", "Friendly"], ["Clan Wars", "Competitive", "Clan Games"]),
        ],
    )
    lines = embed.description.splitlines()
    assert "**Clan type**: Anyone can join → **Invite only**" in lines
    assert "**War frequency**: Always → **Once a week**" in lines
    assert "**Required Town Hall**: TH12 → **TH14**" in lines
    assert "**Required trophies**: 1,000 → **2,400**" in lines
    assert "**Public war log**: Yes → **No**" in lines
    assert "**Location**: None → **International**" in lines
    assert "**Labels**: added **Clan Games**, **Competitive**; removed **Friendly**" in lines
    old, new = embed.fields
    assert old.name == "Old description" and old.value == "Old \\*rules\\*"
    assert new.name == "New description" and len(new.value) <= 1024 and new.value.endswith("…")
    assert len(embed) <= 6000
    empty = E.clan_settings_embed("Clan", "#C", [("description", "", "Hi")])
    assert empty.fields[0].value == "*(empty)*"


def test_clan_move_embed_and_profile_view():
    ours = E.clan_move_embed("Alice", "#A", "Old Clan", "New Clan", "#NEW", own_clan=True)
    assert ours.title == "Alice (#A)" and ours.url == player_link("#A")
    assert ours.description.startswith("Left **Old Clan** · now in **[New Clan](")
    assert "`#NEW`" in ours.description and "this server's clans" in ours.description
    theirs = E.clan_move_embed("Alice", "#A", "Old Clan", None, "#NEW")
    assert "this server" not in theirs.description and "[\\#NEW]" in theirs.description  # no name: the tag
    [button] = E.profile_view("#A").children
    assert button.label == "Profile"
    assert button.style is discord.ButtonStyle.link
    assert button.url == player_link("#A")


# ------------------------------------------------------------------ cog batching / routing


class FakeBot:
    def __init__(self, db):
        self.db = db


class FakeMessage:
    ids = iter(range(1000, 10**6))

    def __init__(self, calls, channel_id, fail=False):
        self.calls, self.channel_id, self.fail, self.id = calls, channel_id, fail, next(self.ids)

    async def edit(self, *, embed):
        if self.fail:
            raise discord.NotFound(type("R", (), {"status": 404, "reason": "gone"})(), "gone")
        self.calls.append(("edit", self.channel_id, None, embed, None))


@pytest.fixture
async def cog(db):
    return Logs(FakeBot(db))


@pytest.fixture
def sent(monkeypatch):
    calls = []

    async def fake_send_clan_log(bot, clan_tag, log_type, embed, *, content=None, view=None):
        calls.append(("clan_log", clan_tag, log_type, embed, view))
        targets = await bot.db.clan_log_targets(clan_tag, log_type)
        return [(t, FakeMessage(calls, t["channel_id"]), embed.copy()) for t in targets]

    async def fake_send_to_channel(bot, channel_id, *, content=None, embed=None, view=None, **kw):
        calls.append(("channel", channel_id, content, embed, view))

    monkeypatch.setattr(logs_mod, "send_clan_log", fake_send_clan_log)
    monkeypatch.setattr(logs_mod, "send_to_channel", fake_send_to_channel)
    return calls


async def test_upgrades_and_boosts_are_batched_per_clan(cog, sent):
    await cog.on_clash_units_upgraded(
        ev.UnitsUpgraded("#C", "#A", "Alice", 16, (("Hero", "Barbarian King", 90, 91),))
    )
    await cog.on_clash_units_upgraded(
        ev.UnitsUpgraded(
            "#C",
            "#A",
            "Alice",
            16,
            (("Hero", "Barbarian King", 91, 92), ("Spell", "Overgrowth Spell", None, 1)),
        )
    )
    await cog.on_clash_units_upgraded(
        ev.UnitsUpgraded("#C", "#B", "Bob", 15, (("Troop", "Hog Rider", 11, 12),))
    )
    await cog.on_clash_units_upgraded(ev.UnitsUpgraded(None, "#Z", "Zed", 9, (("Troop", "Giant", 1, 2),)))
    await cog.on_clash_super_troops_boosted(ev.SuperTroopsBoosted("#C", "#A", "Alice", ("Super Archer",)))
    await cog.on_clash_super_troops_boosted(ev.SuperTroopsBoosted("#C", "#A", "Alice", ("Super Archer",)))
    await cog.on_clash_super_troops_boosted(ev.SuperTroopsBoosted(None, "#Z", "Zed", ("Super Giant",)))
    assert sent == []

    await cog.flush_now()
    assert [(c[1], c[2]) for c in sent] == [("#C", "upgrades"), ("#C", "super_troops")]
    upgrades = sent[0][3].description
    assert "Barbarian King 90 → **92**" in upgrades
    assert "Overgrowth Spell unlocked" in upgrades
    assert "Hog Rider 11 → **12**" in upgrades
    assert "Zed" not in upgrades
    assert sent[0][3].author.name == "#C"  # clan not in the DB: falls back to the tag
    assert sent[1][3].description == f"**[Alice]({player_link('#A')})** boosted **Super Archer**"

    sent.clear()
    await cog.flush_now()
    assert sent == []


async def test_clan_settings_log(cog, sent, db):
    await db.set_clan_log(1, "#C", "clan_settings", 100)
    await cog.on_clash_clan_settings(ev.ClanSettingsChanged("#C", "Clan", (("type", "open", "closed"),)))
    [(_, tag, log_type, embed, _)] = sent
    assert (tag, log_type) == ("#C", "clan_settings")
    assert "Anyone can join → **Closed**" in embed.description


async def test_join_and_leave_have_profile_button(cog, sent, db):
    await db.set_clan_log(1, "#C", "join_leave", 100)
    m = MemberSnap(
        tag="#A",
        name="Alice",
        role="member",
        town_hall=16,
        trophies=5000,
        league=None,
        donations=0,
        received=0,
    )
    await cog.on_clash_member_join(ev.MemberJoined("#C", "Clan", m, 40))
    await cog.on_clash_member_leave(ev.MemberLeft("#C", "Clan", m, 39))
    assert [c[2] for c in sent] == ["join_leave", "join_leave"]
    for call in sent:
        assert call[4].children[0].url == player_link("#A")
    join, leave = sent[0][3], sent[1][3]
    assert join.footer.text == "Joined Clan [40/50]" and join.timestamp is not None
    assert leave.footer.text == "Left Clan [39/50]"
    assert "?" not in join.description  # no API client: only what the member list has


async def test_no_join_leave_log_means_no_player_fetch(cog, sent):
    class Coc:
        async def get_player(self, tag):
            raise AssertionError("fetched without a log")

    cog.bot.coc = Coc()
    m = MemberSnap(tag="#A", name="A", role="member", town_hall=16, trophies=1, league=None,
                   donations=0, received=0)  # fmt: skip
    await cog.on_clash_member_join(ev.MemberJoined("#C", "Clan", m, 40))
    assert sent == []


async def test_join_uses_the_full_profile(cog, sent, db):
    await db.set_clan_log(1, "#C", "join_leave", 100)

    class Coc:
        async def get_player(self, tag):
            return type("P", (), {
                "_raw_data": {
                    "leagueTier": {"name": "Legend I"},
                    "heroes": [
                        {"name": "Barbarian King", "level": 110, "village": "home"},
                        {"name": "Battle Machine", "level": 35, "village": "builderBase"},
                    ],
                },
                "town_hall": 18, "trophies": 4938, "war_stars": 3221,
            })()  # fmt: skip

    cog.bot.coc = Coc()
    m = MemberSnap(tag="#A", name="Al_ice", role="member", town_hall=18, trophies=4938, league="Legend I",
                   donations=0, received=0)  # fmt: skip
    await cog.on_clash_member_join(ev.MemberJoined("#C", "Clan", m, 30))
    embed = sent[0][3]
    assert embed.title == "Al\\_ice (#A)"
    assert "**110**" in embed.description and "Battle Machine" not in embed.description
    assert "**3,221**" in embed.description


def test_join_and_leave_lines():
    join = E.member_join_embed("Aurora", M, 30, STATS)
    head, more = join.description.split("\n")
    assert "TH **18**" in head and "**4,938**" in head
    assert "Barbarian King **110**" in head  # no emoji uploaded in tests: the name instead
    assert "**3,221**" in more and "**0.79%** rushed" in more
    assert "rushed" not in E.member_join_embed("A", M, 30, E.JoinStats(18, 1, None, (), 0, None)).description
    leave = E.member_leave_embed("Aurora", M, 29, 12, 3, badge_url="https://x/b.png")
    assert leave.description.endswith("**12** ▲ **3** ▼")
    assert leave.footer.icon_url == "https://x/b.png"
    moved = E.with_new_clan(leave, "DifferentClass!", "#2JRYQCJRR")
    assert moved.footer.text == "Left Aurora [29/50]\nJoined DifferentClass! (#2JRYQCJRR)"
    assert moved.footer.icon_url == "https://x/b.png"
    assert "this server" in E.with_new_clan(leave, None, "#N", own_clan=True).footer.text


async def _setup_move_logs(db):
    await db.add_guild_clan(1, "#OLD")
    await db.add_guild_clan(1, "#NEW")
    await db.add_guild_clan(2, "#OLD")
    await db.set_clan_log(1, "#OLD", "join_leave", 100, ping_role_id=7)
    await db.set_clan_log(2, "#OLD", "join_leave", 200, color=0x123456)


async def test_clan_move_follow_up_marks_own_clans(cog, sent, db):
    await _setup_move_logs(db)
    await cog.on_clash_player_clan_changed(ev.PlayerClanChanged("#A", "Alice", "#OLD", "#NEW", "New Clan"))
    assert [(c[0], c[1], c[2]) for c in sent] == [("channel", 100, "<@&7>"), ("channel", 200, None)]
    by_channel = {c[1]: c[3] for c in sent}
    assert "this server's clans" in by_channel[100].description
    assert "this server" not in by_channel[200].description
    assert by_channel[200].color.value == 0x123456
    assert "Left **\\#OLD**" in by_channel[100].description  # clan name unknown: tag
    assert sent[0][4].children[0].label == "Profile"


async def test_clan_move_skips_untracked_or_incomplete(cog, sent, db):
    await _setup_move_logs(db)
    await cog.on_clash_player_clan_changed(ev.PlayerClanChanged("#A", "Alice", "#OLD", None, None))
    await cog.on_clash_player_clan_changed(ev.PlayerClanChanged("#A", "Alice", "#ELSE", "#NEW", "New"))
    await cog.on_clash_player_clan_changed(ev.PlayerClanChanged("#A", "Alice", None, "#NEW", "New"))
    assert sent == []


LEAVER = MemberSnap(
    tag="#A",
    name="Alice",
    role="member",
    town_hall=16,
    trophies=5000,
    league=None,
    donations=0,
    received=0,
)


async def test_leave_post_gets_the_new_clan(cog, sent, db):
    await _setup_move_logs(db)
    await cog.on_clash_member_leave(ev.MemberLeft("#OLD", "Old Clan", LEAVER, 39))
    assert await cog._recent_leavers() == {"#A"}
    sent.clear()

    # Seen clanless first, then in a new clan: both leave posts are edited, nothing new is sent.
    await cog.on_clash_player_clan_changed(ev.PlayerClanChanged("#A", "Alice", "#OLD", None, None))
    await cog.on_clash_player_clan_changed(ev.PlayerClanChanged("#A", "Alice", None, "#NEW", "New Clan"))
    assert [(c[0], c[1]) for c in sent] == [("edit", 100), ("edit", 200)]
    assert sent[0][3].footer.text.endswith("Joined New Clan (#NEW) · one of this server's clans")
    assert sent[1][3].footer.text == "Left Old Clan [39/50]\nJoined New Clan (#NEW)"
    assert await cog._recent_leavers() == set()


async def test_failed_edit_posts_the_move_instead(cog, sent, db):
    await _setup_move_logs(db)
    await cog.on_clash_member_leave(ev.MemberLeft("#OLD", "Old Clan", LEAVER, 39))
    cog._left["#A"].posts[0][1].fail = True
    sent.clear()
    await cog.on_clash_player_clan_changed(ev.PlayerClanChanged("#A", "Alice", "#OLD", "#X", "X"))
    assert [(c[0], c[1]) for c in sent] == [("channel", 100), ("edit", 200)]
    assert "now in" in sent[0][3].description


async def test_move_seen_before_the_leave_goes_into_the_leave_post(cog, sent, db):
    await _setup_move_logs(db)

    class Collector:
        def clan_lists_member(self, clan_tag, player_tag):
            return True  # the old clan's poll hasn't noticed the leave yet

    cog.bot.collector = Collector()
    await cog.on_clash_player_clan_changed(ev.PlayerClanChanged("#A", "Alice", "#OLD", "#X", "X"))
    assert sent == []
    await cog.on_clash_member_leave(ev.MemberLeft("#OLD", "Old Clan", LEAVER, 39))
    assert [c[0] for c in sent] == ["clan_log", "edit", "edit"]
    assert sent[1][3].footer.text.endswith("Joined X (#X)")
    assert await cog._recent_leavers() == set()


async def test_recent_leavers_expire(cog, db):
    old = datetime.now(UTC) - logs_mod.MOVE_WINDOW - timedelta(seconds=1)
    cog._left["#A"] = logs_mod.LeavePost("#OLD", old)
    cog._left["#B"] = logs_mod.LeavePost("#OLD", datetime.now(UTC))
    assert await cog._recent_leavers() == {"#B"}


def test_log_list_shows_each_clan_with_its_badge_and_every_log_type():
    from clashhero.cogs.logs import LogClan, log_list_embeds
    from clashhero.constants import CLAN_LOG_TYPES, SERVER_LOG_TYPES

    clan_rows = [
        {"clan_tag": "#A", "log_type": "donations", "channel_id": 1, "ping_role_id": None},
        {"clan_tag": "#A", "log_type": "role_change", "channel_id": 2, "ping_role_id": 9},
        {"clan_tag": "#A", "log_type": "join_leave", "channel_id": 2, "ping_role_id": None},
        {"clan_tag": "#B", "log_type": "unknown_kind", "channel_id": 3, "ping_role_id": None},
    ]
    server_rows = [{"log_type": "welcome", "channel_id": 4}]
    clans = [LogClan("#A", "Aurora", "https://badge/a.png"), LogClan("#C", "Quiet", None)]
    a, c, b, server = log_list_embeds(clans, clan_rows, server_rows, 0x123456)
    assert (a.author.name, a.author.icon_url, a.author.url) == (
        "Aurora (#A)",
        "https://badge/a.png",
        "https://link.clashofclans.com/?action=OpenClanProfile&tag=A",
    )
    assert a.description == "Posting to <#1>, <#2>"
    fields = {f.name: f.value for f in a.fields}
    assert len(a.fields) == len(CLAN_LOG_TYPES) and all(f.inline for f in a.fields)
    assert (fields["Donation log"], fields["Role change"], fields["War start / end"]) == (
        "<#1>",
        "<#2>\nping <@&9>",
        "—",
    )
    assert "Clan achievements" in fields and "Unit upgrades" in fields  # short names in the grid
    assert c.author.name == "Quiet (#C)" and "No logs set up" in c.description and not c.fields
    assert b.author.name == "#B (#B)" and b.fields[-1].name == "unknown_kind"  # not a linked clan
    assert server.title == "Server logs" and len(server.fields) == len(SERVER_LOG_TYPES)
    assert {f.name: f.value for f in server.fields}["Account linked"] == "<#4>"
    [empty] = log_list_embeds([], [], [])
    assert empty.title == "Server logs" and "No logs configured" in empty.description


def test_log_list_pages_stay_within_discord_limits():
    from clashhero.cogs.logs import LogClan, embed_pages, log_list_embeds
    from clashhero.constants import CLAN_LOG_TYPES

    clans = [LogClan(f"#{i:03d}", f"Clan {i}") for i in range(14)]
    rows = [
        {"clan_tag": c.tag, "log_type": kind, "channel_id": 10**17 + n, "ping_role_id": None}
        for c in clans
        for n, kind in enumerate(CLAN_LOG_TYPES)
    ]
    embeds = log_list_embeds(clans, rows, [])
    pages = embed_pages(embeds)
    assert [e for page in pages for e in page] == embeds and len(pages) > 1
    assert all(len(page) <= 10 and sum(len(e) for e in page) <= 6000 for page in pages)


def test_missing_post_permissions():
    assert logs_mod.missing_post_permissions(discord.Permissions.all()) == []
    perms = discord.Permissions(view_channel=True, send_messages=False, embed_links=False)
    assert logs_mod.missing_post_permissions(perms) == ["Send Messages", "Embed Links"]


def test_check_can_post_refuses_a_channel_the_bot_cant_post_in():
    class Channel:
        mention = "#bots"

        def __init__(self, perms):
            self.perms = perms

        def permissions_for(self, member):
            return self.perms

    logs_mod.check_can_post(Channel(discord.Permissions.all()), object())  # fine
    logs_mod.check_can_post(Channel(discord.Permissions.none()), None)  # no member info: allowed
    with pytest.raises(ResolveError, match="View Channel, Send Messages, Embed Links"):
        logs_mod.check_can_post(Channel(discord.Permissions.none()), object())
