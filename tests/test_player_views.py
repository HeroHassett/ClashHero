"""Player pages (/player info, units, upgrades, rushed) and their page buttons."""

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
    FIELD_LIMIT,
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


def test_profile_fits_field_limits():
    raw = {
        "heroes": [{"name": "Archer Queen", "level": 95, "village": "home"}],
        "heroEquipment": [{"name": f"Equipment {i:02d}", "level": 27, "village": "home"} for i in range(80)],
        "troops": [{"name": "Unicorn", "level": 10, "village": "home"}],
    }
    p = NS(
        name="Alex",
        tag="#2PP",
        town_hall=17,
        town_hall_weapon=None,
        exp_level=245,
        trophies=5321,
        best_trophies=6012,
        war_stars=1234,
        war_opted_in=False,
        clan=NS(name="Best_Clan", tag="#2QQ"),
        role=NS(value="coLeader"),
        attack_wins=0,
        defense_wins=0,
        clan_capital_contributions=1234567,
        _raw_data=raw,
    )
    desc, fields = profile_parts(p, discord_id=42, verified=True, season=(1200, 300), names=["Old_one"])
    assert desc.startswith("🏠 **Town Hall 17** · level `245`")
    assert "[Best\\_Clan](" in desc and "Co-Leader" in desc
    assert len(fields) <= 25
    assert all(len(value) <= FIELD_LIMIT for _, value, _ in fields)
    by_name = {name: value for name, value, _ in fields}
    assert by_name["🏆 Trophies"].startswith("`5,321` best `6,012`")
    assert by_name["⚔️ War"] == "`1,234★`\nOpted out"
    assert by_name["🔗 Discord"] == "<@42> ✅"
    assert by_name["🐾 Pets"] == "Unicorn `10`"
    assert by_name["🛡️ Equipment"].endswith(" …")
    assert by_name["📝 Previous names"] == "Old\\_one"


def test_profile_career_stats_from_achievements():
    from clashhero.cogs._info_embeds import compact

    achievements = {
        "Gold Grab": 2_000_000_000, "Elixir Escapade": 1_250_000_000, "Heroic Heist": 74_451_474,
        "Friend in Need": 385_903, "Sharing is caring": 18_282, "Siege Sharer": 6_128,
        "War League Legend": 1_150, "Games Champion": 166_260, "Well Seasoned": 320_040,
        "Aggressive Capitalism": 3_910_515, "Conqueror": 11_137, "Unbreakable": 746,
    }  # fmt: skip
    raw = {"achievements": [{"name": k, "value": v, "village": "home"} for k, v in achievements.items()]}
    p = NS(
        name="Alex", tag="#2PP", town_hall=18, town_hall_weapon=None, exp_level=300, trophies=5000,
        best_trophies=6203, war_stars=4214, war_opted_in=True, clan=None, role=None,
        attack_wins=0, defense_wins=0, clan_capital_contributions=5_160_621, _raw_data=raw,
    )  # fmt: skip
    _, fields = profile_parts(p)
    by_name = {name: value for name, value, _ in fields}
    assert by_name["⚔️ War"] == "`4,214★` wars\n`1,150★` CWL\nOpted in"
    assert by_name["🏰 Capital gold"] == "`5.2M` contributed\n`3.9M` raided"
    assert by_name["🎁 Donated · all time"] == "`385,903` troop space\n`18,282` spell space\n`6,128` sieges"
    loot = by_name["💰 Loot · all time"].splitlines()
    assert [line.rsplit(" ", 1)[1] for line in loot] == ["`2B+`", "`1.2B`", "`74.5M`"]
    assert by_name["🎯 Events"] == "`166,260` Clan Games points\n`320,040` challenge points"
    assert by_name["🗡️ Wins · all time"] == "`11,137` attacks\n`746` defenses"
    assert compact(999_999) == "999,999" and compact(1_000_000) == "1M"


def test_profile_uses_game_symbols_once_uploaded(monkeypatch):
    from clashhero.services.emojis import EMOJIS

    icons = (
        "ui_castle ui_spell_donation wall_wrecker ui_sword ui_shield ui_war_star ui_cwl_star"
        " res_capital_gold ui_xp ui_clan_games res_trophy res_gold"
    ).split()
    monkeypatch.setattr(EMOJIS, "_by_name", {n: f"<:{n}:1>" for n in icons})
    raw = {
        "achievements": [
            {"name": n, "value": v}
            for n, v in {
                "Friend in Need": 10,
                "Sharing is caring": 5,
                "Siege Sharer": 2,
                "War League Legend": 7,
                "Games Champion": 9,
                "Conqueror": 3,
                "Unbreakable": 1,
            }.items()
        ]
    }
    p = NS(
        name="Alex", tag="#2PP", town_hall=18, town_hall_weapon=None, exp_level=300, trophies=5000,
        best_trophies=6203, war_stars=40, war_opted_in=True, clan=None, role=None,
        attack_wins=0, defense_wins=0, clan_capital_contributions=12, _raw_data=raw,
    )  # fmt: skip
    desc, fields = profile_parts(p, season=(4, 0))
    by_name = dict((n, v) for n, v, _ in fields)
    assert "<:ui_xp:1> level `300`" in desc
    assert by_name["<:ui_castle:1> Donations"] == "<:ui_castle:1> `4` given\n<:ui_castle:1> `0` received"
    assert by_name["<:ui_castle:1> Donated · all time"] == (
        "<:ui_castle:1> `10` troop space\n"
        "<:ui_spell_donation:1> `5` spell space\n"
        "<:wall_wrecker:1> `2` sieges"
    )
    assert by_name["<:ui_war_star:1> War"] == "<:ui_war_star:1> `40` wars\n<:ui_cwl_star:1> `7` CWL\nOpted in"
    assert (
        by_name["<:ui_sword:1> Wins · all time"] == "<:ui_sword:1> `3` attacks\n<:ui_shield:1> `1` defenses"
    )
    assert by_name["<:res_capital_gold:1> Capital gold"] == "<:res_capital_gold:1> `12` contributed"
    assert by_name["<:ui_clan_games:1> Events"] == "<:ui_clan_games:1> `9` Clan Games points"
    assert by_name["<:res_trophy:1> Trophies"].startswith("<:res_trophy:1> `5,000` best `6,203`")
