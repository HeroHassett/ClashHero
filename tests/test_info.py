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


# ------------------------------------------------------------------ displays


def icons(monkeypatch, *names):
    from clashhero.services.emojis import EMOJIS

    monkeypatch.setattr(EMOJIS, "_by_name", {n: f"<:{n}:1>" for n in names})


def sample_clan(**extra):
    from types import SimpleNamespace as NS

    fields = dict(
        name="Aurora", tag="#2J8", level=25, member_count=48, type="inviteOnly",
        war_league=NS(name="Gold League I"), capital_league=NS(name="Gold League II"),
        location=NS(name="United States", country_code="US"), war_wins=120, war_losses=30,
        war_ties=5, war_win_streak=3, public_war_log=True, war_frequency="always",
        required_townhall=13, required_trophies=2000, points=45000, capital_points=3200,
        _raw_data={"labels": [{"name": "Clan Wars"}]},
        members=[NS(town_hall=16), NS(town_hall=17), NS(town_hall=16)],
    )  # fmt: skip
    return NS(**{**fields, **extra})


def test_clan_info_fields(monkeypatch):
    from clashhero.cogs.info import clan_info_fields

    icons(monkeypatch, "cwl_gold_i", "res_trophy", "th13", "ui_arrow_up", "ui_arrow_down", "lbl_clan_wars")
    fields = {name: (value, inline) for name, value, inline in clan_info_fields(sample_clan())}
    assert fields["Members"] == ("**48**/50", True)
    assert fields["War League"][0] == "<:cwl_gold_i:1> Gold League I"
    assert fields["Location"][0] == "🇺🇸 United States"
    assert fields["War Record"][0].splitlines() == [
        "<:ui_arrow_up:1> **120** won",  # green ▲ wins, red ▼ losses
        "<:ui_arrow_down:1> **30** lost",
        "**5** tied",
        "**3** win streak",
    ]
    assert fields["Requirements"][0] == "<:th13:1> **TH13+**\n<:res_trophy:1> **2,000**"
    assert fields["Clan Points"][0] == "<:res_trophy:1> **45,000**"
    assert fields["Labels"] == ("<:lbl_clan_wars:1> Clan Wars", False)
    assert fields["Town Hall Spread"][1] is False and "**2**" in fields["Town Hall Spread"][0]
    assert all(len(value) <= 1024 for value, _ in fields.values())


def test_clan_info_fields_private_log_and_no_icons():
    from clashhero.cogs.info import clan_info_fields

    clan = sample_clan(public_war_log=False, required_townhall=None, location=None, war_league=None)
    fields = {name: value for name, value, _ in clan_info_fields(clan)}
    assert fields["War Record"].splitlines() == ["▲ **120** won", "**3** win streak"]  # losses unknown
    assert fields["Requirements"] == "🏆 **2,000**"
    assert fields["War League"] == "—" and fields["Location"] == "—"


def test_clan_heading_with_badge_and_maintenance():
    import discord

    from clashhero.cogs.info import clan_heading
    from clashhero.utils.fmt import clan_link

    e = clan_heading(discord.Embed(description="body"), "Aurora", "#2J8", "https://badge")
    assert (e.author.name, e.author.url, e.author.icon_url) == (
        "Aurora (#2J8)",
        clan_link("#2J8"),
        "https://badge",
    )
    assert e.description == "body"
    assert clan_heading(discord.Embed(), None, "#2J8", None).author.name == "#2J8"  # untracked clan
    # make_embed's maintenance notice moves from the author line to the top of the description.
    notice = discord.Embed(description="```\ntable\n```").set_author(name="🛠️ maintenance")
    clan_heading(notice, "Aurora", "#2J8", None)
    assert notice.author.name == "Aurora (#2J8)"
    assert notice.description == "-# 🛠️ maintenance\n```\ntable\n```"
    full = discord.Embed(description="x" * 4090).set_author(name="🛠️ maintenance")
    assert clan_heading(full, "Aurora", "#2J8", None).author.name == "🛠️ maintenance"  # no room to move


