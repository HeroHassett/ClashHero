"""/legends logs details: hero, equipment and army usage over a season, the day strip, the taller
season card, and the view buttons keeping it."""

import io
from datetime import UTC, datetime

import discord
import pytest
from PIL import Image

from clashhero.cogs import legends as cog
from clashhero.repos.legends import DayStats, Hit, SeasonDay, SeasonSummary
from clashhero.services import cards
from clashhero.services import icons as unit_icons
from clashhero.services.army import common_army, hero_usage, parse_army
from clashhero.utils.seasons import season_by_id

TAG = "#2PQ8VLYC0"
DAY = "2026-11-07"
SEASON = "2026-11-02"
NOW = datetime(2026, 11, 7, 18, tzinfo=UTC)

# Archer Queen (1) with pet 10 and equipment 32 + 14; Barbarian King (0) with equipment 3 + 9.
MAIN = "h1p10e32_14-0e3_9u5x7-3x28s3x35i1x9d1x5"
SWAPPED = "h1p10e14_32-0e3_9u5x7-3x28s3x35i1x9"  # same gear, other order; another clan castle
OTHER = "h1p11e32_15u4x7-3x28s3x35"  # one more troop count off, other pet and gear, no King


def armies(*codes):
    return [parse_army(c) for c in codes]


