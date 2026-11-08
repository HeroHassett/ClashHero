from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import discord
import pytest

from clashhero.cogs import autoboards as cog
from clashhero.repos import autoboards as repo
from clashhero.repos.autoboards import Schedule
from clashhero.utils.seasons import game_season, legend_day_bounds

ANCHOR = datetime(2026, 11, 2, 5, tzinfo=UTC)  # a Monday season start


def at(day: int, hour: int = 0, minute: int = 0, second: int = 0, month: int = 11) -> datetime:
    return datetime(2026, month, day, hour, minute, second, tzinfo=UTC)


# ------------------------------------------------------------------ pure schedule logic


def test_parse_hhmm():
    assert repo.parse_hhmm("18:30") == (18, 30)
    assert repo.parse_hhmm(" 9:05 ") == (9, 5)
    assert repo.parse_hhmm("0930") == (9, 30)
    assert repo.parse_hhmm("7") == (7, 0)
    assert repo.parse_hhmm("23:59 UTC") == (23, 59)
    for bad in ("24:00", "12:60", "ab", "", "1:2:3", "-1:00", "12345"):
        with pytest.raises(ValueError):
            repo.parse_hhmm(bad)


def test_schedule_validation_and_describe():
    for args in (("daily",), ("weekly", 1, 0), ("live", None, None, None, 5), ("hourly",)):
        with pytest.raises(ValueError):
            Schedule(*args)
    assert Schedule("daily", 18, 0).describe() == "Daily at 18:00 UTC"
    assert Schedule("weekly", 9, 5, 6).describe() == "Sundays at 09:05 UTC"
    assert Schedule("live", interval_minutes=15).describe() == "Live, every 15 min"
    assert Schedule("season_end").describe() == "At season end"


def test_slots_daily_weekly():
    daily = Schedule("daily", 18, 0)
    assert repo.previous_slot(daily, at(7, 17, 59)) == at(6, 18)
    assert repo.previous_slot(daily, at(7, 18)) == at(7, 18)
    assert repo.next_slot(daily, at(7, 18)) == at(8, 18)
    monday = Schedule("weekly", 12, 30, 0)  # 2026-11-07 is a Saturday
    assert repo.previous_slot(monday, at(7, 10)) == at(2, 12, 30)
    assert repo.next_slot(monday, at(7, 10)) == at(9, 12, 30)
    assert repo.previous_slot(monday, at(9, 12, 29)) == at(2, 12, 30)
    assert repo.previous_slot(monday, at(9, 12, 30)) == at(9, 12, 30)


def test_slots_season_and_legend_reset():
    season = Schedule("season_end")
    assert repo.previous_slot(season, at(7), ANCHOR) == ANCHOR
    assert repo.next_slot(season, at(7), ANCHOR) == at(30, 5)
    reset = Schedule("legend_reset")
    assert repo.previous_slot(reset, at(7, 4, 59)) == at(6, 5)
    assert repo.next_slot(reset, at(7, 4, 59)) == at(7, 5)
    assert repo.previous_slot(reset, at(7, 5)) == at(7, 5)


def test_daily_due_once_with_grace():
    s = Schedule("daily", 18, 0)
    last = repo.initial_last_run(s, at(7, 10))
    assert last == at(6, 18)  # today's 18:00 is the first post
    assert repo.due_slot(s, at(7, 17, 59), last) is None
    assert repo.due_slot(s, at(7, 18), last) == at(7, 18)
    assert repo.due_slot(s, at(7, 19, 59), last) == at(7, 18)  # late but within grace
    assert repo.due_slot(s, at(7, 20, 1), last) is None  # missed while offline
    assert repo.due_slot(s, at(7, 18, 1), at(7, 18)) is None  # already posted
    assert repo.due_slot(s, at(8, 18), at(7, 18)) == at(8, 18)
    # Created right after the slot: wait for tomorrow.
    assert repo.initial_last_run(s, at(7, 18, 0, 30)) == at(7, 18)


