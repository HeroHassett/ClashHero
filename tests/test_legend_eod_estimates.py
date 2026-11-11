"""The day card's EOD bar and global ranks, Legend I star estimates for inferred battles, the
season history view, and the clan on the season card."""

import dataclasses
import io
import re
from datetime import UTC, datetime
from types import SimpleNamespace as NS

import pytest
from PIL import Image, ImageDraw

from clashhero.cogs import _legend_embeds as E
from clashhero.cogs import legends as cog
from clashhero.repos import legends as repo
from clashhero.repos import ranks
from clashhero.repos.legends import Hit
from clashhero.services import cards
from clashhero.services import icons as unit_icons
from clashhero.services.battlelog import estimate_legend_result
from clashhero.services.emojis import EMOJIS
from clashhero.utils.fmt import display_width
from clashhero.utils.seasons import season_by_id

B = cards.ArmyBattle
TAG = "#2PQ8VLYC0"
DAY, PREV, SEASON = "2026-11-07", "2026-11-06", "2026-11-02"
NOW = datetime(2026, 11, 7, 18, tzinfo=UTC)


def open_png(data: bytes) -> Image.Image:
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


def badge_png() -> bytes:
    im = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    ImageDraw.Draw(im).polygon(
        [(100, 8), (184, 40), (176, 130), (100, 192), (24, 130), (16, 40)], fill=(120, 60, 170)
    )
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


# ------------------------------------------------------------------ B. star estimates


@pytest.mark.parametrize(
    ("trophies", "result"),
    [
        (40, (3, 100)),
        (-40, (3, 100)),
        (39, None),  # 33-39: more than any 2-star can earn (99% = 32), less than a triple
        (33, None),
        (32, (2, 99)),  # 99%: two stars stay under 100
        (29, (2, 90)),  # 89-91% earn 29: the middle
        (-29, (2, 90)),
        (21, (2, 66)),
        (20, (2, 63)),  # 16-20 is two stars only (one star tops out at 15)
        (16, (2, 51)),
        (15, (1, 95)),  # 91-99%
        (5, (1, 5)),  # 1-9%
        (4, None),  # 0-4: not from the rule
        (0, None),
        (41, None),
    ],
)
def test_estimate_legend_result(trophies, result):
    assert estimate_legend_result(trophies) == result


def test_army_battles_estimate_only_inferred_legend_battles():
    hits = [
        Hit("defense", -29),
        Hit("defense", -3),  # not from the rule
        Hit("defense", -31, autofill=True),
        Hit("defense", -25, None, None, "#O", "Opp", 17, 2, 79),
    ]
    est = cog.army_battles(hits, armies=False, estimate=True)
    assert [(b.stars, b.destruction, b.estimated) for b in est] == [
        (2, 90, True),
        (None, None, False),
        (None, None, False),
        (2, 79, False),
    ]
    plain = cog.army_battles(hits, armies=False)
    assert [(b.stars, b.estimated) for b in plain][0] == (None, False)


def test_battle_lines_with_estimates():
    hits = [Hit("defense", -29), Hit("defense", -3), Hit("defense", -25, autofill=True)]
    assert E.battle_lines(hits, estimate=True) == [
        "-29  ★★☆  ~90%  inferred from trophies",
        " -3  ···   —    inferred from trophies",
        "-25  ···   —    auto-filled (no defense received)",
    ]
    assert E.battle_lines(hits)[0] == "-29  ···   —    inferred from trophies"  # below Legend I
    wide = E.battle_lines(
        [Hit("attack", 40), Hit("attack", 28, None, None, "#O", "Zed", 17, 2, 86)], estimate=True
    )
    assert wide == ["+40  ★★★  ~100%  inferred from trophies", "+28  ★★☆    86%  Zed"]  # still aligned


def test_estimated_rows_on_the_day_card():
    inferred = [B(trophies=-29)]
    estimated = cog.army_battles([Hit("defense", -29)], armies=False, estimate=True)
    kw = dict(player_name="Amy", day=DAY, today=True, trophies=4990, cap=8, attacks=[])
    a = open_png(cards.render_legend_day(**kw, defenses=inferred)).convert("RGB")
    b = open_png(cards.render_legend_day(**kw, defenses=estimated)).convert("RGB")
    x0, y0, x1, y1 = (round(v) for v in cards.day_slot_box(1, 0))
    stars = (x0 + 250, y0 + 4, x0 + 345, y1 - 4)  # the % and stars area
    assert a.crop(stars).tobytes() != b.crop(stars).tobytes()
    assert a.crop((x0 + 400, y0, x1, y1)).tobytes() == b.crop((x0 + 400, y0, x1, y1)).tobytes()  # trophies


# ------------------------------------------------------------------ A. the EOD bar and ranks


