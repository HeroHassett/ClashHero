"""Backfill from the battle log, checked against real Legend I battles (Nov 7, 2026) whose
per-hit trophies were confirmed in-game: attacks +249, defenses -173, net +76."""

from datetime import UTC, datetime

from test_collector import FakeHttp, battle, legend_player, setup  # noqa: F401

from clashhero.services.battlelog import legend_trophies, parse_battles

# (timestamp, attack?, stars, destruction, trophies shown in-game)
NOV_7 = [
    ("20261107T060822.000Z", False, 2, 79, -25),
    ("20261107T061526.000Z", False, 3, 100, -40),
    ("20261107T123138.000Z", False, 2, 88, -28),
    ("20261107T145837.000Z", False, 2, 85, -27),
    ("20261107T190104.000Z", False, 2, 81, -26),
    ("20261107T224444.000Z", True, 3, 100, 40),
    ("20261107T224757.000Z", True, 2, 88, 28),
    ("20261107T225031.000Z", True, 1, 56, 11),
    ("20261107T225345.000Z", True, 2, 99, 32),
    ("20261107T225657.000Z", True, 2, 99, 32),
    ("20261107T230018.000Z", True, 3, 100, 40),
    ("20261107T230318.000Z", True, 2, 81, 26),
    ("20261107T230614.000Z", True, 3, 100, 40),
    ("20261107T231920.000Z", False, 2, 83, -27),
]
LOG = [
    battle(ts, attack, f"#O{i}", "legend", stars, pct) for i, (ts, attack, stars, pct, _) in enumerate(NOV_7)
]


def test_legend_trophy_rule_matches_in_game_numbers():
    for b, (*_, expected) in zip(parse_battles(LOG), NOV_7, strict=True):
        assert legend_trophies(b) == expected, b


async def test_first_sight_backfills_the_day(setup):  # noqa: F811
    bot, coc, col = setup
    coc.http = FakeHttp()
    coc.http.items = list(reversed(LOG))  # API order: newest first
    coc.player = legend_player(5021)
    await col._poll_player("#A", datetime(2026, 11, 8, 2, 15, tzinfo=UTC))
    assert not [e for n, e in bot.events if n == "clash_legend_battle"]  # backfill sends no alerts
    rows = await bot.db.fetchall(
        "SELECT kind, trophies, trophies_after FROM legend_events "
        "WHERE legend_day = '2026-11-07' ORDER BY occurred_at"
    )
    attacks = [r["trophies"] for r in rows if r["kind"] == "attack"]
    defenses = [r["trophies"] for r in rows if r["kind"] == "defense"]
    assert (len(attacks), sum(attacks), len(defenses), sum(defenses)) == (8, 249, 6, -173)
    assert rows[-1]["trophies_after"] == 5021
    day = await bot.db.fetchone("SELECT * FROM legend_days WHERE legend_day = '2026-11-07'")
    assert (day["start_trophies"], day["end_trophies"]) == (4945, 5021)
