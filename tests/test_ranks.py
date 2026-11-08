from datetime import UTC, datetime, timedelta

from test_collector import FakeHttp, setup  # noqa: F401

from clashhero.repos import ranks

NOW = datetime(2026, 11, 8, 4, 40, tzinfo=UTC)  # legend day 2026-11-07 (resets at 05:00 UTC)


def items(*pairs):
    return [{"tag": tag, "rank": rank, "trophies": 6000 - rank} for tag, rank in pairs]


async def test_current_and_end_of_day_ranks(db):
    await ranks.store_rankings(db, items(("#A", 1), ("#B", 2)), "2026-11-07", NOW)
    await ranks.store_rankings(db, items(("#B", 1), ("#C", 2)), "2026-11-07", NOW + timedelta(minutes=15))
    assert await ranks.current_rank(db, "#B", NOW) == 1
    assert await ranks.current_rank(db, "#A", NOW) is None  # dropped out of the top 200
    assert await ranks.eod_rank(db, "#B", "2026-11-07") == 1  # the last fetch of the day wins
    assert await ranks.eod_rank(db, "#A", "2026-11-07") is None
    # Next day: the previous day's end-of-day rank stays.
    await ranks.store_rankings(db, items(("#C", 1)), "2026-11-08", NOW + timedelta(hours=1))
    assert await ranks.eod_rank(db, "#B", "2026-11-07") == 1
    assert await ranks.eod_rank(db, "#C", "2026-11-08") == 1


async def test_stale_or_empty_rankings(db):
    assert await ranks.store_rankings(db, [], "2026-11-07", NOW) == 0  # keeps whatever was there
    await ranks.store_rankings(db, items(("#A", 5)), "2026-11-07", NOW)
    assert await ranks.store_rankings(db, [{"tag": "#X"}], "2026-11-07", NOW) == 0  # no rank: ignored
    assert await ranks.current_rank(db, "#A", NOW + timedelta(hours=3)) is None  # too old
    assert await ranks.current_rank(db, "#A") == 5


async def test_collector_polls_the_global_ranking(setup):  # noqa: F811
    bot, coc, col = setup
    coc.http = FakeHttp()
    coc.http.items = items(("#A", 3))
    await col._poll_ranks()
    assert await ranks.current_rank(bot.db, "#A") == 3