def test_season_end_waits_for_final_polls():
    s = Schedule("season_end")
    last = repo.initial_last_run(s, at(7), ANCHOR)
    assert last == ANCHOR
    assert repo.due_slot(s, at(30, 5, 2), last, ANCHOR) is None
    slot = repo.due_slot(s, at(30, 5, 5), last, ANCHOR)
    assert slot == at(30, 5)
    assert repo.due_slot(s, at(30, 20), last, ANCHOR) == at(30, 5)  # 24h grace
    assert repo.due_slot(s, at(1, 6, month=12), last, ANCHOR) is None
    # The post describes the season that just ended.
    ref = repo.reference_time(s, slot, at(30, 5, 5))
    assert ref < at(30, 5) and game_season(ref, ANCHOR).id == "2026-11-02"
    assert repo.next_post_time(s, at(7), last, ANCHOR) == at(30, 5, 5)


def test_legend_reset_created_just_after_reset_still_posts():
    s = Schedule("legend_reset")
    last = repo.initial_last_run(s, at(7, 5, 2))  # within the settle window
    assert last == at(6, 5)
    assert repo.due_slot(s, at(7, 5, 4), last) is None
    assert repo.due_slot(s, at(7, 5, 5), last) == at(7, 5)
    ref = repo.reference_time(s, at(7, 5), at(7, 5, 5))
    assert legend_day_bounds(ref)[0] == at(6, 5)


def test_live_interval():
    s = Schedule("live", interval_minutes=10)
    now = at(7, 12)
    assert repo.initial_last_run(s, now) is None
    assert repo.due_slot(s, now, None) == now
    assert repo.due_slot(s, now + timedelta(minutes=9), now) is None
    assert repo.due_slot(s, now + timedelta(minutes=9, seconds=31), now) is not None  # loop drift
    assert repo.due_slot(s, now + timedelta(minutes=10), now) is not None
    assert repo.next_post_time(s, now, now) == now + timedelta(minutes=10)
    assert repo.next_post_time(s, now, None) == now
    assert repo.reference_time(s, now, now) == now


def test_iso_roundtrip():
    assert repo.from_iso(repo.to_iso(at(7, 1, 2, 3))) == at(7, 1, 2, 3)
    assert repo.from_iso(None) is None
    assert repo.to_iso(datetime(2026, 11, 7, 1, 2, 3, 999)) == "2026-11-07T01:02:03+00:00"


# ------------------------------------------------------------------ storage


async def test_board_crud_and_claim(db):
    now = at(7, 10)
    s = Schedule("daily", 18, 0)
    b1 = await repo.create_board(db, 1, "donations", ["#A", "#B"], 100, s, 9, now)
    b2 = await repo.create_board(db, 1, "trophies", None, 100, Schedule("live", interval_minutes=10), 9, now)
    await repo.create_board(db, 2, "activity", None, 200, s, 9, now)
    with pytest.raises(ValueError):
        await repo.create_board(db, 1, "nope", None, 100, s, 9, now)
    assert await repo.count_boards(db, 1) == 2
    row = await repo.get_board(db, 1, b1)
    assert repo.board_clans(row) == ["#A", "#B"]
    assert repo.Schedule.from_row(row) == s
    assert repo.from_iso(row["last_run"]) == at(6, 18)
    live = await repo.get_board(db, 1, b2)
    assert live["last_run"] is None and repo.board_clans(live) == []
    assert await repo.get_board(db, 2, b1) is None
    # Compare-and-swap: only the first claim of a slot wins.
    assert await repo.claim_run(db, b1, row["last_run"], at(7, 18))
    assert not await repo.claim_run(db, b1, row["last_run"], at(7, 18))
    assert await repo.claim_run(db, b2, None, now)
    assert not await repo.claim_run(db, b2, None, now)
    await repo.set_message_id(db, b2, 555)
    assert (await repo.get_board(db, 1, b2))["message_id"] == 555
    assert not await repo.delete_board(db, 2, b1)
    assert await repo.delete_board(db, 1, b1)
    assert [r["id"] for r in await repo.guild_boards(db, 1)] == [b2]


# ------------------------------------------------------------------ rendering


