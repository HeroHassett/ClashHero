from datetime import UTC, datetime, timedelta

import pytest

from clashhero.cogs.reminders import build_messages
from clashhero.repos import reminders as R

NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)


def test_parse_duration():
    assert R.parse_duration("2h30m") == timedelta(hours=2, minutes=30)
    assert R.parse_duration("1d") == timedelta(days=1)
    assert R.parse_duration("1h 15m") == timedelta(hours=1, minutes=15)
    for bad in ("", "abc", "2x", "h2", "2h junk"):
        with pytest.raises(ValueError):
            R.parse_duration(bad)
    assert R.format_duration(9000) == "2h30m"


def test_is_due():
    end = NOW + timedelta(hours=1)
    assert R.is_due(NOW, end, 3600, False)
    assert not R.is_due(NOW, end, 1800, False)  # too early
    assert not R.is_due(NOW, end, 3600, True)  # already sent
    assert not R.is_due(end, end, 3600, False)  # already over


def test_build_messages():
    pending = [R.Pending("#A", "Alice", "1/2"), R.Pending("#B", "Bob", "0/2"), R.Pending("#C", "Cara", "0/2")]
    msgs = build_messages("Attack!", [("Clan", pending)], {"#A": 1, "#B": 1}, True)
    text = "".join(msgs)
    assert "<@1> Alice 1/2, Bob 0/2" in text and "Not linked: Cara 0/2" in text
    assert build_messages("Attack!", [("Clan", pending)], {}, False) == ["Attack!"]
    assert build_messages("Attack!", [("Clan", [])], {}, True) == []
    # role filter drops user 1 entirely
    filtered = "".join(build_messages("x", [("Clan", pending)], {"#A": 1}, True, lambda uid: False))
    assert "<@1>" not in filtered


async def test_reminder_crud_and_sent(db):
    rid = await R.create_reminder(db, 1, "war", ["#C"], 3600, "go", 5, False, 9)
    assert [r["id"] for r in await R.list_reminders(db, 1)] == [rid]
    assert await R.update_reminder(db, 1, rid, message="hurry")
    assert (await R.get_reminder(db, 1, rid))["message"] == "hurry"
    assert not await R.was_sent(db, rid, "war:1")
    await R.mark_sent(db, rid, "war:1")
    await R.mark_sent(db, rid, "war:1")
    assert await R.was_sent(db, rid, "war:1")
    await R.set_config(db, 1, "war", 77, "optout")
    assert (await R.get_config(db, 1, "war"))["mode"] == "optout"
    assert await R.delete_reminder(db, 1, rid)
    assert not await R.delete_reminder(db, 1, rid)


async def test_pending_queries(db):
    war_id = await db.execute(
        "INSERT INTO wars (clan_tag, war_type, state, preparation_start, attacks_per_member, end_time) "
        "VALUES ('#C', 'random', 'inWar', '2026-11-06', 2, '2026-11-08T00:00:00+00:00')"
    )
    await db.executemany(
        "INSERT INTO war_members (war_id, player_tag, name, town_hall, map_position, is_opponent) "
        "VALUES (?, ?, ?, 16, ?, ?)",
        [(war_id, "#A", "Alice", 1, 0), (war_id, "#B", "Bob", 2, 0), (war_id, "#X", "X", 1, 1)],
    )
    for order in (1, 2):
        await db.execute(
            "INSERT INTO war_attacks (war_id, attack_order, attacker_tag, defender_tag, stars, "
            "destruction, is_fresh, is_opponent, seen_at) VALUES (?, ?, '#A', '#X', 3, 100, 1, 0, 'x')",
            (war_id, order),
        )
    war = await R.active_war(db, "#C", cwl=False)
    assert war is not None and await R.active_war(db, "#C", cwl=True) is None
    assert [(p.player_tag, p.detail) for p in await R.war_pending(db, war)] == [("#B", "0/2")]

    await db.execute(
        "INSERT INTO players (player_tag, name, clan_tag, updated_at) VALUES "
        "('#A', 'Alice', '#C', 'x'), ('#B', 'Bob', '#C', 'x')"
    )
    await db.execute(
        "INSERT INTO raid_members (clan_tag, start_time, player_tag, name, attacks, attack_limit, loot) "
        "VALUES ('#C', 's', '#A', 'Alice', 6, 6, 100)"
    )
    assert [p.player_tag for p in await R.raid_pending(db, "#C", "s")] == ["#B"]
    await db.execute(
        "INSERT INTO clan_games_points (games_id, player_tag, clan_tag, points, updated_at) "
        "VALUES ('2026-11', '#A', '#C', 4000, 'x')"
    )
    assert [p.player_tag for p in await R.games_pending(db, "#C", "2026-11")] == ["#B"]
