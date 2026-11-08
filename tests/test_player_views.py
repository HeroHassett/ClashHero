"""Player pages (/player info, units, upgrades, rushed) and their page buttons."""

import re
from types import SimpleNamespace as NS

import discord
import pytest
from coc import (
    DARK_ELIXIR_TROOP_ORDER,
    ELIXIR_TROOP_ORDER,
    EQUIPMENT,
    PETS_ORDER,
    SIEGE_MACHINE_ORDER,
    SPELL_ORDER,
)

from clashhero.cogs._info_embeds import (
    MORE_NOT_SHOWN,
    UnitLevel,
    chip_grid,
    group_units,
    pack_blocks,
    parse_player_page_id,
    percent,
    player_page_id,
    profile_parts,
    rush_report,
    rushed_blocks,
    units_blocks,
    upgrades_blocks,
)
from clashhero.cogs.info import PlayerPageButton, player_page_view
from clashhero.utils.fmt import display_width

# ------------------------------------------------------------------ custom ids


def test_custom_id_round_trip():
    for page in ("profile", "units", "upgrades", "rushed"):
        for refresh in (False, True):
            cid = player_page_id(page, "#2PP0JCCL", refresh=refresh)
            assert len(cid) <= 100
            assert parse_player_page_id(cid) == (page, "#2PP0JCCL", refresh)
    assert player_page_id("units", "#2pp") == "pl:units:2PP"
    assert player_page_id("rushed", "2PP", refresh=True) == "pl:rushed:2PP:r"


@pytest.mark.parametrize(
    "custom_id",
    ["pl:army:2PP", "pl:units:#2PP", "pl:units:ABC", "pl:units:2PP:x", "pl:units:", "roster:refresh:1"],
)
def test_custom_id_rejects_others(custom_id):
    assert parse_player_page_id(custom_id) is None
    assert PlayerPageButton.__discord_ui_compiled_template__.fullmatch(custom_id) is None


def test_custom_id_unknown_page():
    with pytest.raises(ValueError):
        player_page_id("army", "#2PP")


async def test_button_rebuilt_from_custom_id():
    for cid in ("pl:upgrades:2PP0JCCL", "pl:profile:2PP0JCCL:r"):
        match = PlayerPageButton.__discord_ui_compiled_template__.fullmatch(cid)
        button = await PlayerPageButton.from_custom_id(None, None, match)
        assert button.item.custom_id == cid
        assert (button.page, button.tag) == (cid.split(":")[1], "#2PP0JCCL")


async def test_page_buttons_highlight_current():
    view = player_page_view("units", "#2PP0JCCL")
    buttons = [child.item for child in view.children]
    assert [b.label for b in buttons] == ["Profile", "Units", "Upgrades", "Rushed", "↻"]
    assert len({b.custom_id for b in buttons}) == 5  # unique within the message
    current = buttons[1]
    assert current.disabled and current.style is discord.ButtonStyle.primary
    assert not any(b.disabled for i, b in enumerate(buttons) if i != 1)
    assert buttons[4].custom_id == "pl:units:2PP0JCCL:r"
    assert view.timeout is None and view.is_persistent()


# ------------------------------------------------------------------ grids and grouping


def test_chip_grid_lines_up_columns():
    cells = [
        ("Barbarian", "13/13", "✓"),
        ("Archer", "12/13", ""),
        ("Wall Breaker", "9/13", ""),
        ("Giant", "13/13", "✓"),
        ("Goblin", "9/9", "✓"),
    ]
    # Three per row fit in 52 columns; each column is padded to its own widest chip.
    assert chip_grid(cells, columns=52) == [
        "`Barbarian 13/13✓` `Archer 12/13 ` `Wall Breaker 9/13`",
        "`Giant     13/13✓` `Goblin   9/9✓`",
    ]
    # Narrower: two per row.
    assert chip_grid(cells, columns=40) == [
        "`Barbarian    13/13✓` `Archer 12/13 `",
        "`Wall Breaker  9/13 ` `Giant  13/13✓`",
        "`Goblin         9/9✓`",
    ]