async def _seed(db, season_id: str):
    await db.add_guild_clan(1, "#C1")
    await db.add_guild_clan(1, "#C2")
    await db.execute(
        "INSERT INTO clans (clan_tag, name, updated_at) VALUES ('#C1','Alpha','x'), ('#C2','Beta','x')"
    )
    await db.execute(
        "INSERT INTO players (player_tag, name, town_hall, trophies, league, clan_tag, updated_at) VALUES "
        "('#P1','Ann',16,5300,'Legend I','#C1','x'), ('#P2','Bob',15,4100,'Unranked','#C2','x')"
    )
    await db.execute(
        "INSERT INTO season_stats (season_id, player_tag, clan_tag, donations, received, capital_gold, "
        "activity) VALUES (?, '#P1', '#C1', 1200, 300, 5000, 40), (?, '#P2', '#C2', 800, 100, 0, 12)",
        (season_id, season_id),
    )


async def test_render_every_board_type(db):
    now = datetime.now(UTC)
    season = game_season(now)
    await _seed(db, season.id)
    names = {"#C1": "Alpha", "#C2": "Beta"}
    tags = ["#C1", "#C2"]
    assert set(cog.RENDERERS) == set(repo.BOARD_TYPES)
    for board_type in repo.BOARD_TYPES:
        body = await cog.render_body(db, board_type, tags, names, now)
        assert body and len(body) <= 4096, board_type
    donations = await cog.render_body(db, "donations", tags, names, now)
    assert "Ann" in donations and "1,200" in donations and "Alpha" in donations
    single = await cog.render_body(db, "donations", ["#C1"], names, now)
    assert "Bob" not in single and "CLAN" not in single
    assert "Ann" in await cog.render_body(db, "capital", tags, names, now)
    assert "5300" in await cog.render_body(db, "trophies", tags, names, now)
    legends = await cog.render_body(db, "legends", tags, names, now)
    assert "Ann" in legends and "Bob" not in legends  # only ranked-tracked players
    assert "No war attacks" in await cog.render_body(db, "war_stats", tags, names, now)


async def test_render_war_boards(db):
    now = datetime.now(UTC)
    season = game_season(now)
    await _seed(db, season.id)
    start = (season.start + timedelta(hours=1)).isoformat()
    war_id = await db.execute(
        "INSERT INTO wars (clan_tag, opponent_name, war_type, state, team_size, attacks_per_member, "
        "preparation_start, clan_stars, opponent_stars, clan_destruction, opponent_destruction, result) "
        "VALUES ('#C1', 'Rivals', 'random', 'warEnded', 2, 2, ?, 5, 3, 90.0, 60.0, 'win')",
        (start,),
    )
    await db.executemany(
        "INSERT INTO war_members (war_id, player_tag, name, town_hall, map_position, is_opponent) "
        "VALUES (?, ?, ?, 16, ?, ?)",
        [(war_id, "#P1", "Ann", 1, 0), (war_id, "#P2", "Bob", 2, 0), (war_id, "#X1", "Opp", 1, 1)],
    )
    await db.executemany(
        "INSERT INTO war_attacks (war_id, attack_order, attacker_tag, defender_tag, stars, destruction, "
        "is_fresh, is_opponent, seen_at) VALUES (?, ?, ?, '#X1', ?, ?, 1, 0, 'x')",
        [(war_id, 1, "#P1", 3, 100.0), (war_id, 2, "#P1", 2, 80.0), (war_id, 3, "#P2", 3, 100.0)],
    )
    names = {"#C1": "Alpha", "#C2": "Beta"}
    stats_body = await cog.render_body(db, "war_stats", ["#C1"], names, now)
    assert "67%" in stats_body and "2/3 triples" in stats_body and "1 missed" in stats_body
    assert stats_body.index("Bob") < stats_body.index("Ann")  # 100% before 50%
    results = await cog.render_body(db, "war_results", ["#C1"], names, now)
    assert "Rivals" in results and "✅" in results and "100%" in results


# ------------------------------------------------------------------ posting


class _Message:
    def __init__(self, channel, mid):
        self.channel, self.id = channel, mid

    async def edit(self, **kw):
        if self.id in self.channel.deleted:
            raise discord.NotFound(NS(status=404, reason="Not Found"), "Unknown Message")
        self.channel.edits.append((self.id, kw))

    async def delete(self):
        self.channel.deleted.add(self.id)


