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
    assert embed.title == "Clan upgrades"
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
    assert embeds[0].title == f"Clan upgrades (1/{len(embeds)})"
    joined = "\n".join(e.description for e in embeds)
    for e in embeds:
        assert len(e.description) <= 4000
        assert len(e) <= 6000
    for i in range(40):
        assert f"**[Player{i}]" in joined
    # A player's block isn't split across embeds when it fits.
    for e in embeds:
        for block in e.description.split("\n\n"):
            assert block.startswith("**[Player")
            assert block.count("• ") == 10


def test_super_troops_embeds():
    [embed] = E.super_troops_embeds("Clan", [("Bob", "#B", ["Super Archer"]), ("Al", "#A", ["A", "B"])])
    assert embed.description.splitlines() == [
        "**Al** boosted **A**, **B**",
        "**Bob** boosted **Super Archer**",
    ]
    assert embed.footer.text == "3 boosts"
    assert E.super_troops_embeds("Clan", [("Bob", "#B", [])]) == []


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
    assert "**Alice** (left Old Clan) is now in" in ours.description
    assert "New Clan" in ours.description and "(#NEW)" in ours.description
    assert "this server's clans" in ours.description
    theirs = E.clan_move_embed("Alice", "#A", "Old Clan", None, "#NEW")
    assert "this server" not in theirs.description and "[#NEW]" in theirs.description
    [button] = E.profile_view("#A").children
    assert button.label == "Profile"
    assert button.style is discord.ButtonStyle.link
    assert button.url == player_link("#A")


# ------------------------------------------------------------------ cog batching / routing


class FakeBot:
    def __init__(self, db):
        self.db = db


@pytest.fixture
async def cog(db):
    return Logs(FakeBot(db))


@pytest.fixture
def sent(monkeypatch):
    calls = []

    async def fake_send_clan_log(bot, clan_tag, log_type, embed, *, content=None, view=None):
        calls.append(("clan_log", clan_tag, log_type, embed, view))

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
    assert sent[0][3].title == "#C upgrades"  # clan not in the DB: falls back to the tag
    assert sent[1][3].description == "**Alice** boosted **Super Archer**"

    sent.clear()
    await cog.flush_now()
    assert sent == []


async def test_clan_settings_log(cog, sent):
    await cog.on_clash_clan_settings(ev.ClanSettingsChanged("#C", "Clan", (("type", "open", "closed"),)))
    [(_, tag, log_type, embed, _)] = sent
    assert (tag, log_type) == ("#C", "clan_settings")
    assert "Anyone can join → **Closed**" in embed.description


async def test_join_and_leave_have_profile_button(cog, sent):
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
    assert "(left #OLD)" in by_channel[100].description  # clan name unknown: tag
    assert sent[0][4].children[0].label == "Profile"


async def test_clan_move_skips_untracked_or_incomplete(cog, sent, db):
    await _setup_move_logs(db)
    await cog.on_clash_player_clan_changed(ev.PlayerClanChanged("#A", "Alice", "#OLD", None, None))
    await cog.on_clash_player_clan_changed(ev.PlayerClanChanged("#A", "Alice", "#ELSE", "#NEW", "New"))
    await cog.on_clash_player_clan_changed(ev.PlayerClanChanged("#A", "Alice", None, "#NEW", "New"))
    assert sent == []


async def test_leaver_is_polled_and_clanless_gap_still_reports_move(cog, sent, db):
    await _setup_move_logs(db)
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
    await cog.on_clash_member_leave(ev.MemberLeft("#OLD", "Old Clan", m, 39))
    assert await cog._recent_leavers() == {"#A"}
    sent.clear()

    # Seen clanless first, then in a new clan.
    await cog.on_clash_player_clan_changed(ev.PlayerClanChanged("#A", "Alice", "#OLD", None, None))
    await cog.on_clash_player_clan_changed(ev.PlayerClanChanged("#A", "Alice", None, "#ELSEWHERE", "Far"))
    assert [c[1] for c in sent] == [100, 200]
    assert "(left #OLD)" in sent[0][3].description
    assert await cog._recent_leavers() == set()


async def test_recent_leavers_expire(cog, db):
    cog._left["#A"] = ("#OLD", datetime.now(UTC) - logs_mod.MOVE_WINDOW - timedelta(seconds=1))
    cog._left["#B"] = ("#OLD", datetime.now(UTC))
    assert await cog._recent_leavers() == {"#B"}
