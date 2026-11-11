from clashhero.repos import capital as cr


async def seed_raid(db):
    await db.execute(
        "INSERT INTO raid_seasons (clan_tag, start_time, end_time, state, total_loot, attack_count, "
        "districts_destroyed) VALUES ('#C', '2026-11-06T07:00:00+00:00', '2026-11-09T07:00:00+00:00', "
        "'ongoing', 50000, 30, 12)"
    )
    await db.execute(
        "INSERT INTO raid_seasons (clan_tag, start_time, end_time, state) VALUES "
        "('#C', '2026-10-30T07:00:00+00:00', '2026-11-02T07:00:00+00:00', 'ended')"
    )
    await db.executemany(
        "INSERT INTO raid_members (clan_tag, start_time, player_tag, name, attacks, attack_limit, loot) "
        "VALUES ('#C', '2026-11-06T07:00:00+00:00', ?, ?, ?, 6, ?)",
        [("#A", "Alice", 6, 30000), ("#B", "Bob", 2, 20000)],
    )
    await db.executemany(
        "INSERT INTO players (player_tag, name, town_hall, clan_tag, updated_at) VALUES (?,?,?,?, 'x')",
        [("#A", "Alice", 16, "#C"), ("#B", "Bob", 15, "#C"), ("#D", "Dan", 14, "#C")],
    )


async def test_raid_weekends(db):
    await seed_raid(db)
    latest = await cr.raid_weekend(db, "#C")
    assert latest["total_loot"] == 50000
    assert (await cr.raid_weekend(db, "#C", 1))["state"] == "ended"
    assert await cr.raid_weekend(db, "#C", 5) is None
    members = await cr.raid_members(db, "#C", latest["start_time"])
    assert [m["player_tag"] for m in members] == ["#A", "#B"]
    missing = await cr.raid_non_participants(db, "#C", latest["start_time"])
    assert [m["player_tag"] for m in missing] == ["#D"]
    hist = await cr.player_raid_history(db, ["#A"])
    assert hist[0]["loot"] == 30000 and hist[0]["total_loot"] == 50000


async def test_capital_contributions(db):
    await seed_raid(db)
    await db.executemany(
        "INSERT INTO season_stats (season_id, player_tag, clan_tag, capital_gold) VALUES (?,?,?,?)",
        [("S1", "#A", "#C", 500), ("S1", "#B", "#C", 900), ("S1", "#D", "#C", 0), ("S2", "#A", "#C", 7)],
    )
    rows = await cr.capital_contributions(db, "#C", "S1")
    assert [(r["name"], r["capital_gold"]) for r in rows] == [("Bob", 900), ("Alice", 500)]


def test_games_tiers_and_cap():
    assert cr.games_tier(0) == (0, 3000)
    assert cr.games_tier(7500) == (2, 12000)
    assert cr.games_tier(60000) == (6, None)
    s = cr.summarize_games(
        [
            {"player_tag": "#A", "name": "A", "points": 5000},
            {"player_tag": "#B", "name": "B", "points": 1000},
        ],
        cap=4000,
    )
    assert s.total == 5000 and s.tier == 1 and s.next_tier_at == 7500
    assert s.rows[0]["maxed"] and not s.rows[1]["maxed"]


async def test_clan_games_rows(db):
    await seed_raid(db)
    await db.executemany(
        "INSERT INTO clan_games_points (games_id, player_tag, clan_tag, points, updated_at) "
        "VALUES (?, ?, '#C', ?, 'x')",
        [("2026-11", "#A", 1200), ("2026-10", "#B", 4000)],
    )
    rows = await cr.clan_games_rows(db, "#C", "2026-11")
    assert rows == [{"player_tag": "#A", "points": 1200, "name": "Alice"}]
    missing = await cr.clan_games_missing(db, "#C", "2026-11")
    assert [m["player_tag"] for m in missing] == ["#B", "#D"]


# ------------------------------------------------------------------ displays


class FakeResponse:
    def __init__(self):
        self.sent = []

    async def defer(self, **kwargs):
        pass

    async def send_message(self, *args, **kwargs):
        self.sent.append(kwargs)


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, *args, **kwargs):
        self.sent.append(kwargs)


async def add_clan(db):
    await db.execute(
        "INSERT INTO clans (clan_tag, name, badge_url, updated_at) VALUES (?, ?, ?, 'x')",
        ("#C", "Main", "https://badge/c.png"),
    )
    await db.add_guild_clan(1, "#C")  # the server's only clan, so commands pick it by default


def interaction(db, guild_id=None):
    from types import SimpleNamespace as NS

    bot = NS(db=db, coc=None)
    return NS(
        client=bot,
        guild_id=guild_id,
        channel_id=None,
        user=NS(id=1),
        response=FakeResponse(),
        followup=FakeFollowup(),
    ), bot


