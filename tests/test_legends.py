from datetime import UTC, datetime
from types import SimpleNamespace as NS

from clashhero.cogs import _legend_embeds as E
from clashhero.repos import legends as repo

DAY = "2026-11-07"
SEASON = "2026-11-30"


def ev(tag, kind, trophies, day=DAY):
    return {"player_tag": tag, "kind": kind, "trophies": trophies, "legend_day": day}


async def insert_event(db, tag, kind, trophies, after, day=DAY, at="2026-11-07T10:00:00+00:00"):
    await db.execute(
        "INSERT INTO legend_events (player_tag, legend_day, season_id, kind, trophies, "
        "trophies_after, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (tag, day, SEASON, kind, trophies, after, at),
    )


async def insert_player(db, tag, name, trophies, clan="#C", league="Legend League", th=16):
    await db.execute(
        "INSERT INTO players (player_tag, name, town_hall, trophies, league, clan_tag, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'x')",
        (tag, name, th, trophies, league, clan),
    )


def test_aggregate_day():
    stats = repo.aggregate_day(
        [ev("#A", "attack", 40), ev("#A", "defense", -20), ev("#A", "attack", 32), ev("#B", "defense", -5)]
    )
    a = stats["#A"]
    assert (a.attacks, a.defenses, a.offense, a.defense, a.net) == ([40, 32], [-20], 72, -20, 52)
    assert a.attacks_left == 6 and a.defenses_left == 7
    assert stats["#B"].net == -5


def test_build_season_and_metrics():
    days = [
        {"player_tag": "#A", "legend_day": "2026-11-06", "start_trophies": 5000, "end_trophies": 5040},
        {"player_tag": "#A", "legend_day": DAY, "start_trophies": 5040, "end_trophies": 5050},
    ]
    events = [ev("#A", "attack", 40, "2026-11-06"), ev("#A", "attack", 30), ev("#A", "defense", -20)]
    s = repo.build_season(days, events)
    assert [d.day for d in s.days] == ["2026-11-06", DAY]
    assert s.days[1].stats.net == 10
    assert s.triple_rate == 0.5
    assert s.avg_offense == 35
    assert s.avg_defense_per_day == -20
    rows = E.season_rows(s)
    assert rows[0] == ["11-06", 5000, 5040, "+40", "+0", "1/0"]


def test_sort_board():
    d = repo.DayStats
    entries = [
        repo.BoardEntry("#A", "amy", 16, 5100, d("#A", [40])),
        repo.BoardEntry("#B", "bob", 16, 5200, d("#B")),
        repo.BoardEntry("#C", "cat", 16, 5100, d("#C", [10])),
    ]
    assert [e.player_tag for e in repo.sort_board(entries)] == ["#B", "#A", "#C"]


def test_alert_embed():
    stats = repo.DayStats("#A", [40, 25], [-10])
    e = E.alert_embed(
        name="Amy",
        tag="#A",
        kind="attack",
        trophies=25,
        trophies_after=5055,
        stats=stats,
        occurred_at=datetime(2026, 11, 7, tzinfo=UTC),
    )
    assert "Attack" in e.title and "+25" in e.description and "5055" in e.description
    fields = {f.name: f.value for f in e.fields}
    assert fields["Attacks"] == "2/8 (+65)"
    assert fields["Defenses"] == "1/8 (-10)"
    assert fields["Net today"] == "+55"
    d = E.alert_embed(name="Amy", tag="#A", kind="defense", trophies=-10, trophies_after=5045, stats=stats)
    assert "Defense" in d.title


def test_top_players_embed_highlight():
    players = [NS(tag="#A", rank=1, name="Amy", trophies=6000, clan=None)]
    e = E.top_players_embed(title="t", players=players, page=0, pages=1, highlight={"#A"})
    assert "★1" in e.description


async def test_subscriptions(db):
    sid = await repo.add_subscription(db, guild_id=1, user_id=10, player_tag="#A", created_by=10)
    again = await repo.add_subscription(
        db, guild_id=1, user_id=10, player_tag="#A", attacks=False, created_by=10
    )
    assert sid == again
    assert (await repo.get_subscription(db, sid))["attacks"] == 0
    await repo.add_subscription(
        db, guild_id=1, user_id=11, player_tag="#A", channel_id=5, ping_role_id=7, created_by=11
    )
    assert len(await repo.subscriptions_for_player(db, "#A")) == 2
    assert len(await repo.list_subscriptions(db, user_id=10)) == 1
    assert len(await repo.list_subscriptions(db, guild_id=1)) == 2
    await repo.update_subscription(db, sid, attacks=1, bogus=3)
    assert (await repo.get_subscription(db, sid))["attacks"] == 1
    await repo.delete_subscription(db, sid)
    assert len(await repo.subscriptions_for_player(db, "#A")) == 1
    assert await repo.all_tracked_tags(db) == {"#A"}


