from datetime import UTC, datetime, timedelta

from clashhero.cogs.todo import account_field, item_line, ping_lines
from clashhero.repos import todo as T

NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)  # Saturday: raid weekend on, Clan Games off
GAMES_NOW = datetime(2026, 11, 24, 12, tzinfo=UTC)  # Clan Games running, war/raid above are over
WAR_END = "2026-11-08T00:00:00+00:00"


async def seed(db):
    await db.execute(
        "INSERT INTO clans (clan_tag, name, updated_at) VALUES ('#C', 'Main', 'x'), ('#C2', 'Feeder', 'x')"
    )
    await db.executemany(
        "INSERT INTO players (player_tag, name, town_hall, league, clan_tag, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 'x')",
        [
            ("#A", "Alice", 16, "Legend I", "#C"),
            ("#B", "Bob", 15, "Legend II", "#C"),
            ("#D", "Dan", 14, "Electro League 32", "#C2"),
            ("#E", "Eve", 10, None, None),
        ],
    )

    async def war(clan, type_, per, end, members, prep):
        wid = await db.execute(
            "INSERT INTO wars (clan_tag, war_type, state, preparation_start, attacks_per_member, "
            "end_time) VALUES (?, ?, 'inWar', ?, ?, ?)",
            (clan, type_, prep, per, end),
        )
        order = 0
        for tag, opp, used in members:
            await db.execute(
                "INSERT INTO war_members (war_id, player_tag, name, town_hall, map_position, is_opponent) "
                "VALUES (?, ?, ?, 15, 1, ?)",
                (wid, tag, tag, opp),
            )
            for _ in range(used):
                order += 1
                await db.execute(
                    "INSERT INTO war_attacks (war_id, attack_order, attacker_tag, defender_tag, stars, "
                    "destruction, is_fresh, is_opponent, seen_at) VALUES (?, ?, ?, '#X', 3, 100, 1, ?, 'x')",
                    (wid, order, tag, opp),
                )

    await war("#C", "random", 2, WAR_END, [("#A", 0, 1), ("#B", 0, 2), ("#X", 1, 0)], "2026-11-06")
    await war("#C2", "cwl", 1, WAR_END, [("#D", 0, 0)], "2026-11-06")
    await war("#C3", "random", 2, "2026-11-01T00:00:00+00:00", [("#E", 0, 0)], "2026-10-30")  # stale
    await war("#C4", "random", 2, WAR_END, [("#A", 1, 0)], "2026-11-06")  # Alice as an opponent

    async def battles(tag, day, n):
        for _ in range(n):
            await db.execute(
                "INSERT INTO legend_events (player_tag, legend_day, season_id, kind, trophies, "
                "trophies_after, occurred_at) VALUES (?, ?, 's', 'attack', 30, 5000, 'x')",
                (tag, day),
            )

    await battles("#A", "2026-11-07", 3)
    await battles("#A", "2026-11-06", 2)
    await battles("#B", "2026-11-03", 20)
    await battles("#B", "2026-11-07", 8)
    await battles("#B", "2026-11-01", 5)  # last week
    await battles("#D", "2026-11-07", 4)
    await db.execute(
        "INSERT INTO legend_events (player_tag, legend_day, season_id, kind, trophies, trophies_after, "
        "occurred_at) VALUES ('#A', '2026-11-07', 's', 'defense', -20, 5000, 'x')"
    )

    for clan in ("#C", "#C2"):
        await db.execute(
            "INSERT INTO raid_seasons (clan_tag, start_time, end_time, state) "
            "VALUES (?, 's1', '2026-11-09T07:00:00+00:00', 'ongoing')",
            (clan,),
        )
    await db.executemany(
        "INSERT INTO raid_members (clan_tag, start_time, player_tag, name, attacks, attack_limit, loot) "
        "VALUES ('#C', 's1', ?, ?, ?, ?, 0)",
        [("#A", "Alice", 6, 6), ("#B", "Bob", 2, 5)],
    )
    await db.executemany(
        "INSERT INTO clan_games_points (games_id, player_tag, clan_tag, points, updated_at) "
        "VALUES ('2026-11', ?, '#C', ?, 'x')",
        [("#A", 4000), ("#B", 1200)],
    )


def summary(acc):
    return [(i.kind, i.label, i.done, i.total) for i in acc.items]


async def test_account_todos(db):
    await seed(db)
    accts = {a.player_tag: a for a in await T.account_todos(db, ["#A", "#B", "#D", "#E", "#Z", "#A"], NOW)}
    assert list(accts) == ["#A", "#B", "#D", "#E", "#Z"]
    assert summary(accts["#A"]) == [("war", "Main", 1, 2), ("ranked", "Legend I", 3, 8)]
    assert accts["#A"].items[1].left == 5 and accts["#A"].items[1].period == "today"
    assert accts["#A"].items[0].ends_at == datetime(2026, 11, 8, tzinfo=UTC)
    # Bob: war done; Legend II counts the week (28 of 30), raid 2 of 5
    assert summary(accts["#B"]) == [("ranked", "Legend II", 28, 30), ("raid", "Main", 2, 5)]
    assert accts["#B"].items[0].ends_at == datetime(2026, 11, 9, 5, tzinfo=UTC)
    # Dan: CWL attack left, Electro counts the week (4 of 18), tracked clan mid-raid and not raided yet
    assert summary(accts["#D"]) == [
        ("cwl", "Feeder", 0, 1),
        ("ranked", "Electro League 32", 4, 18),
        ("raid", "Feeder", 0, 5),
    ]
    assert accts["#D"].items[1].pending and not accts["#D"].all_done
    # Eve: only a stale war; unknown tag: nothing
    assert accts["#E"].items == [] and accts["#E"].all_done
    assert (accts["#Z"].name, accts["#Z"].all_done) == ("#Z", True)
    assert not accts["#A"].all_done