def test_raid_weekend_text():
    from clashhero.cogs.capital import raid_weekend_text
    from clashhero.utils.fmt import ts

    summary = {
        "start_time": "2026-10-30T07:00:00+00:00",
        "end_time": "2026-11-02T07:00:00+00:00",
        "state": "ended",
        "total_loot": 50000,
        "attack_count": 30,
        "districts_destroyed": 12,
        "offensive_reward": 1200,
        "defensive_reward": 400,
    }
    members = [{"name": "Alice", "attacks": 6, "attack_limit": 6, "loot": 30000}]
    head, board = raid_weekend_text(summary, members).split("\n\n")
    assert head.split("\n") == [
        f"**Ended** · {ts(summary['start_time'], 'd')} – {ts(summary['end_time'], 'd')}",
        "**50,000** Capital Gold looted",
        "**30** attacks · **12** districts destroyed",
        "**1,200** offense medals · **400** defense medals",
    ]
    assert board.startswith("```") and "30,000" in board and "6/6" in board
    ongoing = raid_weekend_text(
        {**summary, "state": "ongoing", "offensive_reward": 0, "defensive_reward": 0}, []
    )
    assert ongoing.startswith("**Ongoing** · ends <t:") and "medals" not in ongoing
    assert ongoing.endswith("Nobody has raided yet.")


def test_raid_weekend_text_icons(monkeypatch):
    from clashhero.cogs.capital import raid_weekend_lines
    from clashhero.services.emojis import EMOJIS

    monkeypatch.setattr(
        EMOJIS, "_by_name", {"res_capital_gold": "<:res_capital_gold:1>", "ui_sword": "<:ui_sword:2>"}
    )
    lines = raid_weekend_lines({"start_time": "2026-10-30", "end_time": "2026-11-02", "state": "ended"})
    assert lines[1] == "<:res_capital_gold:1> **0** Capital Gold looted"
    assert lines[2].startswith("<:ui_sword:2> **0** attacks")


def test_history_text():
    from clashhero.cogs.capital import history_text

    rows = [
        {"start_time": "2026-11-06T07:00", "name": "Alice", "attacks": 6, "attack_limit": 6, "loot": 30000},
        {"start_time": "2026-10-30T07:00", "name": "Alice", "attacks": 5, "attack_limit": 6, "loot": 20000},
    ]
    text = history_text(rows, one_player=True)
    assert text.startswith("**50,000** Capital Gold looted in **2** raid weekends\n**11** attacks")
    assert "WEEKEND" in text and "PLAYER" not in text
    assert "PLAYER" in history_text(rows, one_player=False)
    assert history_text([], one_player=False) == "No raids recorded yet."


async def test_raids_and_history_commands(db):
    from clashhero.cogs.capital import Capital

    await seed_raid(db)
    await add_clan(db)
    inter, bot = interaction(db, guild_id=1)
    await Capital.raids.callback(Capital(bot), inter, None, 0)
    [msg] = inter.followup.sent
    e = msg["embed"]
    assert (
        e.title == "Raid weekend" and e.author.name == "Main" and e.author.icon_url == "https://badge/c.png"
    )
    assert "**50,000** Capital Gold looted" in e.description and "Alice" in e.description
    assert e.fields[0].name == "▼ Didn't raid (1)" and e.fields[0].value == "Dan"

    await db.link_player("#A", 1)  # the caller's only account
    inter, bot = interaction(db)
    await Capital.history.callback(Capital(bot), inter, None, None)
    e = inter.response.sent[0]["embed"]
    assert e.title == "Alice (#A)" and e.url.endswith("tag=A")
    assert e.description.startswith("**30,000** Capital Gold looted in **1** raid weekend\n")


def test_games_embed_text():
    from clashhero.cogs.clangames import games_embed_text, month_label

    s = cr.summarize_games(
        [
            {"player_tag": "#A", "name": "A", "points": 5000},
            {"player_tag": "#B", "name": "B", "points": 1000},
        ],
        cap=4000,
    )
    text = games_embed_text(s, "Ends soon")
    head, board = text.split("\n\n")
    assert head == "**5,000** clan points · tier **1**/6\n▰▰▰▰▰▱▱▱▱▱▱▱ **2,500** to tier 2\nEnds soon"
    assert "5,000 ✅" in board and "1,000 ✅" not in board
    assert month_label("2026-11") == "November 2026" and month_label("soon") == "soon"


async def test_clangames_command(db):
    from clashhero.cogs.clangames import ClanGames

    await seed_raid(db)
    await add_clan(db)
    await db.execute(
        "INSERT INTO clan_games_points (games_id, player_tag, clan_tag, points, updated_at) "
        "VALUES ('2026-10', '#A', '#C', 4000, 'x')"
    )
    inter, bot = interaction(db, guild_id=1)
    await ClanGames.clangames.callback(ClanGames(bot), inter, None, "2026-10", 4000, True)
    e = inter.response.sent[0]["embed"]
    assert e.title == "Clan Games October 2026" and e.author.name == "Main"
    assert e.description.startswith("**4,000** clan points · tier **1**/6\n")
    assert (e.fields[0].name, e.fields[0].value) == ("▼ No points yet (2)", "Bob, Dan")