class _Channel:
    def __init__(self):
        self.sent, self.edits, self.deleted = [], [], set()

    async def send(self, **kw):
        self.sent.append(kw)
        return _Message(self, 1000 + len(self.sent))

    def get_partial_message(self, mid):
        return _Message(self, mid)


class _Bot:
    def __init__(self, db, channels, forbidden=()):
        self.db, self.channels, self.forbidden = db, channels, set(forbidden)

    def get_channel(self, cid):
        return self.channels.get(cid)

    async def fetch_channel(self, cid):
        if cid in self.forbidden:
            raise discord.Forbidden(NS(status=403, reason="Forbidden"), "Missing Access")
        raise discord.NotFound(NS(status=404, reason="Not Found"), "Unknown Channel")


async def test_run_fixed_board_posts_once(db):
    await _seed(db, game_season(at(7)).id)
    ch = _Channel()
    boards = cog.Autoboards(_Bot(db, {100: ch}))
    bid = await repo.create_board(db, 1, "donations", None, 100, Schedule("daily", 18, 0), 9, at(7, 10))
    stale = await repo.get_board(db, 1, bid)
    assert await boards.run_board(stale, at(7, 17)) is None
    assert await boards.run_board(stale, at(7, 18)) == "ok"
    assert await boards.run_board(stale, at(7, 18, 1)) is None  # stale row: claim fails
    fresh = await repo.get_board(db, 1, bid)
    assert await boards.run_board(fresh, at(7, 18, 1)) is None
    assert len(ch.sent) == 1
    embed = ch.sent[0]["embed"]
    assert embed.title == repo.BOARD_TYPES["donations"] and "Alpha, Beta" in embed.footer.text


async def test_run_live_board_edits_and_reposts(db):
    await _seed(db, game_season(at(7)).id)
    ch = _Channel()
    boards = cog.Autoboards(_Bot(db, {100: ch}))
    s = Schedule("live", interval_minutes=10)
    bid = await repo.create_board(db, 1, "trophies", ["#C1"], 100, s, 9, at(7, 12))
    assert await boards.run_board(await repo.get_board(db, 1, bid), at(7, 12)) == "ok"
    row = await repo.get_board(db, 1, bid)
    assert row["message_id"] == 1001 and len(ch.sent) == 1
    assert "Beta" not in ch.sent[0]["embed"].footer.text
    assert await boards.run_board(row, at(7, 12, 5)) is None
    assert await boards.run_board(row, at(7, 12, 10)) == "ok"
    assert ch.edits and ch.edits[0][0] == 1001 and len(ch.sent) == 1
    ch.deleted.add(1001)  # someone deleted the live message: repost it
    row = await repo.get_board(db, 1, bid)
    assert await boards.run_board(row, at(7, 12, 20)) == "ok"
    assert len(ch.sent) == 2 and (await repo.get_board(db, 1, bid))["message_id"] == 1002


async def test_missing_and_hidden_channels(db):
    await _seed(db, game_season(at(7)).id)
    boards = cog.Autoboards(_Bot(db, {}, forbidden={300}))
    live = Schedule("live", interval_minutes=10)
    gone = await repo.create_board(db, 1, "trophies", None, 200, live, 9, at(7))
    hidden = await repo.create_board(db, 1, "trophies", None, 300, live, 9, at(7))
    assert await boards.run_board(await repo.get_board(db, 1, gone), at(7)) == "gone"
    assert await repo.get_board(db, 1, gone) is None
    assert await boards.run_board(await repo.get_board(db, 1, hidden), at(7)) == "failed"
    assert await repo.get_board(db, 1, hidden) is not None


async def test_removed_clans_are_skipped(db):
    await _seed(db, game_season(at(7)).id)
    ch = _Channel()
    boards = cog.Autoboards(_Bot(db, {100: ch}))
    live = Schedule("live", interval_minutes=10)
    bid = await repo.create_board(db, 1, "trophies", ["#C2", "#GONE"], 100, live, 9, at(7))
    assert await boards._clan_tags(await repo.get_board(db, 1, bid)) == ["#C2"]
    await db.remove_guild_clan(1, "#C2")
    assert await boards.run_board(await repo.get_board(db, 1, bid), at(7)) == "failed"
    assert ch.sent == []
