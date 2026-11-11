"""The /legends logs season card: builder, renderer and command."""

import io
import logging
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace as NS

import pytest
from PIL import Image

from clashhero.cogs import legends as cog
from clashhero.repos import legends as repo
from clashhero.services import cards
from clashhero.utils.seasons import season_by_id

SEASON = "2026-11-02"
TAG = "#2PQ8VLYC0"
NOW = datetime(2026, 11, 7, 18, tzinfo=UTC)  # day 6 of the season


def open_png(data: bytes) -> Image.Image:
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


def ev(day, kind, trophies, stars=None, battle_type=None):
    return {
        "player_tag": TAG,
        "legend_day": day,
        "kind": kind,
        "trophies": trophies,
        "occurred_at": f"{day}T10:00:00+00:00",
        "stars": stars,
        "destruction": None if stars is None else 90,
        "battle_type": battle_type,
    }


def build(days, *, season=SEASON, start=5000, skip=(), lower=False, inferred=()):
    """A season of ``days`` legend days: 8 attacks (3★, 3★, 2★ ...) and 8 defenses a day in
    Legend I; a few gains on defense below it."""
    first = date.fromisoformat(season)
    day_rows, events = [], []
    trophies = start
    for i in range(days):
        if i in skip:
            continue
        day = (first + timedelta(days=i)).isoformat()
        begin = trophies
        logged = i not in inferred
        hits = (
            [("attack", 30, 3), ("attack", 20, 2), ("defense", 8, 1), ("defense", 4, 0)]
            if lower
            else [("attack", 40, 3)] * 5
            + [("attack", 24, 2)] * 3
            + [("defense", -40, 3)] * 3
            + [("defense", -20, 2)] * 5
        )
        for kind, t, stars in hits:
            events.append(ev(day, kind, t, stars if logged else None))
            trophies += t
        day_rows.append(
            {"player_tag": TAG, "legend_day": day, "start_trophies": begin, "end_trophies": trophies}
        )
    return repo.build_season(day_rows, events), trophies


def card_for(summary, *, season=SEASON, now=NOW, name="JoNico", trophies_now=None, **kw):
    return cog.season_card(
        name=name,
        tag=TAG,
        window=season_by_id(season),
        summary=summary,
        now=now,
        trophies_now=trophies_now,
        **kw,
    )


# ------------------------------------------------------------------ star breakdowns


def test_star_counts():
    s = cards.star_counts([3, 3, 2, None, 0, 5, -1])
    assert s.counts == (2, 0, 1, 3) and s.missing == 1 and s.total == 6
    assert s.share(3) == 0.5 and s.triple_rate == 0.5 and s.share(1) == 0
    empty = cards.star_counts([None, None])
    assert empty.total == 0 and empty.missing == 2 and empty.triple_rate is None and empty.share(3) == 0


def test_autofill_is_not_a_battle_and_inferred_battles_lack_stars():
    day = "2026-11-02"
    summary = repo.build_season(
        [{"player_tag": TAG, "legend_day": day, "start_trophies": 5000, "end_trophies": 4950}],
        [
            ev(day, "attack", 40, 3),
            ev(day, "attack", 30),  # inferred from a trophy change: no stars
            ev(day, "defense", -40, 3),
            ev(day, "defense", -50, battle_type="autofill"),
        ],
    )
    card = card_for(summary)
    assert card.attack_stars == cards.StarCounts((0, 0, 0, 1), missing=1)
    assert card.defense_stars == cards.StarCounts((0, 0, 0, 1), missing=0)
    assert (card.attacks, card.defenses) == (2, 2)


# ------------------------------------------------------------------ builder


def test_early_season():
    summary, trophies = build(6)
    card = card_for(summary, trophies_now=trophies)
    assert (
        len(card.days) == 28
        and card.days[0].day == date(2026, 11, 2)
        and card.days[-1].day == date(2026, 11, 29)
    )
    assert card.today == 5 and card.played_days == 6
    assert all(p.end is None and p.net is None for p in card.days[6:])  # the rest of the season
    assert len(card.shown) == 6 and card.shown[-1].day == date(2026, 11, 7)  # the season so far
    assert card.season == "Nov 2 – Nov 30, 2026"
    assert card.trophies == trophies == card.days[5].end  # today's point: the trophies now
    assert card.offense_per_day == 272 and card.defense_per_day == -220 and card.net_per_day == 52
    assert (card.attack_days, card.defense_days, card.cap) == (6, 6, 8)
    first = card.days[0]
    assert (first.offense, first.defense, first.attacks, first.defenses, first.net) == (272, -220, 8, 8, 52)
    assert card.attack_stars.counts == (0, 0, 18, 30) and card.attack_stars.triple_rate == 30 / 48
    assert card.defense_stars.counts == (0, 0, 30, 18)


