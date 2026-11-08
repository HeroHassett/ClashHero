"""Behaviour that keeps the bot working through Clash of Clans updates."""

import logging
from datetime import timedelta
from types import SimpleNamespace as NS

import pytest
from test_collector import NOW, FakeHttp, battle, legend_player, setup  # noqa: F401

from clashhero.cogs._info_embeds import unit_levels
from clashhero.services import battlelog
from clashhero.utils import ranked


@pytest.fixture
def restore_tiers():
    saved = (ranked.LEAGUE_TIERS, dict(ranked._TIER_INDEX), ranked.MIN_TRACKED_TIER)
    yield
    ranked.LEAGUE_TIERS, ranked._TIER_INDEX, ranked.MIN_TRACKED_TIER = saved


def test_new_tiers_from_api_are_tracked(restore_tiers):
    names = list(ranked.LEAGUE_TIERS[:-1]) + ["Legend I", "Legend Supreme"]
    assert ranked.set_tiers(names) == ["Legend Supreme"]
    assert ranked.is_tracked_ranked("Legend Supreme") and ranked.is_legend_tier("Legend Supreme")
    assert ranked.is_tracked_ranked("Dragon League 28") and not ranked.is_tracked_ranked("Titan League 27")


def test_broken_tier_list_is_ignored(restore_tiers):
    assert ranked.set_tiers([]) == [] and ranked.set_tiers(["Only one"]) == []
    assert ranked.is_tracked_ranked("Legend I")


def test_unknown_battle_types_logged_once(caplog):
    battlelog._warned_types.clear()
    b = battlelog.parse_battles([battle("20261107T120000.000Z") | {"battleType": "brandNewMode"}])
    with caplog.at_level(logging.WARNING):
        assert battlelog.note_unknown_types(b) == {"brandNewMode"}
        assert battlelog.note_unknown_types(b) == set()
    assert sum("brandNewMode" in r.message for r in caplog.records) == 1
    assert not b[0].ranked


async def test_one_bad_player_doesnt_stop_the_cycle(setup):  # noqa: F811
    bot, coc, col = setup
    seen = []

    async def fake_poll(tag, now):
        if tag == "#BAD":
            raise IndexError("unknown troop level")
        seen.append(tag)

    col._poll_player = fake_poll
    col.player_tags_to_poll = lambda: _async({"#A", "#BAD", "#C"})
    await col._poll_players()
    assert sorted(seen) == ["#A", "#C"]


async def _async(v):
    return v


async def test_trophies_without_a_known_battle_fall_back_to_inference(setup):  # noqa: F811
    bot, coc, col = setup
    coc.http = FakeHttp()
    coc.http.items = [battle("20261107T110000.000Z")]
    coc.player = legend_player(5000)
    await col._poll_player("#A", NOW)
    # Trophies move, but the only new battle is of a type this version doesn't treat as ranked.
    coc.http.items = [battle("20261107T120000.000Z") | {"battleType": "brandNewMode"}] + coc.http.items
    coc.player = legend_player(5030)
    await col._poll_player("#A", NOW + timedelta(minutes=1))
    assert not [e for n, e in bot.events if n == "clash_legend_battle"]
    await col._poll_player("#A", NOW + timedelta(minutes=12))  # re-check: still nothing ranked
    assert not [e for n, e in bot.events if n == "clash_legend_battle"]
    await col._poll_player("#A", NOW + timedelta(minutes=45))  # past the 30-minute window
    (e,) = [e for n, e in bot.events if n == "clash_legend_battle"]
    assert (e.kind, e.trophies, e.from_battle_log) == ("attack", 30, False)


class StaticUnit:
    def __init__(self, maxes):
        self.maxes = maxes

    def get_max_level_for_townhall(self, th):
        return self.maxes.get(th)


def test_unit_levels_survive_stale_game_data():
    client = NS(
        get_troop=lambda n, home, lvl: StaticUnit({17: 11, 18: 12}) if n == "Barbarian" else None,
        get_spell=lambda n, lvl: StaticUnit({17: 10, 18: 11}),
        get_hero=lambda n, lvl: None,
        get_pet=lambda n, lvl: None,
        get_equipment=lambda n, lvl: None,
    )
    raw = {
        "troops": [
            {"name": "Barbarian", "level": 13, "maxLevel": 13, "village": "home"},  # newer than data
            {"name": "Brand New Troop", "level": 2, "maxLevel": 5, "village": "home"},  # unknown
        ],
        "spells": [{"name": "Rage Spell", "level": 9, "maxLevel": 11, "village": "home"}],
    }
    player = NS(town_hall=18, _raw_data=raw, _client=client)
    units = {u.name: u for u in unit_levels(player)}
    assert (units["Barbarian"].max_for_th, units["Barbarian"].approximate) == (13, True)
    assert units["Barbarian"].max_for_prev_th is None
    assert (units["Brand New Troop"].max_for_th, units["Brand New Troop"].approximate) == (5, True)
    assert (units["Rage Spell"].max_for_th, units["Rage Spell"].max_for_prev_th) == (11, 10)
    assert not units["Rage Spell"].approximate


async def test_reregistering_a_player_source_replaces_it(setup):  # noqa: F811
    bot, coc, col = setup

    class Cog:
        def __init__(self, tags):
            self.tags = tags

        async def tracked(self):
            return self.tags

    col.register_player_source(Cog({"#OLD"}).tracked)
    col.register_player_source(Cog({"#NEW"}).tracked)  # e.g. the cog was reloaded
    assert len(col._player_sources) == 1
    tags = await col.player_tags_to_poll()
    assert "#NEW" in tags and "#OLD" not in tags


async def test_bot_uses_the_running_event_loop():
    # coc.py binds to the loop current at construction; the bot must be built inside the loop it
    # runs on (see __main__.run) or CWL war fetches fail with "attached to a different loop".
    import asyncio
    from pathlib import Path

    from clashhero.bot import ClashHeroBot
    from clashhero.config import Config

    bot = ClashHeroBot(Config("t", "e", "p", "k", Path(":memory:"), None, 60, 60, 120, 600, "INFO"))
    assert bot.coc.loop is asyncio.get_running_loop()


def test_home_units_reads_raw_and_survives_unknown_levels():
    from clashhero.services.snapshots import home_units

    raw = {
        "heroes": [
            {"name": "Barbarian King", "level": 100, "village": "home"},
            {"name": "Battle Machine", "level": 35, "village": "builderBase"},
        ],
        "heroEquipment": [{"name": "Giant Gauntlet", "level": 27, "village": "home"}],
        "troops": [
            {"name": "L.A.S.S.I", "level": 15, "village": "home"},
            {"name": "Barbarian", "level": 13, "village": "home"},
            {"name": "Super Barbarian", "level": 13, "village": "home"},
        ],
    }
    p = NS(_raw_data=raw)
    assert home_units(p, "heroes") == [("Barbarian King", 100)]
    assert home_units(p, "equipment") == [("Giant Gauntlet", 27)]
    assert home_units(p, "pets") == [("L.A.S.S.I", 15)]
    assert home_units(p, "troops") == [("Barbarian", 13)]

    class Stale:  # coc.py model whose bundled data is older than the game
        @property
        def pets(self):
            raise IndexError("list index out of range")

    assert home_units(Stale(), "pets") == []


def test_cogs_dont_answer_errors_twice():
    from clashhero.cogs._base import BaseCog

    # The bot-wide tree.on_error replies; a cog-level handler would make Discord.py reply twice.
    assert "cog_app_command_error" not in BaseCog.__dict__
