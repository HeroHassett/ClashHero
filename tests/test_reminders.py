from datetime import UTC, datetime, timedelta

import pytest

from clashhero.cogs.reminders import build_messages, reminder_block
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


def test_parse_roles():
    assert R.parse_roles("elder, co-leader") == ["admin", "coLeader"]
    assert R.parse_roles("Leader Co Leader member") == ["member", "coLeader", "leader"]
    assert R.parse_roles("coLeader,admin") == ["admin", "coLeader"]
    assert R.parse_roles("any") is None and R.parse_roles("  ") is None
    with pytest.raises(ValueError):
        R.parse_roles("elder, boss")


def _p(tag, th, role, done, total):
    return R.Pending(tag, tag, f"{done}/{total}", th, role, done, total)


def test_filters_apply():
    people = [
        _p("#A", 16, "leader", 0, 5),
        _p("#B", 12, "member", 3, 5),
        _p("#C", None, None, 1200, 4000),
        _p("#D", 14, "admin", 4, 6),
    ]
    # no filters: everyone (existing behaviour)
    assert R.Filters().apply(people, "war") == people
    assert R.Filters().describe() == ""

    def tags(f, type_="war"):
        return [p.player_tag for p in f.apply(people, type_)]

    assert tags(R.Filters(min_th=13)) == ["#A", "#D"]  # unknown TH never passes a TH filter
    assert tags(R.Filters(max_th=14)) == ["#B", "#D"]
    assert tags(R.Filters(min_th=12, max_th=14)) == ["#B", "#D"]
    assert tags(R.Filters(clan_roles=frozenset({"admin", "member"}))) == ["#B", "#D"]
    # games_below only applies to Clan Games, raid_min_left only to raids
    assert tags(R.Filters(games_below=1000), "games") == ["#A", "#B", "#D"]
    assert tags(R.Filters(games_below=1000), "war") == ["#A", "#B", "#C", "#D"]
    assert tags(R.Filters(raid_min_left=3), "raid") == ["#A", "#C"]
    assert tags(R.Filters(raid_min_left=3), "games") == ["#A", "#B", "#C", "#D"]
    desc = R.Filters(
        min_th=12, max_th=15, clan_roles=frozenset({"coLeader", "admin"}), war_type="cwl", games_below=1000
    ).describe()
    assert desc == "TH12-15 · Elder, Co-Leader · CWL wars · below 1,000 pts"
    assert R.Filters(min_th=13).describe() == "TH13+"
    assert R.Filters(raid_min_left=2).describe() == "2+ raid attacks left"


async def test_filter_storage(db):
    f = R.Filters(min_th=12, clan_roles=frozenset({"coLeader", "admin"}), war_type="random")
    rid = await R.create_reminder(db, 1, "war", ["#C"], 3600, "go", 5, False, 9, f)
    rem = await R.get_reminder(db, 1, rid)
    assert rem["clan_roles"] == "admin coLeader"
    assert R.Filters.from_row(rem) == f
    # None leaves a column alone, CLEAR resets it
    assert await R.update_reminder(db, 1, rid, min_th=R.CLEAR, max_th=15, war_type=None)
    assert R.Filters.from_row(await R.get_reminder(db, 1, rid)) == R.Filters(
        max_th=15, clan_roles=frozenset({"admin", "coLeader"}), war_type="random"
    )
    assert not await R.update_reminder(db, 1, rid, min_th=None)
    # reminders created without filters have none
    plain = await R.create_reminder(db, 1, "raid", ["#C"], 3600, "go", 5, False, 9)
    assert R.Filters.from_row(await R.get_reminder(db, 1, plain)) == R.Filters()


def test_check_filters():
    from clashhero.cogs.reminders import Reminders
    from clashhero.utils.resolve import ResolveError

    Reminders._check_filters("war", R.Filters(war_type="friendly", min_th=10, max_th=10))
    Reminders._check_filters("games", R.Filters(games_below=500))
    for type_, f in [
        ("cwl", R.Filters(war_type="random")),
        ("war", R.Filters(games_below=500)),
        ("games", R.Filters(raid_min_left=2)),
        ("raid", R.Filters(min_th=15, max_th=12)),
    ]:
        with pytest.raises(ResolveError):
            Reminders._check_filters(type_, f)