def test_full_past_season():
    summary, trophies = build(28, season="2026-10-05")
    card = card_for(summary, season="2026-10-05")
    assert card.today is None and card.played_days == 28 and len(card.shown) == 28
    assert card.trophies == trophies == card.days[-1].end  # final: the last day's end
    assert card.trophies == 5000 + 28 * 52


def test_untracked_days_are_gaps():
    summary, _ = build(22, skip=(4, 5, 6, 15), inferred=(9,))
    card = card_for(summary, now=datetime(2026, 11, 23, 18, tzinfo=UTC), trophies_now=9999)
    assert card.today == 21 and card.trophies == 9999
    assert [i for i, p in enumerate(card.days[:22]) if p.end is None] == [4, 5, 6, 15]
    assert card.played_days == 18
    assert card.attack_stars.missing == 8 and card.defense_stars.missing == 8


def test_tracking_started_mid_season():
    summary, _ = build(8)
    late = repo.SeasonSummary(summary.days[3:])  # first tracked day: Nov 5
    card = card_for(late, now=datetime(2026, 11, 9, 18, tzinfo=UTC))
    assert [p.end is None for p in card.shown] == [True] * 3 + [False] * 5
    assert card.attack_days == 5 and card.played_days == 5


def test_ranks_live_or_the_latest_end_of_day():
    summary, trophies = build(6)
    ranks = {"2026-11-03": 900, "2026-11-05": 812, "2026-11-20": 1}  # a future day is never shown
    card = card_for(summary, trophies_now=trophies, day_ranks=ranks)
    assert [p.rank for p in card.shown] == [None, 900, None, 812, None, None]
    assert (card.rank, card.rank_day) == (812, date(2026, 11, 5))  # not live: from the end of a day
    live = card_for(summary, trophies_now=trophies, day_ranks=ranks, rank_now=150)
    assert (live.rank, live.rank_day) == (150, None)
    past = card_for(
        build(28, season="2026-10-05")[0], season="2026-10-05", rank_now=150, day_ranks={"2026-11-01": 77}
    )
    assert (past.rank, past.rank_day) == (77, date(2026, 11, 1))  # a past season ignores the live rank
    assert (card_for(summary).rank, card_for(summary).rank_day) == (None, None)


def test_battle_caps_follow_the_league():
    summary, _ = build(6)
    assert card_for(summary, league="Legend I").cap == 8
    assert card_for(summary, league="Legend II").cap is None  # weekly tournaments have no daily cap


def test_lower_tier_defenses_gain():
    summary, _ = build(10, start=3900, lower=True)
    card = card_for(summary, now=datetime(2026, 11, 11, 18, tzinfo=UTC))
    assert card.defense_per_day == 12 and card.offense_per_day == 50 and card.net_per_day == 62


def test_empty_season():
    card = card_for(repo.SeasonSummary([]))
    assert card.trophies is None and card.peak is None and card.played_days == 0
    assert (card.rank, card.attack_days) == (None, 0)
    assert card.offense_per_day is None and card.attack_stars.total == 0


# ------------------------------------------------------------------ renderer


@pytest.mark.parametrize(
    ("days", "kw", "now"),
    [
        (6, {}, NOW),  # early season
        (28, {"season": "2026-10-05"}, NOW),  # a full past season
        (22, {"skip": (4, 5, 6, 15), "inferred": (9, 10)}, datetime(2026, 11, 23, 18, tzinfo=UTC)),
        (12, {"start": 3900, "lower": True}, datetime(2026, 11, 13, 18, tzinfo=UTC)),
        (0, {}, NOW),  # nothing recorded yet
    ],
)
def test_render_is_a_png_of_the_expected_size(days, kw, now):
    summary, trophies = build(days, **kw)
    card = card_for(
        summary, season=kw.get("season", SEASON), now=now, trophies_now=trophies if days else None
    )
    im = open_png(cards.render_legend_season(card))
    assert im.format == "PNG" and im.size == (cards.WIDTH, cards.season_height(card))