def test_bar_shows_eod_and_ranks():
    kw = dict(player_name="JoNico", day=DAY, today=True, trophies=4931, cap=8, attacks=[], defenses=[])
    plain = open_png(cards.render_legend_day(**kw)).convert("RGB")
    full = open_png(cards.render_legend_day(**kw, rank=7418, eod=5062, eod_rank=2451)).convert("RGB")
    no_rank = open_png(cards.render_legend_day(**kw, rank=7418, eod=5062)).convert("RGB")
    bar = tuple(round(v) for v in cards.DAY_BAR)
    left, middle = (bar[0], bar[1], 300, bar[3]), (360, bar[1], 640, bar[3])
    assert plain.crop(left).tobytes() != full.crop(left).tobytes()  # "#7418"
    assert plain.crop(middle).tobytes() != full.crop(middle).tobytes()  # "EOD: 5062 (#2451)"
    assert no_rank.crop(middle).tobytes() != full.crop(middle).tobytes()  # without "(#2451)"
    right = (680, bar[1], bar[2], bar[3])
    past = open_png(cards.render_legend_day(**{**kw, "today": False}, eod=5062)).convert("RGB")
    assert past.crop(right).tobytes() != full.crop(right).tobytes()  # "Day: Nov 07", not "Total: 4931"


def test_long_names_still_fit():
    im = open_png(
        cards.render_legend_day(
            player_name="戦Supercalifragilistic Expialidocious四" * 2,
            day=DAY,
            today=False,
            trophies=5027,
            cap=8,
            attacks=[],
            defenses=[],
            rank=199,
            eod=5027,
            eod_rank=4087,
        )
    )
    assert im.size == (1000, cards.legend_day_height(8))


async def _setup(db, monkeypatch, league="Legend I"):
    await db.execute(
        "INSERT INTO players (player_tag, name, town_hall, trophies, league, clan_tag, updated_at) "
        "VALUES (?, 'JoNico', 17, 4931, ?, NULL, 'x')",
        (TAG, league),
    )
    for day, start, end in ((PREV, 5100, 5062), (DAY, 5062, 4931)):
        await db.execute(
            "INSERT INTO legend_days (player_tag, legend_day, season_id, start_trophies, end_trophies) "
            "VALUES (?, ?, ?, ?, ?)",
            (TAG, day, SEASON, start, end),
        )
    await db.execute(
        "INSERT INTO legend_events (player_tag, legend_day, season_id, kind, trophies, trophies_after, "
        "occurred_at) VALUES (?, ?, ?, 'defense', -29, 5033, ?)",
        (TAG, DAY, SEASON, f"{DAY}T09:00:00+00:00"),
    )
    await ranks.store_rankings(db, [{"tag": TAG, "rank": 2451, "trophies": 5062}], PREV, NOW)
    await ranks.store_rankings(db, [{"tag": TAG, "rank": 7418, "trophies": 4931}], DAY, NOW)
    monkeypatch.setattr(cog, "_now", lambda: NOW)

    async def no_trophy():
        return None

    monkeypatch.setattr(unit_icons, "fetch_trophy_icon", no_trophy)


@pytest.fixture
def spy(monkeypatch):
    seen = {}
    real_day, real_week = cards.render_legend_day, cards.render_ranked_week

    def day(**kwargs):
        seen["day"] = kwargs
        return real_day(**kwargs)

    def week(**kwargs):
        seen["week"] = kwargs
        return real_week(**kwargs)

    monkeypatch.setattr(cards, "render_legend_day", day)
    monkeypatch.setattr(cards, "render_ranked_week", week)
    return seen


async def test_today_shows_the_previous_days_end(db, monkeypatch, spy):
    await _setup(db, monkeypatch)
    embed, file = await cog._day_reply(NS(db=db), TAG, DAY, None)
    assert embed is None and file.filename == "legend-day.png"
    kw = spy["day"]
    assert (kw["today"], kw["trophies"], kw["rank"]) == (True, 4931, 7418)
    assert (kw["eod"], kw["eod_rank"]) == (5062, 2451)  # yesterday's end and rank
    [inferred] = kw["defenses"]
    assert (inferred.stars, inferred.destruction, inferred.estimated) == (2, 90, True)


async def test_a_past_day_shows_its_own_end(db, monkeypatch, spy):
    await _setup(db, monkeypatch)
    await cog._day_reply(NS(db=db), TAG, PREV, None)
    kw = spy["day"]
    assert (kw["today"], kw["eod"], kw["eod_rank"], kw["rank"]) == (False, 5062, 2451, 7418)


async def test_no_ranks_outside_the_top_200(db, monkeypatch, spy):
    await _setup(db, monkeypatch)
    await db.execute("DELETE FROM player_ranks")
    await db.execute("DELETE FROM legend_day_ranks")
    await cog._day_reply(NS(db=db), TAG, DAY, None)
    kw = spy["day"]
    assert (kw["rank"], kw["eod"], kw["eod_rank"]) == (None, 5062, None)


