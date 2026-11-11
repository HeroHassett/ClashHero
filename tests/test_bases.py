import sqlite3

import discord
import pytest

from clashhero.cogs.bases import base_list_line, render_base_card, usage_line
from clashhero.repos import bases as repo
from clashhero.utils.fmt import player_link


async def test_usage_counts_per_season(db):
    ring = await repo.add_base(db, 1, "Ring", 17, added_by=9, type="cwl")
    await repo.add_base(db, 1, "Box", 17, added_by=9)
    await repo.add_base(db, 1, "Old", 15, added_by=9)
    await repo.add_base(db, 2, "Ring", 17, added_by=9)  # other guild, same name is fine
    await repo.record_usage(db, ring, "2026-11", 1, "#P")
    await repo.record_usage(db, ring, "2026-11", 3, None)
    await repo.record_usage(db, ring, "2026-10", 1, None)
    rows = await repo.list_bases_with_usage(db, 1, "2026-11")
    assert [(r["name"], r["uses"]) for r in rows] == [("Box", 0), ("Ring", 2), ("Old", 0)]
    unused = await repo.list_bases_with_usage(db, 1, "2026-11", unused_only=True, town_hall=17)
    assert [r["name"] for r in unused] == ["Box"]
    assert [r["name"] for r in await repo.list_bases_with_usage(db, 1, "2026-11", type="cwl")] == ["Ring"]
    report = await repo.usage_report(db, 1, "2026-11")
    assert [r["war_round"] for r in report] == [1, 3]
    assert await repo.remove_usage(db, ring, "2026-11", 3)
    assert not await repo.remove_usage(db, ring, "2026-11", 3)
    assert [r["war_round"] for r in await repo.usage_report(db, 1, "2026-11")] == [1]


async def test_unique_name_case_insensitive_and_edit(db):
    bid = await repo.add_base(db, 1, "Ring", 17, added_by=9)
    with pytest.raises(sqlite3.IntegrityError):
        await repo.add_base(db, 1, "ring", 16, added_by=9)
    assert (await repo.get_base_by_name(db, 1, "RING"))["id"] == bid
    await repo.update_base(db, bid, notes="anti-root", town_hall=16)
    assert (await repo.get_base(db, bid))["town_hall"] == 16
    with pytest.raises(ValueError):
        await repo.update_base(db, bid, guild_id=5)
    await repo.delete_base(db, bid)
    assert await repo.get_base(db, bid) is None


async def test_voting_default_on(db):
    assert await repo.get_voting(db, 1)
    await repo.set_voting(db, 1, False)
    assert not await repo.get_voting(db, 1)


def test_filter_bases():
    rows = [
        {"name": "Ring", "town_hall": 17, "type": "cwl"},
        {"name": "Box", "town_hall": 16, "type": "war"},
        {"name": "Ringo", "town_hall": 15, "type": "war"},
    ]
    assert [r["name"] for r in repo.filter_bases(rows, "ring")] == ["Ring", "Ringo"]
    assert [r["name"] for r in repo.filter_bases(rows, "th16")] == ["Box"]
    assert len(repo.filter_bases(rows, "")) == 3


def test_is_copy_link():
    assert repo.is_copy_link("https://link.clashofclans.com/en?action=OpenLayout&id=x")
    assert not repo.is_copy_link("hello")
    assert not repo.is_copy_link(None)


def test_base_list_and_usage_lines():
    row = {
        "town_hall": 9,
        "name": "Ring",
        "type": "war",
        "uses": 2,
        "link": "https://link.clashofclans.com/x",
    }
    assert (
        base_list_line(row, 4)
        == "` TH9` **Ring** · ⚔️ War · used **2×** · [Copy base](https://link.clashofclans.com/x)"
    )
    assert (
        base_list_line({**row, "uses": 0, "link": None, "type": "cwl"}) == "`TH9` **Ring** · 🏆 CWL · unused"
    )
    use = {
        "war_round": None,
        "town_hall": 17,
        "base_name": "Box",
        "player_name": None,
        "player_tag": "#P",
        "used_at": "2026-11-02T10:00:00+00:00",
    }
    assert usage_line(use).startswith(f"`R?` `TH17` **Box** · [#P]({player_link('#P')}) · <t:")
    named = usage_line({**use, "player_name": "Ann"})
    assert f"· [Ann]({player_link('#P')}) ·" in named
    assert usage_line({**use, "war_round": 3, "player_tag": None}).startswith("`R3` `TH17` **Box** · — · ")


def test_lines_and_card_use_town_hall_icons(monkeypatch):
    from clashhero.services.emojis import EMOJIS

    monkeypatch.setattr(EMOJIS, "_by_name", {"th16": "<:th16:1>", "ui_sword": "<:ui_sword:2>"})
    row = {"town_hall": 16, "name": "Ring", "type": "war", "uses": 0, "link": None}
    assert base_list_line(row, 4) == "<:th16:1> **Ring** · <:ui_sword:2> War · unused"
    base = {**row, "notes": None, "image_url": None, "added_by": 9}
    card = render_base_card(base, discord.Color.blue())
    assert card.title == "Ring"
    assert card.description == "<:th16:1> **Town Hall 16** · <:ui_sword:2> War"
    assert [f.name for f in card.fields] == ["Added by"]