@pytest.mark.parametrize("name", ["龍の騎士 JoNico", "قراف Night Owl", "전설의 용사", "⚡🔥", ""])
def test_cjk_arabic_and_undrawable_names(name):
    summary, trophies = build(3)
    card = card_for(summary, name=name, trophies_now=trophies)
    im = open_png(cards.render_legend_season(card))
    assert im.size == (1000, cards.season_height(card))


def test_gaps_break_the_line():
    """No line is drawn across untracked days: the plot is empty between the days around a gap."""
    summary, _ = build(12, skip=(4, 5, 6))
    card = card_for(summary, now=datetime(2026, 11, 13, 18, tzinfo=UTC))
    im = open_png(cards.render_legend_season(card)).convert("RGB")
    slot = (cards.PLOT_R - cards.PLOT_L) / len(card.shown)  # the season so far: 12 days
    x = round(cards.PLOT_L + 5.5 * slot)  # the middle of the gap
    column = [im.getpixel((x, y)) for y in range(cards.PLOT_TOP, cards.PLOT_TOP + cards.PLOT_H)]
    assert not any(r > 180 and g > 120 and b < 120 for r, g, b in column)  # no gold line
    x = round(cards.PLOT_L + 1.0 * slot)  # between two tracked days: the line is there
    column = [im.getpixel((x, y)) for y in range(cards.PLOT_TOP, cards.PLOT_TOP + cards.PLOT_H)]
    assert any(r > 180 and g > 120 and b < 120 for r, g, b in column)


def test_the_strip_lines_up_then_wraps():
    assert [cards.strip_rows(n) for n in (1, 14, 15, 20, 21, 28)] == [1, 1, 2, 2, 3, 3]
    short = card_for(build(6)[0])
    full = card_for(build(28, season="2026-10-05")[0], season="2026-10-05")
    step = cards.STRIP_ROW_H + cards.STRIP_ROW_GAP
    assert cards.trajectory_height(full) - cards.trajectory_height(short) == 2 * step
    assert open_png(cards.render_legend_season(full)).size == (1000, cards.season_height(full))


def test_point_labels():
    def x_at(i, col=60.0):
        return cards.PLOT_L + (i + 0.5) * col

    points = [
        (i, cards.SeasonPoint(date(2026, 11, 2 + i), end)) for i, end in enumerate((5000, 5100, 4950, 5050))
    ]
    assert cards._labelled(points, lambda i: x_at(i, 80)) == points  # few days: every point
    many = [(i, cards.SeasonPoint(date(2026, 10, 5) + timedelta(days=i), 5000 + 10 * i)) for i in range(28)]
    many[20] = (20, cards.SeasonPoint(many[20][1].day, 9999))  # the peak
    kept = [i for i, _ in cards._labelled(many, lambda i: x_at(i, 30))]
    assert kept == [27, 20, 0]  # latest, peak, low (also the first); spaced at least 64 px apart
    assert cards._labelled([], x_at) == []


def test_the_line_is_smooth_but_never_overshoots():
    points = [(0.0, 10.0), (10.0, 0.0), (20.0, 0.0), (30.0, 5.0)]
    curve = cards._smooth(points, steps=10)
    assert curve[0] == points[0] and curve[-1] == points[-1] and len(curve) == 31
    assert all(pt in curve for pt in points)
    assert all(-1e-9 <= y <= 10 for _, y in curve)
    assert all(y == 0 for x, y in curve if 10 <= x <= 20)  # flat stays flat
    assert cards._smooth(points[:2]) == points[:2]


def test_nice_ticks():
    ticks = cards._nice_ticks(5012, 5421)
    assert ticks[0] <= 5012 and ticks[-1] >= 5421 and len(ticks) <= 6
    assert all(t % 100 == 0 for t in ticks)
    flat = cards._nice_ticks(5000, 5000)
    assert flat[0] < 5000 < flat[-1]


# ------------------------------------------------------------------ /legends logs