def test_membership_line_and_donation_totals(monkeypatch):
    from clashhero.cogs.info import donation_totals, membership_line
    from clashhero.utils.fmt import clan_link

    row = {
        "kind": "join",
        "clan_tag": "#C",
        "clan_name": "Au_rora",
        "occurred_at": "2026-11-01T00:00:00+00:00",
    }
    assert membership_line(row) == f"▲ Joined **[Au\\_rora]({clan_link('#C')})** <t:1793491200:f>"
    left = membership_line({**row, "kind": "leave", "clan_name": None})
    assert left.startswith(f"▼ Left **[#C]({clan_link('#C')})**")
    icons(monkeypatch, "ui_arrow_up_blue", "ui_arrow_down")
    assert donation_totals(12345, 678) == (
        "<:ui_arrow_up_blue:1> **12,345** donated · <:ui_arrow_down:1> **678** received"
    )


WAR = {
    "opponent_name": "VN_PRO",
    "opponent_tag": "#OPP",
    "team_size": 15,
    "war_type": "cwl",
    "cwl_round": 3,
    "state": "inWar",
    "clan_stars": 42,
    "opponent_stars": 39,
    "clan_destruction": 95.33,
    "opponent_destruction": 90.1,
    "start_time": "2026-11-10T00:00:00+00:00",
    "end_time": "2026-11-11T00:00:00+00:00",
}


def test_war_block(monkeypatch):
    from clashhero.cogs.summary import war_block
    from clashhero.utils.fmt import clan_link

    icons(monkeypatch, "ui_sword", "ui_war_star", "ui_stopwatch", "ui_arrow_up", "ui_arrow_down")
    assert war_block("Aurora", WAR) == [
        f"**Aurora** vs **[VN\\_PRO]({clan_link('#OPP')})**",
        "<:ui_sword:1> CWL round 3 · 15 vs 15",
        "<:ui_arrow_up:1> <:ui_war_star:1> **42** – **39** · 95.3% – 90.1%",  # ahead: green ▲
        "<:ui_stopwatch:1> Battle day · ends <t:1794355200:R>",
    ]
    behind = war_block("Aurora", {**WAR, "clan_stars": 30})
    assert behind[2].startswith("<:ui_arrow_down:1> ")
    level = war_block("Aurora", {**WAR, "opponent_stars": 42, "opponent_destruction": 95.33})
    assert level[2].startswith("<:ui_war_star:1> ")  # level: no arrow
    prep = war_block("Aurora", {**WAR, "state": "preparation", "war_type": "random", "opponent_tag": None})
    assert prep == [
        "**Aurora** vs **VN\\_PRO**",
        "<:ui_sword:1> War · 15 vs 15",
        "<:ui_stopwatch:1> Preparation · battle day starts <t:1794268800:R>",
    ]
    assert war_block("Au_rora", None) == ["**Au\\_rora** · not in war"]


def test_cwl_and_best_blocks(monkeypatch):
    from clashhero.cogs.summary import best_field, cwl_block, section

    assert cwl_block("Aurora", "Gold League I", "Ended") == ["**Aurora** · Gold League I", "Ended"]
    icons(monkeypatch, "cwl_gold_i", "ui_donate_troops")
    assert cwl_block("Aurora", "Gold League I", "Ended")[0] == "<:cwl_gold_i:1> **Aurora** · Gold League I"
    assert best_field("Donations", [{"name": "A_b", "value": 1234}]) == (
        "<:ui_donate_troops:1> Donations",
        "`  1,234` A\\_b",  # values right-aligned in chips so the column lines up
    )
    assert best_field("Clan Games", []) == ("Clan Games", "—")  # no icon uploaded: the name alone
    assert section("Top donors") == "\n**Top donors**\n"  # a table's code block ends without a newline
