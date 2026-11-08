from datetime import UTC, datetime, timedelta

from clashhero.repos import flags as repo

NOW = datetime(2026, 11, 7, tzinfo=UTC)


async def test_create_list_expire_delete(db):
    f1 = await repo.create_flag(db, 1, "#P1", "ban", "cheating", 9, NOW)
    await repo.create_flag(db, 1, "#P2", "strike", None, 9, NOW, expiry_days=1)
    await repo.create_flag(db, 2, "#P1", "strike", None, 9, NOW)
    assert len(await repo.list_flags(db, 1, NOW)) == 2
    later = NOW + timedelta(days=2)
    assert [r["player_tag"] for r in await repo.list_flags(db, 1, later)] == ["#P1"]
    assert len(await repo.list_flags(db, 1, later, include_expired=True)) == 2
    assert [r["flag_type"] for r in await repo.list_flags(db, 1, NOW, flag_type="strike")] == ["strike"]
    assert len(await repo.active_flags(db, 1, "#P1", NOW)) == 1
    assert not await repo.delete_flag(db, 2, f1)  # other guild's id space
    assert await repo.delete_flag(db, 1, f1)
    assert await repo.active_flags(db, 1, "#P1", NOW) == []


async def test_list_by_clan(db):
    await db.execute(
        "INSERT INTO players (player_tag, name, clan_tag, updated_at) VALUES ('#P1','A','#C','x')"
    )
    await repo.create_flag(db, 1, "#P1", "ban", None, 9, NOW)
    await repo.create_flag(db, 1, "#P2", "ban", None, 9, NOW)
    rows = await repo.list_flags(db, 1, NOW, clan_tags=["#C"])
    assert [r["player_tag"] for r in rows] == ["#P1"]
    assert await repo.list_flags(db, 1, NOW, clan_tags=[]) == []