async def test_clan_games_todos(db):
    await seed(db)
    accts = {a.player_tag: a for a in await T.account_todos(db, ["#A", "#B", "#D", "#E"], GAMES_NOW)}
    games = {t: [(i.done, i.total) for i in a.items if i.kind == "games"] for t, a in accts.items()}
    assert games == {"#A": [], "#B": [(1200, 4000)], "#D": [(0, 4000)], "#E": []}
    assert all(i.kind not in ("war", "cwl", "raid") for a in accts.values() for i in a.items)
    assert await T.games_items(db, {"#B": {"clan_tag": "#C"}}, NOW) == {}


def test_ranked_item():
    assert T.ranked_item("Legend I", 8, 0, NOW) is None
    item = T.ranked_item("Legend I", 3, 40, NOW)
    assert (item.left, item.ends_at) == (5, datetime(2026, 11, 8, 5, tzinfo=UTC))
    assert T.ranked_item("Legend III", 0, 24, NOW) is None
    item = T.ranked_item("Legend III", 5, 20, NOW)
    assert (item.left, item.period, item.ends_at) == (4, "this week", datetime(2026, 11, 9, 5, tzinfo=UTC))
    item = T.ranked_item("Electro League 32", 4, 10, NOW)
    assert (item.total, item.done, item.period) == (18, 10, "this week")  # weekly cap for tier 32
    assert T.ranked_item("Wizard League 10", 1, 1, NOW) is None
    assert T.ranked_item(None, 0, 0, NOW) is None


def test_pick_inactive():
    def row(name, ago):
        seen = None if ago is None else (NOW - timedelta(days=ago)).isoformat()
        return {"player_tag": "#" + name, "name": name, "last_seen": seen}

    rows = [row("fresh", 1), row("old", 10), row("never", None), row("mid", 4), row("edge", 2.9)]
    assert [r["name"] for r in T.pick_inactive(rows, NOW, 3)] == ["never", "old", "mid"]
    assert [r["name"] for r in T.pick_inactive(rows, NOW, 30)] == ["never"]


async def test_inactive_members(db):
    await db.executemany(
        "INSERT INTO players (player_tag, name, town_hall, clan_role, clan_tag, last_seen, updated_at) "
        "VALUES (?, ?, 15, 'member', ?, ?, 'x')",
        [
            ("#A", "a", "#C", (NOW - timedelta(days=5)).isoformat()),
            ("#B", "b", "#C", NOW.isoformat()),
            ("#D", "d", "#OTHER", None),
            ("#E", "e", "#C", None),
        ],
    )
    assert [r["player_tag"] for r in await T.inactive_members(db, "#C", NOW, 3)] == ["#E", "#A"]


def test_item_lines():
    end = datetime(2026, 11, 8, tzinfo=UTC)
    stamp = f"<t:{int(end.timestamp())}:R>"
    assert (
        item_line(T.TodoItem("war", "Main", 1, 2, end))
        == f"**War** · Main: 1 of 2 attacks left · ends {stamp}"
    )
    assert (
        item_line(T.TodoItem("cwl", "Main", 0, 1, end))
        == f"**CWL** · Main: 1 of 1 attack left · ends {stamp}"
    )
    assert (
        item_line(T.TodoItem("ranked", "Legend I", 3, 8, end, "today"))
        == f"**Ranked** · Legend I: 5 of 8 attacks left today · resets {stamp}"
    )
    assert item_line(T.TodoItem("ranked", "Electro League 32", 1, None, end, "today")) == (
        "**Ranked** · Electro League 32: 1 attack today"
    )
    assert (
        item_line(T.TodoItem("games", "", 1200, 4000, end))
        == f"**Clan Games**: 1,200/4,000 points · ends {stamp}"
    )
    name, value = account_field(T.AccountTodo("#A", "Alice", 16, []))
    assert (name, value) == ("Alice · TH16 (#A)", "All done ✅")
    _, value = account_field(T.AccountTodo("#A", "Alice", None, [T.TodoItem("war", "Main", 1, 2, end)]))
    assert value.startswith("**War**")


def test_ping_lines():
    rows = [{"player_tag": t, "name": n} for t, n in [("#A", "a_1"), ("#B", "b"), ("#C", "c")]]
    lines, unlinked = ping_lines(rows, {"#A": 1, "#B": 1})
    assert lines == ["<@1> a\\_1, b"] and unlinked == 1