def test_chip_grid_fits_columns():
    cells = [(f"Unit {i}", f"{i}/9", "") for i in range(7)]
    assert len(chip_grid(cells, columns=40)) == 3  # 3 per row fit
    assert len(chip_grid(cells, columns=20)) == 7  # one per row
    for row in chip_grid(cells, columns=40):
        assert display_width(row.replace("`", "")) <= 40
    assert chip_grid([]) == []


def test_group_units_splits_dark_troops_in_game_order():
    units = [
        UnitLevel("Hog Rider", "troops", 5, 10, 9),
        UnitLevel("Brand New Troop", "troops", 1, 3, None),
        UnitLevel("Rage Spell", "spells", 5, 6, 6),
        UnitLevel("Archer", "troops", 5, 10, 9),
        UnitLevel("Minion", "troops", 5, 10, 9),
        UnitLevel("Barbarian", "troops", 5, 10, 9),
    ]
    groups = dict(group_units(units))
    assert list(groups) == ["troops", "dark", "spells"]
    assert [u.name for u in groups["troops"]] == ["Barbarian", "Archer", "Brand New Troop"]
    assert [u.name for u in groups["dark"]] == ["Minion", "Hog Rider"]


# ------------------------------------------------------------------ pages


def sample():
    return [
        UnitLevel("Barbarian", "troops", 13, 13, 12),  # maxed
        UnitLevel("Archer", "troops", 10, 13, 12),  # rushed
        UnitLevel("Minion", "troops", 12, 13, 11),  # 1 to go, not rushed
        UnitLevel("Rage Spell", "spells", 6, 6, 6),
        UnitLevel("Archer Queen", "heroes", 80, 100, 95),  # rushed by 15
        UnitLevel("Giant Gauntlet", "equipment", 5, 27, 27),  # never counted as rushed
        UnitLevel("Brand New Troop", "troops", 2, 5, None, approximate=True),
    ]


def test_units_page():
    text = "\n".join(line for block in units_blocks(sample(), 17) for line in block)
    assert "`Barbarian 13/13✓` `Archer 10/13` `Brand New Troop 2/~5`" in text  # ~: approximate max
    assert "🟣 **Troops** · 1/3 maxed" in text and "⚫ **Dark Troops** · 0/1 maxed" in text
    assert "`Rage 6/6✓`" in text  # spells drop the suffix
    assert "`~`" in text  # approximate note


def test_upgrades_page():
    blocks = upgrades_blocks(sample(), 17)
    text = "\n".join(line for block in blocks for line in block)
    assert "Barbarian" not in text and "Rage" not in text  # maxed units are left out
    assert "`Archer          10/13` `R`" in text
    assert "`Minion 12/13`\n" in text  # not rushed: no R
    assert "`Giant Gauntlet 5/27`\n" in text  # equipment: no R
    assert "`Brand New Troop  2/~5`\n⏱️ 2 upgrades · 6 levels · 80% done" in text  # 25/31 levels
    assert blocks[-2][0].endswith("of TH17 max levels done")  # overall % at the bottom
    assert "`R` rushed: below the TH16 max" in blocks[-1][0]
    maxed = upgrades_blocks([UnitLevel("Barbarian", "troops", 13, 13, 12)], 17)
    assert "Everything is maxed for TH17" in maxed[0][1]


def test_rush_report_percentages():
    r = rush_report(sample())
    assert [u.name for u in r.rushed] == ["Archer", "Archer Queen"]
    assert (r.lab.done, r.lab.total) == (2, 12 + 12 + 11 + 6)
    assert (r.heroes.done, r.heroes.total) == (15, 95)
    assert (r.overall.done, r.overall.total) == (17, 41 + 95)
    assert r.skipped == 1  # Brand New Troop: previous-TH max unknown
    top = rushed_blocks(sample(), 17)[0]
    assert top[1] == "🧪 Lab `4.9%` · 👑 Heroes `15.8%` · 📊 Overall `12.5%`"
    assert "Equipment isn't counted" in top[2]
    fine = rushed_blocks([UnitLevel("Barbarian", "troops", 12, 13, 12)], 17)
    assert fine[0][0] == "✅ **Not rushed** 🎉" and len(fine) == 1


