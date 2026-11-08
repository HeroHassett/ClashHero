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
