from datetime import UTC, datetime

from clashhero.utils.seasons import (
    clan_games,
    cwl_season_id,
    game_season,
    legend_day_bounds,
    legend_day_id,
    previous_game_season,
    raid_weekend,
    ranked_week,
)


def dt(*args):
    return datetime(*args, tzinfo=UTC)


def test_game_season_mid_cycle():
    s = game_season(dt(2026, 11, 7, 12))
    assert s.start == dt(2026, 11, 2, 5)
    assert s.end == dt(2026, 11, 30, 5)
    assert s.id == "2026-11-02"  # Supercell ids seasons by start date
    assert s.start.weekday() == 0


def test_game_season_boundary():
    assert game_season(dt(2026, 11, 30, 5)).start == dt(2026, 11, 30, 5)
    assert game_season(dt(2026, 11, 30, 4, 59)).end == dt(2026, 11, 30, 5)


def test_season_before_anchor():
    s = game_season(dt(2025, 9, 1))
    assert s.start < dt(2025, 9, 1) < s.end
    assert s.start.weekday() == 0


def test_previous_season():
    assert previous_game_season(dt(2026, 11, 7)).end == dt(2026, 11, 2, 5)


def test_legend_day():
    assert legend_day_bounds(dt(2026, 11, 7, 3)) == (dt(2026, 11, 6, 5), dt(2026, 11, 7, 5))
    assert legend_day_id(dt(2026, 11, 7, 6)) == "2026-11-07"


def test_cwl_season_id():
    assert cwl_season_id(dt(2026, 1, 3)) == "2026-01"


def test_raid_weekend():
    # 2026-11-07 is a Saturday; raid weekend started Friday 2026-11-06 07:00.
    w = raid_weekend(dt(2026, 11, 7, 12))
    assert w.start == dt(2026, 11, 6, 7)
    assert w.contains(dt(2026, 11, 7, 12))
    # Friday before 07:00 belongs to the previous week's (finished) raid.
    assert raid_weekend(dt(2026, 11, 13, 6)).start == dt(2026, 11, 6, 7)


def test_clan_games():
    w = clan_games(dt(2026, 11, 7))
    assert w.start == dt(2026, 11, 22, 8)
    assert w.end == dt(2026, 11, 28, 8)


def test_season_matches_api_season_ids():
    # legendStatistics.bestSeason.id seen from the live API was "2025-12-29".
    assert game_season(dt(2026, 1, 5)).id == "2025-12-29"


def test_ranked_week():
    w = ranked_week(dt(2026, 11, 7, 12))
    assert (w.id, w.start, w.end) == ("2026-11-02", dt(2026, 11, 2, 5), dt(2026, 11, 9, 5))
    assert ranked_week(dt(2026, 11, 9, 4, 59)).id == "2026-11-02"
    assert ranked_week(dt(2026, 11, 9, 5)).id == "2026-11-09"


def test_season_label_and_lookup():
    from clashhero.utils.seasons import season_by_id, season_for_date, season_label

    s = season_for_date("2026-11-03")
    assert s.id == "2026-11-02" and season_label(s) == "Nov 2 – Nov 30, 2026"
    assert season_for_date("2026-11").id == "2026-11-02"
    assert season_for_date("not a date") is None
    assert season_label(season_by_id("2026-12-28")) == "Dec 28 – Jan 25, 2026–2027"
