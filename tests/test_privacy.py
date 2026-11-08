from datetime import UTC, datetime, timedelta

from clashhero.services import privacy

NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)


async def seed_guild(db, guild_id):
    await db.add_guild_clan(guild_id, "#C")
    await db.set_clan_log(guild_id, "#C", "donations", 10)
    await db.get_guild_settings(guild_id)
    await db.execute(
        "INSERT INTO rosters (guild_id, clan_tag, name, category) VALUES (?, '#C', 'R', 'cwl')", (guild_id,)
    )
    rid = await db.fetchval("SELECT id FROM rosters WHERE guild_id = ?", (guild_id,))
    await db.execute(
        "INSERT INTO roster_members (roster_id, player_tag, user_id) VALUES (?, '#P', 42)", (rid,)
    )


async def test_purge_guild_removes_everything_for_that_guild_only(db):
    await seed_guild(db, 1)
    await seed_guild(db, 2)
    report = await privacy.purge_guild(db, 1)
    assert report.total > 0
    for table in ("guild_clans", "clan_logs", "guild_settings", "rosters"):
        assert await db.fetchval(f"SELECT COUNT(*) FROM {table} WHERE guild_id = 1") == 0
        assert await db.fetchval(f"SELECT COUNT(*) FROM {table} WHERE guild_id = 2") == 1
    # cascade removed guild 1's roster members, kept guild 2's
    assert await db.fetchval("SELECT COUNT(*) FROM roster_members") == 1


async def test_purge_user(db):
    await db.link_player("#P", 42)
    await db.link_player("#Q", 7)
    await db.execute(
        "INSERT INTO legend_subscriptions (guild_id, user_id, player_tag, created_by) "
        "VALUES (1, 42, '#P', 42)"
    )
    await db.execute(
        "INSERT INTO reminders (guild_id, type, clans, duration_seconds, message, channel_id, created_by) "
        "VALUES (1, 'war', '#C', 3600, 'hi', 5, 42)"
    )
    summary = await privacy.user_data_summary(db, 42)
    assert summary["player_links"] == 1 and summary["legend_subscriptions"] == 1
    await privacy.purge_user(db, 42)
    assert await db.get_linked_tags(42) == []
    assert await db.get_linked_tags(7) == ["#Q"]
    assert await db.fetchval("SELECT COUNT(*) FROM legend_subscriptions") == 0
    # server config they created stays, but anonymised
    assert await db.fetchval("SELECT created_by FROM reminders") == 0
    assert await privacy.user_data_summary(db, 42) == {}


async def test_departures_grace_period(db):
    await seed_guild(db, 1)
    await seed_guild(db, 2)
    missing = await privacy.note_departures(db, {2}, NOW)
    assert missing == {1}
    assert await privacy.purge_departed_guilds(db, NOW + timedelta(days=1)) == []
    assert await privacy.purge_departed_guilds(db, NOW + timedelta(days=8)) == [1]
    assert await db.get_guild_clans(1) == []
    assert await db.get_guild_clans(2) == ["#C"]


async def test_rejoin_cancels_departure(db):
    await seed_guild(db, 1)
    await privacy.note_departures(db, set(), NOW)
    await privacy.note_departures(db, {1}, NOW + timedelta(days=1))
    assert await privacy.purge_departed_guilds(db, NOW + timedelta(days=30)) == []


async def test_retention(db):
    old = (NOW - timedelta(days=500)).isoformat()
    recent = (NOW - timedelta(days=5)).isoformat()
    await db.execute(
        "INSERT INTO activity (player_tag, hour, count) VALUES ('#A', ?, 1), ('#A', ?, 1)",
        ((NOW - timedelta(days=100)).strftime("%Y-%m-%dT%H"), NOW.strftime("%Y-%m-%dT%H")),
    )
    await db.execute(
        "INSERT INTO wars (clan_tag, war_type, state, preparation_start) VALUES ('#C','random','warEnded',?),"
        " ('#C','random','warEnded',?)",
        (old, recent),
    )
    await db.execute(
        "INSERT INTO players (player_tag, name, updated_at) VALUES ('#OLD','x',?), ('#LINKED','y',?)",
        (old, old),
    )
    await db.link_player("#LINKED", 1)
    report = await privacy.purge_old_data(db, NOW)
    assert report.rows["activity"] == 1 and report.rows["wars"] == 1 and report.rows["players"] == 1
    assert await db.fetchval("SELECT COUNT(*) FROM players") == 1
