import logging

from clashhero.services import army as army_mod
from clashhero.services.army import parse_army

# Real battle-log codes (Nov 2026): a Legend I attack and a defense.
ATTACK = (
    "h1p9e17_48-2m1p16e34_4-6p17e49_43-7p4e52_60i1x188-1x63-1x76d1x2-1x70"
    "u6x57-1x63-5x65-8x8-2x10s3x109-2x98-1x9-2x120-1x5"
)
DEFENSE = (
    "h0p10e32_14-1p16e16_48-2p7e4_19-7p4e57_53i1x64-1x147d1x53-1x98"
    "u2x1-2x58-5x7-3x28-1x23-4x5-5x80-1x97-2x12-1x135-1x82-1x6s1x10-3x35-1x120-2x2-1x5-1x9"
)


def test_heroes_with_pets_and_equipment():
    a = parse_army(ATTACK)
    got = [(h.hero.name, h.pet and h.pet.name, [e.name for e in h.equipment]) for h in a.heroes]
    assert got == [
        ("Archer Queen", "Frosty", ["Giant Arrow", "Action Figure"]),
        ("Grand Warden", "Sneezy", ["Healing Tome", "Eternal Tome"]),
        ("Minion Prince", "Greedy Raven", ["Meteor Staff", "Dark Orb"]),
        ("Dragon Duke", "Phoenix", ["Fire Heart", "Revenge Deck"]),
    ]


def test_troops_spells_and_clan_castle():
    a = parse_army(ATTACK)
    assert [(u.name, u.count) for u in a.troops] == [
        ("Rocket Balloon", 6),
        ("Inferno Dragon", 1),
        ("Dragon Rider", 5),
        ("Dragon", 8),
        ("Minion", 2),
    ]
    assert [(u.name, u.count) for u in a.spells][:2] == [("Ice Block Spell", 3), ("Revive Spell", 2)]
    assert [u.name for u in a.cc_troops] == ["Sky Wagon", "Inferno Dragon", "Ice Hound"]
    assert [u.name for u in a.cc_spells] == ["Rage Spell", "Overgrowth Spell"]


def test_newer_equipment_and_super_troops():
    d = parse_army(DEFENSE)
    assert [e.name for e in d.heroes[1].equipment] == ["Monolith Arrow", "Action Figure"]
    assert [e.name for e in d.heroes[3].equipment] == ["Flame Blower", "Rocket Backpack"]
    assert [u.name for u in d.cc_troops] == ["Super Valkyrie", "Super Yeti"]
    assert "Troop Launcher" in d.names() and "Dragon Duke" in d.names()


def test_full_link_and_bad_input():
    link = "https://link.clashofclans.com/en?action=CopyArmy&army=u10x0-2x3s1x9-3x2"
    a = parse_army(link)
    assert [(u.name, u.count) for u in a.troops] == [("Barbarian", 10), ("Giant", 2)]
    assert [(u.name, u.count) for u in a.spells] == [("Poison Spell", 1), ("Rage Spell", 3)]
    assert not parse_army(None) and not parse_army("") and not parse_army("garbage")


def test_unknown_ids_are_kept_and_logged_once(caplog):
    army_mod._reported.discard(("troop", 999))
    with caplog.at_level(logging.WARNING, logger="clashhero.services.army"):
        a = parse_army("u3x999-1x0")
        parse_army("u1x999")
    assert (a.troops[0].name, a.troops[0].label, a.troops[0].count) == (None, "?", 3)
    assert sum("troop id 999" in r.getMessage() for r in caplog.records) == 1


async def test_summary_names_units_newer_than_coc_py():
    import coc

    from clashhero.cogs._info_embeds import summarize_army

    client = coc.Client()
    client._load_static()
    s = summarize_army(client.parse_army_link("h7p17e52_60u1x0-2x10i1x188s2x2"))
    assert s.heroes == [("Dragon Duke", "Greedy Raven", ["Fire Heart", "Revenge Deck"])]
    assert s.troops == [("Barbarian", 1, 1), ("Minion", 2, 2)]
    assert s.cc_troops == [("Sky Wagon", 1)]
    assert (s.troop_space, s.spell_space) == (5, 4)