async def test_weekly_tiers_get_the_rank_and_no_estimates(db, monkeypatch, spy):
    await _setup(db, monkeypatch, league="Legend II")
    await cog._day_reply(NS(db=db), TAG, DAY, None)
    kw = spy["week"]
    assert kw["rank"] == 7418
    assert [b.estimated for b in kw["defenses"]] == [False]


async def test_logs_today_lines_estimate_in_legend_one(db, monkeypatch):
    await _setup(db, monkeypatch)
    embed = await cog._logs_day_view(NS(db=db), TAG, DAY, None)
    assert "-29  ★★☆  ~90%  inferred from trophies" in embed.description


# ------------------------------------------------------------------ C. the season history view


def _summary():
    days = [
        {"player_tag": TAG, "legend_day": "2026-11-02", "start_trophies": 5000, "end_trophies": 5018},
        {"player_tag": TAG, "legend_day": "2026-11-03", "start_trophies": 5018, "end_trophies": 4975},
    ]

    def ev(day, kind, trophies, stars, battle_type=None):
        return {
            "player_tag": TAG,
            "legend_day": day,
            "kind": kind,
            "trophies": trophies,
            "occurred_at": f"{day}T10:00:00+00:00",
            "stars": stars,
            "battle_type": battle_type,
        }

    events = [
        *(ev("2026-11-02", "attack", 40, 3) for _ in range(5)),
        *(ev("2026-11-02", "attack", 28, 2) for _ in range(2)),
        ev("2026-11-02", "attack", 2, 0),  # not won
        *(ev("2026-11-02", "defense", -40, 3) for _ in range(3)),
        ev("2026-11-02", "defense", 0, 0),  # won
        ev("2026-11-03", "attack", 31, None),  # no star data: not counted
        ev("2026-11-03", "defense", -74, None, "autofill"),  # not a battle
    ]
    return repo.build_season(days, events)


def history(**kw):
    args = dict(
        name="K!NG ALVARO",
        tag=TAG,
        town_hall=18,
        league="Legend I",
        trophies=4949,
        window=season_by_id(SEASON),
        summary=_summary(),
        today=5,
        now=NOW,
    )
    return E.season_logs_embed(**{**args, **kw})


def test_history_view_layout():
    e = history()
    assert e.title == f"K!NG ALVARO ({TAG})" and e.url.endswith("2PQ8VLYC0")
    lines = e.description.split("\n")
    assert lines[0] == "Town Hall 18 · **4,949** trophies"  # plain text until the emojis are uploaded
    assert lines[1] == "**Legend Season Logs (2 Nov – 30 Nov 2026)**"
    assert lines[2] == "⚔️ **7** attacks won · 🛡️ **1** defense won"
    table = re.search(r"```\n(.*?)\n```", e.description, re.S).group(1).split("\n")
    assert table[0].split() == ["DAY", "ATK", "DEF", "+/-", "INIT", "FINAL"]
    assert table[1].split() == ["1", "+258⁸", "-120⁴", "+138", "5000", "5018"]
    assert table[2].split() == ["2", "+31¹", "-74¹", "-43", "5018", "4975"]
    assert len({display_width(line) for line in table}) == 1  # aligned columns
    assert e.footer.text == "Day 6/28 (2 Nov – 30 Nov 2026)" and e.timestamp == NOW


def test_history_view_symbols_and_a_finished_season(monkeypatch):
    monkeypatch.setattr(EMOJIS, "_by_name", {"th18": "<:th18:1>", "lt_legend_i": "<:lt_legend_i:2>"})
    e = history(today=None)
    assert e.description.split("\n")[0] == "<:th18:1> Town Hall 18 · <:lt_legend_i:2> **4,949** trophies"
    assert e.footer.text == "Day 28/28 (2 Nov – 30 Nov 2026)"


def test_history_view_keeps_the_newest_days():
    days = [
        {
            "player_tag": TAG,
            "legend_day": f"2026-11-{2 + i:02d}",
            "start_trophies": 5000,
            "end_trophies": 5001,
        }
        for i in range(28)
    ]
    summary = repo.build_season(days, [])
    e = history(summary=summary, limit=700)
    assert len(e.description) <= 700
    table = re.search(r"```\n(.*?)\n```", e.description, re.S).group(1).split("\n")
    assert table[0].split()[0] == "DAY" and table[-1].split()[0] == "28"  # the newest kept
    assert len(table) < 29


def test_history_view_without_days():
    e = history(summary=repo.SeasonSummary([]))
    assert e.description.endswith("No legend days recorded for this season.")
    assert "⚔️ **0** attacks won · 🛡️ **0** defenses won" in e.description


