from types import SimpleNamespace as NS

import pytest
from test_cards import badge_png, open_png

from clashhero.cogs import info as info_mod
from clashhero.services import cards

AURORA = (0, 0, 1, 11, 4, 2, 5, 10, 5, 7, 2, 2, 0, 0, 0, 0, 0, 0, 0, 23, 11, 9, 1, 0)


@pytest.mark.parametrize(
    ("top", "ticks"),
    [(23, [0, 10, 20, 30]), (4, [0, 1, 2, 3, 4]), (100, [0, 50, 100]), (7, [0, 2, 4, 6, 8]), (0, [0, 1])],
)
def test_nice_ticks(top, ticks):
    assert cards.nice_ticks(top) == ticks


def test_busiest_window_wraps_past_midnight():
    assert cards.busiest_window(AURORA) == 19
    late = [0] * 24
    late[22], late[23], late[0] = 5, 5, 5
    assert cards.busiest_window(late) == 22


def test_activity_card_renders():
    plain = open_png(cards.render_activity(cards.ActivityCard("Aurora", "#2J8VGUCV2", AURORA, "UTC+00:00")))
    assert plain.size == (cards.WIDTH, cards.ACT_HEIGHT)
    badged = cards.render_activity(
        cards.ActivityCard("Aurora", "#2J8VGUCV2", AURORA, "UTC+00:00", badge=badge_png())
    )
    corner = (cards.WIDTH - 140, 20, cards.WIDTH, 140)  # where the badge goes
    assert plain.crop(corner).tobytes() != open_png(badged).convert(plain.mode).crop(corner).tobytes()
    empty = open_png(
        cards.render_activity(cards.ActivityCard("3 clans", "#A, #B, #C", (0,) * 24, "UTC-05:00"))
    )
    assert empty.size == plain.size


async def test_activity_command_image(monkeypatch):
    class Db:
        async def get_clan_row(self, tag):
            return {"name": "Aurora", "badge_url": "https://api-assets.clashofclans.com/badges/x.png"}

    async def fake_badges(urls):
        return {u: badge_png() for u in urls}

    monkeypatch.setattr(cards, "fetch_badges", fake_badges)
    cog = NS(db=Db())
    image = await info_mod.Info._activity_image(cog, ["#2J8VGUCV2"], list(AURORA), "UTC+00:00")
    assert image.filename == "clan-activity.png"
    assert open_png(image.fp.read()).size == (cards.WIDTH, cards.ACT_HEIGHT)


def test_activity_days_counts_only_tracked_days():
    from datetime import UTC, datetime

    from clashhero.cogs._info_embeds import activity_days

    now = datetime(2026, 11, 8, 22, tzinfo=UTC)
    assert activity_days([("2026-11-06T22", 3), ("2026-11-08T19", 5)], now) == 2
    assert activity_days([("2026-11-08T21", 1)], now) == 1
    assert activity_days([("2026-10-01T00", 1)], now) == 7
    assert activity_days([], now) == 7