@pytest.fixture
async def season_db(db, monkeypatch):
    await db.execute(
        "INSERT INTO players (player_tag, name, town_hall, trophies, league, clan_tag, updated_at) "
        "VALUES (?, 'Amy', 17, 5104, 'Legend I', '#C', 'x')",
        (TAG,),
    )
    summary_rows = [("2026-11-02", 5000, 5052), ("2026-11-03", 5052, 5104)]
    for day, start, end in summary_rows:
        await db.execute(
            "INSERT INTO legend_days (player_tag, legend_day, season_id, start_trophies, end_trophies) "
            "VALUES (?, ?, ?, ?, ?)",
            (TAG, day, SEASON, start, end),
        )
        for kind, t, stars in [("attack", 40, 3), ("attack", 32, 2), ("defense", -20, 2)]:
            await db.execute(
                "INSERT INTO legend_events (player_tag, legend_day, season_id, kind, trophies, "
                "trophies_after, occurred_at, stars, destruction) VALUES (?, ?, ?, ?, ?, 0, ?, ?, 90)",
                (TAG, day, SEASON, kind, t, f"{day}T10:00:00+00:00", stars),
            )
    await db.execute(
        "INSERT INTO legend_day_full_ranks (player_tag, legend_day, rank, trophies) "
        "VALUES (?, '2026-11-02', 431, 5052)",
        (TAG,),
    )
    await db.execute(
        "INSERT INTO player_ranks (player_tag, rank, trophies, fetched_at) VALUES (?, 7, 5104, ?)",
        (TAG, datetime(2026, 11, 3, 17, tzinfo=UTC).isoformat()),
    )
    monkeypatch.setattr(cog, "_now", lambda: datetime(2026, 11, 3, 18, tzinfo=UTC))
    return db


class FakeResponse:
    def __init__(self):
        self.deferred = False

    async def defer(self, **kwargs):
        self.deferred = True


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)


async def run_logs(db, season=None):
    interaction = NS(
        client=NS(db=db), guild_id=None, user=NS(id=1), response=FakeResponse(), followup=FakeFollowup()
    )
    await cog.Legends.legends_logs.callback(cog.Legends(NS(db=db)), interaction, TAG, None, season)
    assert interaction.response.deferred
    [msg] = interaction.followup.sent
    return msg


async def test_logs_attach_the_season_card(season_db, monkeypatch):
    seen = {}
    real = cards.render_legend_season

    def spy(card):
        seen["card"] = card
        return real(card)

    monkeypatch.setattr(cards, "render_legend_season", spy)
    msg = await run_logs(season_db)
    embed, file = msg["embed"], msg["file"]
    assert embed.image.url == "attachment://legend-season.png" and file.filename == "legend-season.png"
    card = seen["card"]
    assert open_png(file.fp.read()).size == (1000, cards.season_height(card))
    assert embed.author.name == "LEGEND I · TODAY"  # the day at a glance, with the season card
    assert "ATTACK" in embed.description and "**Attacks 2/8** · ▲ **+72**" in embed.description
    assert [b.item.label for b in msg["view"].children][0] == "Refresh"

    assert (card.player_name, card.today, card.trophies, card.league) == ("Amy", 1, 5104, "Legend I")
    assert [p.end for p in card.days[:3]] == [5052, 5104, None]
    assert [p.rank for p in card.days[:2]] == [431, None] and card.rank == 7  # live top-200 rank
    assert card.rank_day is None and card.cap == 8
    assert card.attack_stars.counts == (0, 0, 2, 2)


async def test_logs_fall_back_to_the_text_embed(season_db, monkeypatch, caplog):
    def broken(card):
        raise RuntimeError("no fonts")

    monkeypatch.setattr(cards, "render_legend_season", broken)
    with caplog.at_level(logging.ERROR):
        msg = await run_logs(season_db)
    assert "file" not in msg
    assert msg["embed"].image.url is None and "ATTACK" in msg["embed"].description  # text alone
    assert any("legend season card" in r.getMessage() for r in caplog.records)


async def test_logs_for_a_past_season_by_date(season_db):
    msg = await run_logs(season_db, season="2026-10-20")
    e = msg["embed"]  # the history view
    assert e.description.endswith("No legend days recorded for this season.")
    assert "Legend Season Logs (5 Oct – 2 Nov 2026)" in e.description and e.footer.text.startswith(
        "Day 28/28"
    )
    assert msg["file"].filename == "legend-season.png"  # an empty season still gets its card
    current = [b.item for b in msg["view"].children if b.item.style.name == "primary"]
    assert [b.label for b in current] == ["View Historical Data"]
    assert current[0].custom_id == f"lg:v:season:{TAG[1:]}:2026-11-01"  # the season's last day


async def test_logs_for_the_current_season_open_on_today(season_db):
    msg = await run_logs(season_db, season="2026-11-05")
    assert msg["embed"].author.name == "LEGEND I · TODAY"
