async def test_guild_clans_alias_and_channel(db):
    await db.add_guild_clan(1, "#AAA")
    await db.set_clan_alias(1, "#AAA", "main")
    assert await db.clan_by_alias(1, "MAIN") == "#AAA"
    await db.set_channel_clan(55, 1, "#AAA")
    assert await db.get_channel_clan(55) == "#AAA"
    await db.set_clan_log(1, "#AAA", "donations", 99)
    await db.remove_guild_clan(1, "#AAA")
    assert await db.get_guild_clans(1) == []
    assert await db.get_channel_clan(55) is None
    assert await db.clan_log_targets("#AAA", "donations") == []


async def test_links_default_and_reassign(db):
    await db.link_player("#P1", 10)
    await db.link_player("#P2", 10)
    links = await db.get_links(10)
    assert [(r["player_tag"], r["is_default"]) for r in links] == [("#P1", 1), ("#P2", 0)]
    await db.set_default_account(10, "#P2")
    assert (await db.get_links(10))[0]["player_tag"] == "#P2"
    await db.link_player("#P1", 20, verified=True)
    assert await db.get_linked_tags(10) == ["#P2"]
    assert (await db.get_link("#P1"))["verified"] == 1


async def test_settings(db):
    s = await db.get_guild_settings(1)
    assert s["embed_color"] is None
    await db.set_guild_setting(1, "embed_color", 0xFF0000)
    assert (await db.get_guild_settings(1))["embed_color"] == 0xFF0000


async def test_batch_rolls_back(db):
    try:
        async with db.batch():
            await db.add_guild_clan(1, "#X")
            raise RuntimeError
    except RuntimeError:
        pass
    assert await db.get_guild_clans(1) == []


async def test_live_messages(db):
    await db.upsert_live_message(1, "war_embed", "#C", 5, options={"a": 1})
    await db.set_live_message_id(1, "war_embed", "#C", 777)
    rows = await db.live_messages("war_embed")
    assert rows[0]["message_id"] == 777


async def test_columns_added_to_existing_database(tmp_path):
    import aiosqlite

    from clashhero.db import Database

    path = tmp_path / "old.db"
    async with aiosqlite.connect(path) as conn:  # a database from before the columns existed
        await conn.execute(
            "CREATE TABLE legend_events (id INTEGER PRIMARY KEY, player_tag TEXT, legend_day TEXT, "
            "season_id TEXT, kind TEXT, trophies INTEGER, trophies_after INTEGER, occurred_at TEXT)"
        )
        await conn.commit()
    db = Database(path)
    await db.connect()
    cols = {r[1] for r in await db.fetchall("PRAGMA table_info(legend_events)")}
    assert {"opponent_tag", "army_share_code", "stars"} <= cols
    await db.close()
    db = Database(path)  # second start is a no-op
    await db.connect()
    await db.close()


async def test_stored_opponent_town_halls_raised_once(tmp_path):
    import aiosqlite

    from clashhero.db import Database

    path = tmp_path / "old.db"
    async with aiosqlite.connect(path) as conn:  # battles saved with the log's zero-based level
        await conn.execute(
            "CREATE TABLE legend_events (id INTEGER PRIMARY KEY, player_tag TEXT, legend_day TEXT, "
            "season_id TEXT, kind TEXT, trophies INTEGER, trophies_after INTEGER, occurred_at TEXT, "
            "opponent_th INTEGER)"
        )
        await conn.executemany(
            "INSERT INTO legend_events (id, kind, opponent_th) VALUES (?, 'defense', ?)",
            [(1, 17), (2, 16), (3, 0), (4, None)],
        )
        await conn.commit()
    for _ in range(2):  # the second start leaves them alone
        db = Database(path)
        await db.connect()
        try:
            rows = await db.fetchall("SELECT opponent_th FROM legend_events ORDER BY id")
        finally:
            await db.close()
        assert [r[0] for r in rows] == [18, 17, 0, None]


async def test_fresh_database_keeps_new_battles(tmp_path):
    from clashhero.db import Database

    path = tmp_path / "new.db"
    db = Database(path)
    await db.connect()
    await db.execute(
        "INSERT INTO legend_events (player_tag, legend_day, season_id, kind, trophies, trophies_after, "
        "occurred_at, opponent_th) VALUES ('#A', '2026-11-11', '2026-11-02', 'attack', 40, 5040, "
        "'2026-11-11T12:00:00+00:00', 18)"
    )
    await db.close()
    db = Database(path)
    await db.connect()
    try:
        th = await db.fetchval("SELECT opponent_th FROM legend_events")
    finally:
        await db.close()
    assert th == 18