def test_rushed_grids_show_previous_max():
    blocks = rushed_blocks(sample(), 17)
    text = "\n".join(line for block in blocks[1:] for line in block)
    assert "`Archer 10/12`" in text and "`Archer Queen 80/95`" in text
    assert "Minion" not in text and "Giant Gauntlet" not in text


def test_percent_rounding():
    assert percent(999, 1000) == "99.9%"  # never shows 100% before it's done
    assert percent(1, 3000, round_up=True) == "0.1%"  # never shows 0% when rushed
    assert percent(1, 3, digits=0) == "33%"
    assert percent(0, 0) == "0.0%" and percent(0, 0, empty=100) == "100.0%"


# ------------------------------------------------------------------ Discord limits


def full_account(max_level=100):
    names = [
        *((n, "troops") for n in [*ELIXIR_TROOP_ORDER, *DARK_ELIXIR_TROOP_ORDER, *SIEGE_MACHINE_ORDER]),
        *((n, "spells") for n in SPELL_ORDER),
        *((n, "pets") for n in PETS_ORDER),
        *((n, "equipment") for n in EQUIPMENT),
        *((n, "heroes") for n in ("Barbarian King", "Archer Queen", "Minion Prince", "Grand Warden")),
    ]
    out = []
    for name, cat in names:
        cat = "sieges" if name in SIEGE_MACHINE_ORDER else cat
        out.append(UnitLevel(name, cat, max_level // 3, max_level, max_level - 1))
    return out


def test_pages_fit_one_embed_for_a_whole_account():
    units = full_account()
    for builder in (units_blocks, upgrades_blocks, rushed_blocks):
        pages = pack_blocks(builder(units, 17))
        assert len(pages) == 1 and len(pages[0]) <= 4096, builder.__name__
        assert MORE_NOT_SHOWN not in pages[0]


def test_pack_blocks_splits_under_limits():
    blocks = [[f"**Block {b}**", *(f"line {b}.{i} " + "x" * 40 for i in range(20))] for b in range(10)]
    pages = pack_blocks(blocks, limit=1000, budget=5000)
    assert all(len(p) <= 1000 for p in pages)
    assert sum(len(p) for p in pages) <= 5000
    assert pages[-1].endswith(MORE_NOT_SHOWN)  # 10 blocks of ~1000 chars don't fit in 5000
    small = pack_blocks([["a", "b"], ["c"]])
    assert small == ["a\nb\n\nc"]
    # A block that fits isn't split across embeds; the next block starts a new one.
    two = pack_blocks([["x" * 600], ["y" * 600]], limit=1000)
    assert two == ["x" * 600, "y" * 600]
    assert pack_blocks([]) == [""]


def profile_player(**extra):
    raw = {
        "heroes": [{"name": "Archer Queen", "level": 95, "village": "home"}],
        "achievements": [
            {"name": n, "value": v}
            for n, v in {
                "Gold Grab": 2_000_000_000,
                "Elixir Escapade": 1_250_000_000,
                "Heroic Heist": 74_451_474,
                "Friend in Need": 385_903,
                "Sharing is caring": 18_282,
                "Siege Sharer": 6_128,
                "Conqueror": 11_137,
                "Unbreakable": 746,
                "War League Legend": 1_150,
                "Games Champion": 166_260,
                "Well Seasoned": 320_040,
                "Aggressive Capitalism": 3_910_515,
                "Most Valuable Clanmate": 5_160_621,
            }.items()
        ],
    }
    fields = dict(
        name="Alex", tag="#2PP", town_hall=18, town_hall_weapon=None, exp_level=239, trophies=4961,
        best_trophies=5249, war_stars=685, war_opted_in=True, clan=NS(name="Best_Clan", tag="#2QQ"),
        role=NS(value="admin"), attack_wins=0, defense_wins=0, clan_capital_contributions=5_160_621,
        _raw_data=raw,
    )  # fmt: skip
    return NS(**{**fields, **extra})


def test_profile_one_stat_per_line():
    desc, fields = profile_parts(
        profile_player(), discord_id=42, verified=True, season=(4, 118), names=["Old_one"]
    )
    assert fields == []  # everything is in the description: no inline columns to wrap
    lines = desc.splitlines()
    assert lines[0] == "TH **18** XP **239** 🏆 **4,961** ⭐ **685**"
    for expected in (
        "**Season Stats**",
        "🏰 **Donated** 4",
        "🏰 **Received** 118",
        "🗡️ **Attacks Won** 0",
        "🏆 **Best Trophies** 5,249 (all-time high)",
        "**Achievement Stats**",
        "**Total Loot** Gold 2B+ Elixir 1.2B DE 74.5M",
        "🏰 **Troops Donated** 385,903",
        "🧪 **Spells Donated** 18,282",
        "🪵 **Siege Donated** 6,128",
        "🗡️ **Attacks Won** 11,137",
        "🛡️ **Defense Won** 746",
        "⭐ **CWL War Stars** 1,150",
        "🎯 **Clan Games Points** 166,260",
        "🏰 **Capital Gold Looted** 3,910,515",
        "🏰 **Capital Gold Contributed** 5,160,621",
        "🔗 **Discord** <@42> ✅",
        "**Previous Names** Old\\_one",
    ):
        assert expected in lines, expected
    assert "[Best\\_Clan](" in desc and "(Elder)" in desc
    assert len(desc) <= 4096
    # No line long enough to wrap on a phone (emoji markup aside).
    plain = [re.sub(r"<a?:\w+:\d+>|\[|\]\([^)]*\)", "", line) for line in lines]
    assert max(len(line) for line in plain if not line.startswith("**Heroes")) <= 45


def test_profile_uses_game_symbols_once_uploaded(monkeypatch):
    from clashhero.services.emojis import EMOJIS

    icons = (
        "ui_donate_troops ui_donate_spells ui_donate_sieges ui_sword ui_shield ui_war_star ui_cwl_star"
        " res_capital_gold ui_xp ui_clan_games res_trophy res_gold ui_stopwatch"
    ).split()
    monkeypatch.setattr(EMOJIS, "_by_name", {n: f"<:{n}:1>" for n in icons})
    desc, _ = profile_parts(profile_player(), season=(4, 0), last_seen="2026-11-08T03:26:07+00:00")
    lines = desc.splitlines()
    assert lines[0].startswith("TH **18** <:ui_xp:1> **239** <:res_trophy:1> **4,961** <:ui_war_star:1>")
    for expected in (
        "<:ui_donate_troops:1> **Donated** 4",
        "<:ui_donate_troops:1> **Troops Donated** 385,903",
        "<:ui_donate_spells:1> **Spells Donated** 18,282",
        "<:ui_donate_sieges:1> **Siege Donated** 6,128",
        "<:ui_sword:1> **Attacks Won** 11,137",
        "<:ui_shield:1> **Defense Won** 746",
        "<:ui_cwl_star:1> **CWL War Stars** 1,150",
        "<:res_capital_gold:1> **Capital Gold Contributed** 5,160,621",
    ):
        assert expected in lines, expected
    assert any(line.startswith("<:ui_stopwatch:1> **Last Seen** <t:") for line in lines)


def test_profile_shows_country_flag():
    from clashhero.repos.ranks import CountryRank

    desc, _ = profile_parts(profile_player(), country=CountryRank(57, "US", "United States"))
    assert desc.splitlines()[1].endswith("🇺🇸 United States")
    assert "#57" not in desc  # the flag only; ranks shown are global
    assert "🇺🇸" not in profile_parts(profile_player())[0]
