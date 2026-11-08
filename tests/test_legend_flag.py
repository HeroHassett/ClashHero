"""The player's country flag at the top left of the battle-log cards, as the game shows it."""

import io
from datetime import UTC, datetime
from types import SimpleNamespace as NS

import pytest
from PIL import Image

from clashhero.cogs import legends as cog
from clashhero.repos import ranks
from clashhero.services import cards, country_flags
from clashhero.services import icons as unit_icons

TAG = "#2PQ8VLYC0"
DAY, SEASON = "2026-11-07", "2026-11-02"
NOW = datetime(2026, 11, 7, 18, tzinfo=UTC)
US = {"id": 32000249, "name": "United States", "countryCode": "US"}


def flag_png(color=(200, 30, 40), size=(80, 42)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def open_rgb(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im.load()
    return im.convert("RGB")


def day_card(**kw) -> bytes:
    args = dict(player_name="JoNico", day=DAY, today=True, trophies=4931, cap=8, attacks=[], defenses=[])
    return cards.render_legend_day(**{**args, **kw})


def week_card(**kw) -> bytes:
    args = dict(player_name="JoNico", trophies=975, cap=30, attacks=[], defenses=[])
    return cards.render_ranked_week(**{**args, **kw})


def _near(c, target, tolerance=12):
    return all(abs(a - b) <= tolerance for a, b in zip(c, target, strict=True))


# ------------------------------------------------------------------ the cards


@pytest.mark.parametrize("render", [day_card, week_card])
def test_the_flag_is_drawn_left_of_the_tabs(render):
    cx, cy = (round(v) for v in cards.FLAG_CENTER)
    plain = open_rgb(render())
    flagged = open_rgb(render(flag=flag_png()))
    assert _near(plain.getpixel((cx, cy)), cards.GW_FRAME)  # nothing there without a flag
    assert _near(flagged.getpixel((cx, cy)), (200, 30, 40))
    assert cards.GW_EDGE < cx - cards.FLAG_MAX[0] / 2 and cx + cards.FLAG_MAX[0] / 2 < cards.GW_TAB_X0
    # Only the flag's corner of the image changes.
    rest = (int(cards.GW_TAB_X0), 0, 1000, plain.height)
    assert plain.crop(rest).tobytes() == flagged.crop(rest).tobytes()


def test_no_flag_or_an_unreadable_one_changes_nothing():
    plain = day_card()
    assert day_card(flag=None) == plain
    assert day_card(flag=b"not an image") == plain
    assert week_card(flag=b"") == week_card()


def test_light_flags_keep_an_outline_and_their_shape():
    im = open_rgb(day_card(flag=flag_png((255, 255, 255), (80, 40))))  # a white 2:1 flag
    cx, cy = (round(v) for v in cards.FLAG_CENTER)
    assert _near(im.getpixel((cx, cy)), (255, 255, 255), 6)
    left = round(cards.FLAG_CENTER[0] - cards.FLAG_MAX[0] / 2) - 1  # its 36 px width fills the box
    assert sum(im.getpixel((left, cy))) < 3 * 110  # the dark outline beside it
    # 36 x 18 keeps the 2:1 shape: 11 px above the centre is frame, not flag (a stretched 36 x 24
    # flag would reach it).
    assert not _near(im.getpixel((cx, round(cards.FLAG_CENTER[1] - 11))), (255, 255, 255), 40)


# ------------------------------------------------------------------ the cog


async def _setup(db, monkeypatch, league="Legend I"):
    await db.execute(
        "INSERT INTO players (player_tag, name, town_hall, trophies, league, clan_tag, updated_at) "
        "VALUES (?, 'JoNico', 17, 4931, ?, NULL, 'x')",
        (TAG, league),
    )
    await db.execute(
        "INSERT INTO legend_days (player_tag, legend_day, season_id, start_trophies, end_trophies) "
        "VALUES (?, ?, ?, 5062, 4931)",
        (TAG, DAY, SEASON),
    )
    monkeypatch.setattr(cog, "_now", lambda: NOW)

    async def no_trophy():
        return None

    monkeypatch.setattr(unit_icons, "fetch_trophy_icon", no_trophy)


@pytest.fixture
def spy(monkeypatch):
    seen = {"fetched": []}
    real_day, real_week = cards.render_legend_day, cards.render_ranked_week

    def day(**kwargs):
        seen["day"] = kwargs
        return real_day(**kwargs)

    def week(**kwargs):
        seen["week"] = kwargs
        return real_week(**kwargs)

    async def fetch(code):
        seen["fetched"].append(code)
        return flag_png()

    monkeypatch.setattr(cards, "render_legend_day", day)
    monkeypatch.setattr(cards, "render_ranked_week", week)
    monkeypatch.setattr(country_flags, "fetch_flag", fetch)
    return seen


async def test_the_day_card_gets_the_flag_of_the_players_country(db, monkeypatch, spy):
    await _setup(db, monkeypatch)
    await ranks.store_country_rankings(db, US, [{"tag": TAG, "rank": 57, "trophies": 4931}], NOW)
    embed, file = await cog._day_reply(NS(db=db), TAG, DAY, None)
    assert embed is None and file.filename == "legend-day.png"
    assert spy["fetched"] == ["US"] and spy["day"]["flag"] == flag_png()
    assert spy["day"]["rank"] is None  # the bar's rank stays global; 57 is the country rank


async def test_no_country_rank_means_no_flag(db, monkeypatch, spy):
    await _setup(db, monkeypatch)
    await cog._day_reply(NS(db=db), TAG, DAY, None)
    assert spy["fetched"] == [] and spy["day"]["flag"] is None


@pytest.mark.parametrize("failure", ["none", "raises"])
async def test_flag_failures_never_fail_the_reply(db, monkeypatch, spy, failure):
    await _setup(db, monkeypatch)
    await ranks.store_country_rankings(db, US, [{"tag": TAG, "rank": 57, "trophies": 4931}], NOW)

    async def fetch(code):
        if failure == "raises":
            raise OSError("flagcdn is down")
        return None

    monkeypatch.setattr(country_flags, "fetch_flag", fetch)
    embed, file = await cog._day_reply(NS(db=db), TAG, DAY, None)
    assert embed is None and file.filename == "legend-day.png" and spy["day"]["flag"] is None


async def test_the_weekly_grid_gets_the_flag(db, monkeypatch, spy):
    await _setup(db, monkeypatch, league="Legend II")
    await ranks.store_country_rankings(db, US, [{"tag": TAG, "rank": 57, "trophies": 975}], NOW)
    _, file = await cog._day_reply(NS(db=db), TAG, DAY, None)
    assert file.filename == "ranked-week.png" and spy["week"]["flag"] == flag_png()


class FakeResponse:
    async def defer(self, **kwargs):
        pass


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)


async def test_legends_week_passes_the_flag(db, monkeypatch, spy):
    await _setup(db, monkeypatch, league="Legend III")
    await ranks.store_country_rankings(db, US, [{"tag": TAG, "rank": 57, "trophies": 975}], NOW)
    interaction = NS(
        client=NS(db=db), guild_id=None, user=NS(id=1), response=FakeResponse(), followup=FakeFollowup()
    )
    await cog.Legends.legends_week.callback(cog.Legends(NS(db=db)), interaction, TAG, None, 0)
    [msg] = interaction.followup.sent
    assert msg["file"].filename == "ranked-week.png" and spy["week"]["flag"] == flag_png()
