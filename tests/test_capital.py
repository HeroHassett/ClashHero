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