def test_season_span_across_years():
    assert E.season_span(season_by_id("2026-12-28")) == "28 Dec 2026 – 25 Jan 2027"


# ------------------------------------------------------------------ D. the clan on the season card


def _card(**kw):
    days = tuple(cards.SeasonPoint(datetime(2026, 11, 2 + i).date()) for i in range(28))
    base = cards.SeasonCard(
        player_name="K!NG ALVARO", player_tag=TAG, season="Nov 2 – Nov 30, 2026", days=days
    )
    return dataclasses.replace(base, **kw)


def test_the_clan_shows_over_the_name():
    plain = open_png(cards.render_legend_season(_card())).convert("RGB")
    with_clan = open_png(cards.render_legend_season(_card(clan_name="KiNG oF ClAn", clan_badge=badge_png())))
    name_only = open_png(cards.render_legend_season(_card(clan_name="KiNG oF ClAn"))).convert("RGB")
    eyebrow = (146, 36, 480, 66)  # right of the logo, over the player's name
    assert plain.crop(eyebrow).tobytes() != with_clan.convert("RGB").crop(eyebrow).tobytes()
    assert name_only.crop(eyebrow).tobytes() != with_clan.convert("RGB").crop(eyebrow).tobytes()
    badge = with_clan.convert("RGB").crop((146, 36, 172, 64))
    colours = [c for _, c in badge.getcolors(maxcolors=100_000)]
    assert any(r > 100 and b > 150 and g < 90 for r, g, b in colours)  # the purple badge
    long = _card(
        player_name="戦Supercalifragilistic Expialidocious" * 2, clan_name="龍の騎士団 of the north" * 3
    )
    assert open_png(cards.render_legend_season(long)).size == (1000, cards.season_height(long))


def test_no_clan_leaves_the_card_unchanged():
    assert cards.render_legend_season(_card()) == cards.render_legend_season(
        _card(clan_name=None, clan_badge=None)
    )
    assert cards.render_legend_season(_card()) == cards.render_legend_season(_card(clan_badge=badge_png()))


async def test_player_clan_from_the_stored_clan(db, monkeypatch):
    url = "https://api-assets.clashofclans.com/badges/200/x.png"
    await db.execute(
        "INSERT INTO clans (clan_tag, name, badge_url, updated_at) VALUES ('#CLAN', 'KiNG oF ClAn', ?, 'x')",
        (url,),
    )
    asked = []

    async def badges(urls):
        urls = list(urls)
        asked.extend(urls)
        return {u: b"png" for u in urls}

    monkeypatch.setattr(cards, "fetch_badges", badges)
    assert await cog._player_clan(NS(db=db), {"clan_tag": "#CLAN"}) == ("KiNG oF ClAn", b"png")
    assert asked == [url]
    assert await cog._player_clan(NS(db=db), {"clan_tag": None}) == (None, None)
    assert await cog._player_clan(NS(db=db), None) == (None, None)


async def test_player_clan_from_the_api_and_failing_soft(db, monkeypatch):
    async def badges(urls):
        return dict.fromkeys(urls)  # the badge couldn't be downloaded

    monkeypatch.setattr(cards, "fetch_badges", badges)

    class Coc:
        async def get_clan(self, tag):
            return NS(name="Night Owls", badge=NS(medium="https://api-assets.clashofclans.com/b.png"))

    assert await cog._player_clan(NS(db=db, coc=Coc()), {"clan_tag": "#OWLS"}) == ("Night Owls", None)

    class Broken:
        async def get_clan(self, tag):
            raise RuntimeError("API down")

    assert await cog._player_clan(NS(db=db, coc=Broken()), {"clan_tag": "#OWLS"}) == (None, None)


async def test_the_season_view_shows_the_clan(db, monkeypatch):
    await _setup(db, monkeypatch)
    await db.execute("UPDATE players SET clan_tag = '#CLAN' WHERE player_tag = ?", (TAG,))
    await db.execute(
        "INSERT INTO clans (clan_tag, name, badge_url, updated_at) VALUES "
        "('#CLAN', 'KiNG oF ClAn', 'https://api-assets.clashofclans.com/b.png', 'x')"
    )

    async def badges(urls):
        return {u: badge_png() for u in urls}

    seen = {}
    real = cards.render_legend_season

    def render(card):
        seen["card"] = card
        return real(card)

    monkeypatch.setattr(cards, "fetch_badges", badges)
    monkeypatch.setattr(cards, "render_legend_season", render)
    embed, file = await cog.render_legend_view(NS(db=db), None, "season", TAG, DAY)
    assert file.filename == "legend-season.png" and seen["card"].clan_name == "KiNG oF ClAn"
    assert seen["card"].clan_badge == badge_png()
    assert embed.title == f"JoNico ({TAG})"