def open_png(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


# ------------------------------------------------------------------ usage


def test_hero_usage_counts_heroes_gear_and_pets():
    queen, king = hero_usage(armies(MAIN, SWAPPED, OTHER))
    assert (queen.hero.id, queen.uses) == (1, 3)
    assert sorted(e.id for e in queen.equipment) == [14, 32]
    assert queen.equipment_uses == 2  # the order of the pair doesn't matter
    assert [e.id for e in queen.equipment] == [14, 32]  # shown as last brought
    assert (queen.pet.id, queen.pet_uses) == (10, 2)
    assert (king.hero.id, king.uses, king.pet, king.pet_uses) == (0, 2, None, 0)


def test_hero_usage_ties_go_to_the_latest_and_heroes_sort_by_use():
    (queen,) = hero_usage(armies("h1e32_14", "h1e32_15"))
    assert sorted(e.id for e in queen.equipment) == [15, 32] and queen.equipment_uses == 1
    order = [u.hero.id for u in hero_usage(armies("h2-0", "h0-2-1", "h0"))]
    assert order == [0, 2, 1]  # most used first, ties by hero
    assert hero_usage([]) == []


def test_common_army_matches_exact_troops_and_spells():
    usage = common_army(armies(MAIN, SWAPPED, OTHER))
    assert usage.uses == 2
    assert [(u.id, u.count) for u in usage.army.troops] == [(7, 5), (28, 3)]
    assert [(u.id, u.count) for u in usage.army.spells] == [(35, 3)]
    assert usage.army.heroes == []  # heroes have their own panel
    # The clan castle varies (it's donated): the most common one among those attacks, latest on a tie.
    assert [(u.id, u.count) for u in usage.army.cc_troops] == [(9, 1)]
    assert usage.army.cc_spells == []


def test_common_army_without_troops_or_spells():
    assert common_army([]) is None
    assert common_army(armies("h1p10e32_14")) is None


# ------------------------------------------------------------------ the details from a season


def season(*days):
    return SeasonSummary(list(days))


def day(d, attacks=(), defenses=(), codes=()):
    hits = [
        Hit("attack", t, f"{d}T10:00:00+00:00", army_share_code=c)
        for t, c in zip(attacks, codes, strict=False)
    ]
    hits += [Hit("attack", t) for t in attacks[len(hits) :]]
    stats = DayStats(TAG, list(attacks), list(defenses), 8, hits, [Hit("defense", t) for t in defenses])
    return SeasonDay(d, None, None, stats)


def test_season_details_armies():
    summary = season(
        day("2026-11-05", [40, 32], [-40], codes=[MAIN, ""]),
        day("2026-11-06"),
        day(DAY, [40], [-16, -5], codes=[SWAPPED]),
    )
    details = cog.season_details(summary)
    assert details.attacks == 2  # only attacks with an army code
    assert details.heroes[0].uses == 2 and details.army.uses == 2
    assert {"Archer Queen", "Barbarian King"} <= details.icon_names()
    empty = cog.season_details(season(day(DAY, [40])))
    assert (empty.attacks, empty.heroes, empty.army) == (0, (), None)


# ------------------------------------------------------------------ the card


def card(details=None):
    summary = season(
        day("2026-11-05", [40, 32], [-40], codes=[MAIN, OTHER]), day(DAY, [40], [-16], codes=[MAIN])
    )
    base = cog.season_card(name="JoNico", tag=TAG, window=season_by_id(SEASON), summary=summary, now=NOW)
    if details is True:
        details = cog.season_details(summary)
    return base if details is None else cards.SeasonCard(**{**base.__dict__, "details": details})


def test_the_detailed_card_is_taller():
    plain, detailed = card(), card(True)
    height = cards.season_height(detailed)
    assert height > cards.season_height(plain)
    im = open_png(cards.render_legend_season(detailed))
    assert im.size == (cards.WIDTH, height)
    # Same top: the details only add panels under the star breakdowns.
    top = (0, 0, cards.WIDTH, cards.stars_top(plain) + cards.STARS_H)
    plain_im = open_png(cards.render_legend_season(plain)).convert("RGB")
    assert plain_im.crop(top).tobytes() == im.convert("RGB").crop(top).tobytes()


def test_details_without_armies():
    empty = card(cards.SeasonDetails())
    im = open_png(cards.render_legend_season(empty))
    expected = cards.season_height(card()) + cards.DETAILS_GAP + cards.HEROES_EMPTY_H
    assert im.size == (cards.WIDTH, expected) == (cards.WIDTH, cards.season_height(empty))


def code(troops=0, spells=0, cc_troops=0, cc_spells=0):
    """An army code with that many kinds of each (ids from 0)."""
    parts = []
    for letter, n in (("u", troops), ("s", spells), ("i", cc_troops), ("d", cc_spells)):
        if n:
            parts.append(letter + "-".join(f"1x{i}" for i in range(n)))
    return "".join(parts)


def test_a_full_army_fits_two_rows():
    columns, rows = cards.army_columns(parse_army(code(13, 4, 6, 2)))
    assert rows == 2
    assert [(c.label, len(c.units), c.cols) for c in columns] == [
        ("TROOPS", 13, 7),
        ("SPELLS", 4, 2),
        ("CLAN CASTLE", 8, 4),
    ]
    dividers = cards.ARMY_DIVIDER * (len(columns) - 1)
    assert sum(c.width for c in columns) + dividers == pytest.approx(cards.DETAILS_W)
    assert all(c.width >= c.cols * cards.ARMY_CELL_W for c in columns)


def test_spare_cells_widen_a_column_to_save_it_a_row():
    columns, rows = cards.army_columns(parse_army(code(6, 5, 4)))
    assert rows == 2  # the clan castle can't fit one row beside the others
    assert [c.cols for c in columns] == [6, 5, 2]  # troops and spells each fill a single row


def test_a_small_army_is_one_row_and_empty_columns_are_left_out():
    columns, rows = cards.army_columns(parse_army(MAIN))
    assert rows == 1 and [c.label for c in columns] == ["TROOPS", "SPELLS", "CLAN CASTLE"]
    spells_only, rows = cards.army_columns(parse_army("s3x35"))
    assert rows == 1 and [c.label for c in spells_only] == ["SPELLS"]
    assert cards.army_columns(parse_army("h1p10e32_14")) == ([], 0)


def test_unit_names_under_the_tiles():
    totem, freeze = parse_army("s3x35-1x1").spells
    assert cards._army_unit_name(totem) == totem.name.removesuffix(" Spell") != totem.name
    fonts = cards._text_fonts(10, 500)
    room = cards._px(cards.ARMY_CELL_W - 2)
    text = lambda lines: [" ".join(s for s, _ in runs) for runs in lines]  # noqa: E731
    assert text(cards._name_lines("Druid", fonts, room)) == ["Druid"]
    assert text(cards._name_lines("Super Wall Breaker", fonts, room)) == ["Super Wall", "Breaker"]
    assert text(cards._name_lines("Headhunter", fonts, room)) == ["Headhunter"]  # the longest word


def test_the_army_row_grows_with_its_rows():
    one = card(cards.SeasonDetails(army=common_army(armies(MAIN))))
    two = card(cards.SeasonDetails(army=common_army(armies(code(13, 4, 6, 2)))))
    step = cards.ARMY_CELL_H + cards.ARMY_ROW_GAP
    assert cards.season_height(two) - cards.season_height(one) == step
    for c in (one, two):
        assert open_png(cards.render_legend_season(c)).size == (cards.WIDTH, cards.season_height(c))


def test_unknown_units_and_long_names_still_draw():
    details = cog.season_details(season(day(DAY, [40], codes=["h1p999e998_997u5x998s2x999"])))
    assert details.heroes[0].equipment[0].name is None
    open_png(cards.render_legend_season(card(details))).load()


# ------------------------------------------------------------------ the option and the buttons


def test_view_ids_carry_details():
    cid = cog.legend_view_id("season", TAG, DAY, details=True)
    assert cid == "lg:v:season:2PQ8VLYC0:2026-11-07:d"
    assert cog.parse_legend_view_id(cid) == ("season", TAG, DAY, True)
    assert cog.parse_legend_view_id(cid[:-2]) == ("season", TAG, DAY, False)  # buttons sent before
    assert cog.LegendViewButton.__discord_ui_compiled_template__.fullmatch(cid)
    assert cog.parse_legend_view_id(cid + "x") is None


def test_every_button_keeps_details():
    row = cog.legend_view_row("day", TAG, DAY, details=True)
    ids = [child.item.custom_id for child in row.children]
    assert all(i.endswith(":d") for i in ids) and len(ids) == 4
    assert all(child.details for child in row.children)


async def test_render_legend_view_draws_the_details(db, monkeypatch):
    await db.execute(
        "INSERT INTO players (player_tag, name, town_hall, trophies, league, clan_tag, updated_at) "
        "VALUES (?, 'JoNico', 17, 5100, 'Legend I', NULL, 'x')",
        (TAG,),
    )
    await db.execute(
        "INSERT INTO legend_days (player_tag, legend_day, season_id, start_trophies, end_trophies) "
        "VALUES (?, ?, ?, 5060, NULL)",
        (TAG, DAY, SEASON),
    )
    await db.execute(
        "INSERT INTO legend_events (player_tag, legend_day, season_id, kind, trophies, trophies_after, "
        "occurred_at, stars, destruction, army_share_code, battle_type) "
        "VALUES (?, ?, ?, 'attack', 40, 5100, ?, 3, 100, ?, 'legend')",
        (TAG, DAY, SEASON, f"{DAY}T09:00:00+00:00", MAIN),
    )
    monkeypatch.setattr(cog, "_now", lambda: NOW)
    asked = {}

    async def icons(names):
        asked["names"] = set(names)
        return {}

    seen = {}
    real = cards.render_legend_season

    def render(c):
        seen["card"] = c
        return real(c)

    monkeypatch.setattr(unit_icons, "fetch_icons", icons)
    monkeypatch.setattr(cards, "render_legend_season", render)
    _, file = await cog.render_legend_view(_Bot(db), None, "day", TAG, DAY, details=True)
    assert isinstance(file, discord.File)
    details = seen["card"].details
    assert details.attacks == 1 and seen["card"].cap == 8 and seen["card"].shown[-1].attacks == 1
    assert "Archer Queen" in asked["names"]
    await cog.render_legend_view(_Bot(db), None, "season", TAG, DAY)
    assert seen["card"].details is None


class _Bot:
    def __init__(self, db):
        self.db = db
