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


US = {"id": 32000249, "name": "United States", "countryCode": "US"}


async def test_country_rankings(db):
    await ranks.store_country_rankings(db, US, items(("#A", 57), ("#B", 3)), NOW)
    assert await ranks.country_rank(db, "#A", NOW) == ranks.CountryRank(57, "US", "United States")
    # Next fetch: #B dropped out, #A moved up; another country doesn't touch the US rows.
    await ranks.store_country_rankings(db, US, items(("#A", 50)), NOW + timedelta(hours=3))
    await ranks.store_country_rankings(
        db, {"id": 1, "name": "Japan", "countryCode": "JP"}, items(("#C", 1)), NOW
    )
    assert (await ranks.country_rank(db, "#A")).rank == 50
    assert await ranks.country_rank(db, "#B") is None
    assert (await ranks.country_rank(db, "#C")).country_code == "JP"
    assert await ranks.country_rank(db, "#A", NOW + timedelta(hours=12)) is None  # stale
    assert (
        await ranks.store_country_rankings(db, {"id": 2, "name": "X"}, items(("#D", 1)), NOW) == 0
    )  # no code


async def test_collector_polls_country_rankings(setup):  # noqa: F811
    bot, coc, col = setup

    class Http(FakeHttp):
        async def request(self, route):
            if "/locations?" in route.url:
                return {
                    "items": [
                        {**US, "isCountry": True},
                        {"id": 32000000, "name": "Europe", "isCountry": False},  # a region: skipped
                    ]
                }
            return {"items": items(("#A", 7))}

    coc.http = Http()
    await col._poll_country_ranks()
    assert (await ranks.country_rank(bot.db, "#A")).rank == 7


async def test_season_finishes_and_best_finish(db):
    await ranks.store_season_finishes(db, "v2-2026-09-28T05:00:00Z", items(("#A", 12000), ("#B", 4)))
    await ranks.store_season_finishes(db, "v2-2026-10-26T05:00:00Z", items(("#A", 9013)))
    assert await ranks.stored_seasons(db) == {"v2-2026-09-28T05:00:00Z", "v2-2026-10-26T05:00:00Z"}
    best = await ranks.best_finish(db, "#A")
    assert (best.rank, best.season_id) == (9013, "v2-2026-10-26T05:00:00Z")
    assert await ranks.best_finish(db, "#Z") is None


async def test_collector_fetches_each_finished_season_once(setup):  # noqa: F811
    bot, coc, col = setup
    calls = []

    class Http(FakeHttp):
        async def request(self, route):
            calls.append(route.url)
            if route.url.endswith("/seasons"):
                return {"items": [{"id": "2025-05"}, {"id": "v2-2026-10-26T05:00:00Z"}]}
            if "after=" not in route.url:
                return {"items": items(("#A", 1)), "paging": {"cursors": {"after": "p2"}}}
            return {"items": items(("#B", 2)), "paging": {"cursors": {}}}

    coc.http = Http()
    await col._poll_season_finishes()
    assert (await ranks.best_finish(bot.db, "#B")).rank == 2  # both pages stored
    assert not any("2025-05" in u for u in calls[1:])  # pre-ranked-era seasons skipped
    calls.clear()
    await col._poll_season_finishes()
    assert len(calls) == 1  # already stored: only the season list is read