async def test_board_limit_and_tags(db, monkeypatch):
    monkeypatch.setattr(repo, "BOARD_LIMIT", 2)
    assert await repo.board_add(db, 1, ["#A", "#B", "#C"]) == 2
    assert await repo.board_tags(db, 1) == ["#A", "#B"]
    assert await repo.board_remove(db, 1, "#A")
    assert not await repo.board_remove(db, 1, "#A")
    assert await repo.all_tracked_tags(db) == {"#B"}


async def test_board_entries_and_clan_day(db):
    await insert_player(db, "#A", "Amy", 5030)
    await insert_player(db, "#B", "Bob", 5100)
    await insert_player(db, "#Z", "Zed", 3000, league="Crystal League I")
    await insert_event(db, "#A", "attack", 40, 5040)
    await insert_event(db, "#A", "defense", -10, 5030, at="2026-11-07T11:00:00+00:00")
    for tag, start, end in (("#A", 5000, 5030), ("#B", 5100, 5100)):
        await db.execute(
            "INSERT INTO legend_days (player_tag, legend_day, season_id, start_trophies, end_trophies) "
            "VALUES (?, ?, ?, ?, ?)",
            (tag, DAY, SEASON, start, end),
        )
    assert sorted(await repo.legend_members(db, ["#C"])) == ["#A", "#B"]
    entries = await repo.board_entries(db, ["#A", "#B"], DAY)
    assert [e.player_tag for e in entries] == ["#B", "#A"]
    assert entries[1].stats.net == 30
    rows = await repo.clan_day_rows(db, "#C", DAY)
    assert [(r["player_tag"], r["offense"], r["defense"]) for r in rows] == [("#B", 0, 0), ("#A", 40, -10)]
    e = E.clan_day_embed(clan_name="Clan", clan_tag="#C", day=DAY, rows=rows)
    assert "Amy" in e.description
    summary = await repo.season_summary(db, "#A", SEASON)
    assert summary.days[0].start == 5000
    hist = await repo.player_seasons(db, "#A")
    assert (hist[0]["final_trophies"], hist[0]["attacks"]) == (5030, 1)
    assert await repo.seasons_for_player(db, "#A") == [SEASON]


async def test_top_today_and_daily_posted(db):
    await insert_player(db, "#A", "Amy", 5030)
    await insert_player(db, "#B", "Bob", 5100)
    await insert_event(db, "#A", "attack", 40, 5040)
    await insert_event(db, "#B", "attack", 20, 5120)
    await insert_event(db, "#B", "attack", 30, 5150)
    await insert_event(db, "#A", "defense", -30, 5010)
    await insert_event(db, "#B", "defense", -5, 5145)
    off = await repo.top_today(db, DAY, "offense")
    assert [r["player_tag"] for r in off] == ["#B", "#A"]
    assert [r["player_tag"] for r in await repo.top_today(db, DAY, "offense", min_battles=2)] == ["#B"]
    dfn = await repo.top_today(db, DAY, "defense")
    assert [r["player_tag"] for r in dfn] == ["#B", "#A"]
    assert not await repo.daily_posted(db, "#C", DAY)
    await repo.mark_daily_posted(db, "#C", DAY)
    await repo.mark_daily_posted(db, "#C", DAY)
    assert await repo.daily_posted(db, "#C", DAY)
    await db.set_clan_log(1, "#C", "legend_daily", 9)
    assert await repo.clans_with_daily_log(db) == ["#C"]


def test_alert_line():
    from clashhero.cogs.legends import alert_line

    sub = {"id": 12, "name": "Amy", "player_tag": "#P", "attacks": 1, "defenses": 0}
    assert alert_line(sub, "DM") == "`#12` **Amy** `#P` → DM · ⚔️ on · 🛡️ off"
    assert alert_line({**sub, "name": None}, "<#5>").startswith("`#12` **?** `#P` → <#5>")
