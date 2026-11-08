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
