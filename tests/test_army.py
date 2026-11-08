import coc
import pytest

from clashhero.cogs._info_embeds import army_link, army_param, summarize_army
from clashhero.cogs.army import army_embed_fields


def test_army_param():
    link = "https://link.clashofclans.com/en?action=CopyArmy&army=u10x0-2x3s1x9-3x2"
    assert army_param(link) == "u10x0-2x3s1x9-3x2"
    assert army_param("u10x0s1x9") == "u10x0s1x9"
    with pytest.raises(ValueError):
        army_param("hello world")
    assert army_link("u1x0").endswith("army=u1x0")


async def test_summarize_army_from_static_data():
    client = coc.Client()
    client._load_static()
    recipe = client.parse_army_link("u10x0-2x3s1x9-3x2")
    s = summarize_army(recipe)
    assert s.troops == [("Barbarian", 10, 1), ("Giant", 2, 5)]
    assert s.troop_space == 20
    assert s.spell_space == 1 + 6
    titles = [t for t, _ in army_embed_fields(s)]
    assert titles == ["Troops (20 space)", "Spells (7 space)"]


def test_army_fields_use_unit_icons(monkeypatch):
    from clashhero.cogs._info_embeds import ArmySummary
    from clashhero.services.emojis import EMOJIS

    monkeypatch.setattr(
        EMOJIS,
        "_by_name",
        {"thrower": "<:thrower:1>", "dragon_duke": "<:dragon_duke:2>", "phoenix": "<:phoenix:3>"},
    )
    s = ArmySummary(
        troops=[("Thrower", 9, 16), ("Mystery", 1, 1)],
        spells=[],
        cc_troops=[("Thrower", 1)],
        cc_spells=[],
        heroes=[("Dragon Duke", "Phoenix", ["Fire Heart"])],
    )
    fields = dict(army_embed_fields(s))
    assert fields["Heroes"] == "<:dragon_duke:2> **Dragon Duke** · <:phoenix:3> Fire Heart"
    assert fields["Troops (145 space)"].split("\n") == ["<:thrower:1> ` 9x` Thrower", "`  1x` Mystery"]
    assert fields["Clan Castle"] == "<:thrower:1> ` 1x` Thrower"
