"""Decode the game's army share codes: the battle log's ``armyShareCode`` and "copy army" links.

A code is a run of sections, each a letter followed by ``-``-separated entries:

- ``h`` heroes: ``<hero>[m<mode>][p<pet>][e<equipment>[_<equipment>]]``
- ``u`` troops and ``s`` spells: ``<count>x<id>``
- ``i`` clan castle troops and ``d`` clan castle spells: ``<count>x<id>``

e.g. ``h0p10e32_14-1p16e16_48u5x7-3x28s3x35-1x109``. IDs are offsets from each kind's base ID in
the game data. Names come from coc.py's bundled game data plus :data:`NEWER_IDS` for units it
doesn't know yet; anything still unknown keeps its ID with ``name=None`` (shown as "?") and is
logged once so it can be added.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

log = logging.getLogger(__name__)

BASE_IDS = {
    "troop": 4_000_000,
    "spell": 26_000_000,
    "hero": 28_000_000,
    "pet": 73_000_000,
    "equipment": 90_000_000,
}

# Units newer than coc.py's game data (checked Nov 2026). Worked out from the official API, which
# lists units in ID order, and by matching top players' equipped gear to their attack codes.
NEWER_IDS: dict[tuple[str, int], str] = {
    ("hero", 7): "Dragon Duke",
    ("pet", 17): "Greedy Raven",
    ("troop", 188): "Sky Wagon",
    ("equipment", 16): "Monolith Arrow",
    ("equipment", 52): "Fire Heart",
    ("equipment", 53): "Rocket Backpack",
    ("equipment", 56): "Stun Blaster",
    ("equipment", 57): "Flame Blower",
    ("equipment", 59): "Electro Fangs",
    ("equipment", 60): "Revenge Deck",
    ("equipment", 61): "Portal Pendant",
}

_STATIC_KEYS = {
    "troops": "troop",
    "spells": "spell",
    "heroes": "hero",
    "pets": "pet",
    "equipment": "equipment",
}

_SECTION = re.compile(
    r"h(?P<heroes>[^idus]+)"
    r"|i(?P<cc_troops>[\dx-]+)"
    r"|d(?P<cc_spells>[\dx-]+)"
    r"|u(?P<troops>[\dx-]+)"
    r"|s(?P<spells>[\dx-]+)"
)
_HERO = re.compile(r"(?P<hero>\d+)(?:m(?P<mode>\d+))?(?:p(?P<pet>\d+))?(?:e(?P<e1>\d+)(?:_(?P<e2>\d+))?)?")
_ITEM = re.compile(r"(?P<count>\d+)x(?P<id>\d+)")

_reported: set[tuple[str, int]] = set()


@cache
def _game_data() -> tuple[dict[tuple[str, int], str], dict[tuple[str, int], int]]:
    """(kind, id) -> in-game name, and -> housing space, from coc.py's game data + NEWER_IDS."""
    names: dict[tuple[str, int], str] = {}
    housing: dict[tuple[str, int], int] = {}
    try:
        import coc

        path = Path(coc.__file__).parent / "static" / "static_data.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for key, kind in _STATIC_KEYS.items():
            base = BASE_IDS[kind]
            for item in data.get(key) or []:
                if isinstance(item.get("_id"), int) and item.get("name"):
                    names.setdefault((kind, item["_id"] - base), item["name"])
                    if isinstance(item.get("housing_space"), int):
                        housing.setdefault((kind, item["_id"] - base), item["housing_space"])
    except Exception:
        log.warning("Couldn't read coc.py's game data; army codes will show IDs", exc_info=True)
    names.update(NEWER_IDS)
    return names, housing


def _names() -> dict[tuple[str, int], str]:
    return _game_data()[0]


def housing_space(kind: str, unit_id: int) -> int:
    """Housing space of a troop or spell, or 0 when unknown."""
    return _game_data()[1].get((kind, unit_id), 0)


def unit_name(kind: str, unit_id: int) -> str | None:
    name = _names().get((kind, unit_id))
    if name is None and (kind, unit_id) not in _reported:
        _reported.add((kind, unit_id))
        log.warning("Unknown %s id %d in an army code; add it to army.NEWER_IDS", kind, unit_id)
    return name


@dataclass(frozen=True)
class Unit:
    kind: str  # troop | spell | hero | pet | equipment
    id: int
    name: str | None  # None when the ID isn't known yet
    count: int = 1

    @property
    def label(self) -> str:
        return self.name or "?"


@dataclass(frozen=True)
class HeroLoadout:
    hero: Unit
    pet: Unit | None = None
    equipment: tuple[Unit, ...] = ()


@dataclass
class Army:
    heroes: list[HeroLoadout] = field(default_factory=list)
    troops: list[Unit] = field(default_factory=list)
    spells: list[Unit] = field(default_factory=list)
    cc_troops: list[Unit] = field(default_factory=list)
    cc_spells: list[Unit] = field(default_factory=list)

    def __bool__(self) -> bool:
        return any((self.heroes, self.troops, self.spells, self.cc_troops, self.cc_spells))

    def names(self) -> set[str]:
        """Every known unit name in the army (for fetching icons)."""
        units = [*self.troops, *self.spells, *self.cc_troops, *self.cc_spells]
        for h in self.heroes:
            units += [h.hero, *(h.equipment), *([h.pet] if h.pet else [])]
        return {u.name for u in units if u.name}


def _unit(kind: str, unit_id: int, count: int = 1) -> Unit:
    return Unit(kind, unit_id, unit_name(kind, unit_id), count)


def _items(text: str, kind: str) -> list[Unit]:
    out = []
    for part in text.split("-"):
        m = _ITEM.fullmatch(part)
        if m and int(m["count"]) > 0:
            out.append(_unit(kind, int(m["id"]), int(m["count"])))
    return out


def parse_army(code: str | None) -> Army:
    """Decode an army share code (or a full "copy army" link). Bad input gives an empty Army."""
    army = Army()
    if not code:
        return army
    code = code.split("army=", 1)[-1].split("&", 1)[0]
    for m in _SECTION.finditer(code):
        if m["heroes"]:
            for entry in m["heroes"].split("-"):
                h = _HERO.fullmatch(entry)
                if not h:
                    continue
                pet = _unit("pet", int(h["pet"])) if h["pet"] else None
                gear = tuple(_unit("equipment", int(e)) for e in (h["e1"], h["e2"]) if e)
                army.heroes.append(HeroLoadout(_unit("hero", int(h["hero"])), pet, gear))
        elif m["troops"]:
            army.troops += _items(m["troops"], "troop")
        elif m["spells"]:
            army.spells += _items(m["spells"], "spell")
        elif m["cc_troops"]:
            army.cc_troops += _items(m["cc_troops"], "troop")
        elif m["cc_spells"]:
            army.cc_spells += _items(m["cc_spells"], "spell")
    return army
