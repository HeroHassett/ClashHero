"""Ranked rules: daily/weekly resets, autofilled defenses, defenses that gain below Legend I,
and the 7-hit first day after promotion to Legend I."""

from datetime import UTC, datetime
from types import SimpleNamespace as NS

from test_collector import FakeHttp, battle, player_obj, setup  # noqa: F401

from clashhero.repos.legends import day_stats
from clashhero.services.battlelog import assign_trophies, parse_battles
from clashhero.services.collector import crossing_between


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def ranked_player(trophies, tier="Legend I"):
    p = player_obj(trophies=trophies, clan=NS(tag="#C", name="Clan"))
    p._raw_data = {"leagueTier": {"name": tier}, "troops": []}
    p.league = NS(name=tier)
    return p


def events(bot):
    return [e for n, e in bot.events if n == "clash_legend_battle"]


def test_crossings():
    assert not crossing_between(at(7, 6), at(7, 23)).day
    c = crossing_between(at(7, 23), at(8, 5, 1))  # Saturday -> Sunday reset
    assert (c.day, c.week, c.prev_day) == (True, False, "2026-11-07")
    c = crossing_between(at(8, 23), at(9, 5, 1))  # Sunday -> Monday: weekly reset
    assert (c.day, c.week, c.prev_day) == (True, True, "2026-11-08")
    assert crossing_between(at(6, 12), at(9, 12)).prev_day is None  # several days: no single day


def test_defenses_gain_below_legend_one():
    b = parse_battles(
        [
            battle("20261107T120000.000Z", kind="ranked"),
            battle("20261107T120100.000Z", attack=False, kind="ranked"),
        ]
    )
    assert assign_trophies(b, 50, defenses_gain=True) == [25, 25]
    assert assign_trophies(b, 50) == [50, 0]  # Legend I: gains only go to attacks


async def test_autofill_at_daily_reset(setup):  # noqa: F811
    bot, coc, col = setup
    coc.http = FakeHttp()
    # Nov 6: 8 attacks and 5 real defenses (2★ 81% = -26 each)
    log = [battle(f"20261106T{10 + i:02d}0000.000Z", opp=f"#A{i}") for i in range(8)]
    log += [
        battle(f"20261106T{18 + i:02d}0000.000Z", attack=False, opp=f"#D{i}", stars=2, pct=81)
        for i in range(5)
    ]
    coc.http.items = list(reversed(log))
    coc.player = ranked_player(5190)
    await col._poll_player("#A", at(6, 23, 30))
    # Reset at Nov 7 05:00: the game fills the 3 missing defenses with the average (-26 each).
    coc.player = ranked_player(5190 - 78)
    await col._poll_player("#A", at(7, 5, 1))
    rows = await bot.db.fetchall(
        "SELECT trophies FROM legend_events WHERE battle_type = 'autofill' AND legend_day = '2026-11-06'"
    )
    assert [r["trophies"] for r in rows] == [-26, -26, -26]
    stats = await day_stats(bot.db, "#A", "2026-11-06")
    assert (len(stats.defenses), stats.defense) == (8, -26 * 8)
    day = await bot.db.fetchone("SELECT end_trophies FROM legend_days WHERE legend_day = '2026-11-06'")
    assert day["end_trophies"] == 5112
    assert not events(bot)  # no alerts for autofill


async def test_shielded_day_gets_no_autofill(setup):  # noqa: F811
    bot, coc, col = setup
    coc.http = FakeHttp()
    coc.http.items = [battle("20261105T120000.000Z")]
    coc.player = ranked_player(5100)
    await col._poll_player("#A", at(6, 23))
    await col._poll_player("#A", at(7, 5, 1))  # trophies unchanged across the reset
    assert await bot.db.fetchval("SELECT COUNT(*) FROM legend_events WHERE battle_type = 'autofill'") == 0


async def test_weekly_reset_is_not_a_battle(setup):  # noqa: F811
    bot, coc, col = setup
    coc.http = FakeHttp()
    # Sunday Nov 8: 8 attacks, 6 defenses at -25; the player ends the week under 5000.
    log = [battle(f"20261108T{10 + i:02d}0000.000Z", opp=f"#A{i}", stars=2, pct=50) for i in range(8)]
    log += [
        battle(f"20261108T{18 + i:02d}0000.000Z", attack=False, opp=f"#D{i}", stars=2, pct=79)
        for i in range(6)
    ]
    coc.http.items = list(reversed(log))
    coc.player = ranked_player(4980)
    await col._poll_player("#A", at(8, 23, 59))
    # Monday 05:00: autofill (2 x -25) and then the floor back up to 5000.
    coc.player = ranked_player(5000)
    await col._poll_player("#A", at(9, 5, 1))
    autofill = await bot.db.fetchall("SELECT trophies FROM legend_events WHERE battle_type = 'autofill'")
    assert [r["trophies"] for r in autofill] == [-25, -25]  # expected amount, from the season average
    assert await bot.db.fetchval("SELECT COUNT(*) FROM legend_events WHERE legend_day = '2026-11-09'") == 0
    assert not events(bot)


async def test_season_reset_without_battle_log_is_not_inferred(setup):  # noqa: F811
    bot, coc, col = setup  # no battle log available (inference path)
    coc.player = ranked_player(5300)
    await col._poll_player("#A", at(8, 23))
    coc.player = ranked_player(5000)
    await col._poll_player("#A", at(9, 5, 1))
    assert await bot.db.fetchval("SELECT COUNT(*) FROM legend_events") == 0


async def test_lower_tier_battles_score_by_rule(setup):  # noqa: F811
    """Below Legend I the API's trophy count stays put, so battles are scored by stars and
    destruction (attacker: rate x destruction; defender: what's left of 40) and trophy changes are
    never carried, inferred or autofilled."""
    bot, coc, col = setup
    coc.http = FakeHttp()
    coc.http.items = [battle("20261106T120000.000Z", attack=False, opp="#D", kind="ranked")]
    coc.player = ranked_player(1003, "Electro League 32")
    await col._poll_player("#A", at(6, 23))
    coc.http.items = [
        battle("20261107T060000.000Z", opp="#E", kind="ranked", stars=2, pct=81),  # +26
        battle("20261107T061000.000Z", attack=False, opp="#F", kind="ranked", stars=1, pct=60),  # +28
        battle("20261107T062000.000Z", opp="#G", kind="ranked", stars=0, pct=18),  # +1
        *coc.http.items,
    ]
    coc.player = ranked_player(1030, "Electro League 32")  # the API count moves on its own
    await col._poll_player("#A", at(7, 6, 30))
    rows = await bot.db.fetchall(
        "SELECT opponent_tag, trophies, battle_type FROM legend_events ORDER BY occurred_at"
    )
    assert [(r["opponent_tag"], r["trophies"]) for r in rows] == [
        ("#D", 0),
        ("#E", 26),
        ("#F", 28),
        ("#G", 1),
    ]
    assert all(r["battle_type"] == "ranked" for r in rows)  # no autofill or inferred rows


async def test_first_legend_one_day_has_seven_hits(setup):  # noqa: F811
    bot, coc, col = setup
    coc.player = ranked_player(4950, "Legend II")
    await col._poll_player("#A", at(8, 23))
    coc.player = ranked_player(5000, "Legend I")  # promoted at Monday's reset
    await col._poll_player("#A", at(9, 5, 2))
    assert (await day_stats(bot.db, "#A", "2026-11-09")).cap == 7
    assert (await day_stats(bot.db, "#A", "2026-11-10")).cap == 8