async def test_roles_autocomplete():
    from clashhero.cogs.reminders import Reminders

    async def values(current):
        return [c.value for c in await Reminders._roles_autocomplete(None, None, current)]

    assert await values("") == ["any", "member", "elder", "co-leader", "leader"]
    assert await values("el") == ["elder"]
    assert await values("member, ") == ["member, elder", "member, co-leader", "member, leader"]
    assert await values("member, elder, l") == ["member, elder, leader"]


async def test_war_type_and_pending_details(db):
    await db.execute(
        "INSERT INTO players (player_tag, name, town_hall, clan_tag, clan_role, updated_at) VALUES "
        "('#A', 'Alice', 16, '#C', 'coLeader', 'x'), ('#B', 'Bob', 13, '#OTHER', 'leader', 'x'), "
        "('#M', 'Mia', 11, '#C', 'member', 'x')"
    )
    war_id = await db.execute(
        "INSERT INTO wars (clan_tag, war_type, state, preparation_start, attacks_per_member, end_time) "
        "VALUES ('#C', 'friendly', 'inWar', '2026-11-06', 2, '2026-11-08T00:00:00+00:00')"
    )
    await db.executemany(
        "INSERT INTO war_members (war_id, player_tag, name, town_hall, map_position, is_opponent) "
        "VALUES (?, ?, ?, ?, ?, 0)",
        [(war_id, "#A", "Alice", 15, 1), (war_id, "#B", "Bob", None, 2)],
    )
    assert (await R.active_war(db, "#C", cwl=False))["id"] == war_id
    assert await R.active_war(db, "#C", cwl=False, war_type="random") is None
    assert await R.active_war(db, "#C", cwl=False, war_type="cwl") is None
    war = await R.active_war(db, "#C", cwl=False, war_type="friendly")
    pending = await R.war_pending(db, war)
    # TH from the war lineup (else the player row); role only when they're in the war's clan
    assert [(p.player_tag, p.town_hall, p.role, p.left) for p in pending] == [
        ("#A", 15, "coLeader", 2),
        ("#B", 13, None, 2),
    ]
    assert [p.player_tag for p in R.Filters(clan_roles=frozenset({"coLeader"})).apply(pending, "war")] == [
        "#A"
    ]

    await db.execute(
        "INSERT INTO raid_members (clan_tag, start_time, player_tag, name, attacks, attack_limit, loot) "
        "VALUES ('#C', 's', '#A', 'Alice', 4, 6, 100)"
    )
    raid = await R.raid_pending(db, "#C", "s")
    assert [(p.player_tag, p.town_hall, p.role, p.left) for p in raid] == [
        ("#A", 16, "coLeader", 2),
        ("#M", 11, "member", 5),
    ]
    assert [p.player_tag for p in R.Filters(raid_min_left=3).apply(raid, "raid")] == ["#M"]
    games = await R.games_pending(db, "#C", "2026-11")
    assert [(p.player_tag, p.town_hall, p.role, p.done) for p in games] == [
        ("#A", 16, "coLeader", 0),
        ("#M", 11, "member", 0),
    ]


def test_reminder_block():
    row = {
        "id": 3,
        "type": "war",
        "duration_seconds": 7200,
        "channel_id": 55,
        "clans": "#A #B",
        "message": "Attack!",
    }
    assert reminder_block(row) == "`#3` ⚔️ **Clan War** · `2h` before the end\n<#55> · `#A` `#B`\n> Attack!"
    filtered = reminder_block({**row, "type": "raid", "clans": "", "min_th": 14})
    assert filtered.splitlines()[0].startswith("`#3` 🏰 **Capital Raid Weekend**")
    assert filtered.splitlines()[1] == "<#55>"
    assert filtered.splitlines()[-1] == "> Only: TH14+"
