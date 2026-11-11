from clashhero.repos import autoroles as repo
from clashhero.repos.autoroles import Account, Mapping


def acc(
    tag="#A",
    clan="#C",
    role="member",
    th=16,
    league="Legend League",
    trophies=5300,
    verified=False,
    default=False,
    bh=10,
    bleague=None,
    name="Alice",
):
    return Account(tag, name, verified, default, clan, role, th, bh, trophies, league, bleague)


MAPS = [
    Mapping("clan_role", "#C", "leader", 1),
    Mapping("clan_role", "#C", "member", 2),
    Mapping("clan_role", "#C", "everyone", 3),
    Mapping("town_hall", "", "16", 16),
    Mapping("league", "", "legend league", 20),
    Mapping("trophy_band", "", "5250-5499", 21),
    Mapping("builder_hall", "", "10", 30),
    Mapping("family", "", "family", 40),
    Mapping("family", "", "guest", 41),
    Mapping("family", "", "exclusive", 42),
    Mapping("family", "", "verified", 43),
    Mapping("family", "", "linked", 44),
    Mapping("family", "", "leaders", 45),
    Mapping("war", "#C", "war", 99),
]


def test_family_member_roles():
    roles = repo.desired_roles([acc()], {"#C"}, MAPS)
    assert roles == {2, 3, 16, 20, 21, 30, 40, 42, 44}


def test_guest_gets_no_th_unless_allowed():
    a = acc(clan="#OTHER")
    assert repo.desired_roles([a], {"#C"}, MAPS) == {41, 44}
    assert 16 in repo.desired_roles([a], {"#C"}, MAPS, allow_non_family=True)


def test_leader_verified_and_mixed_accounts():
    accounts = [acc(role="leader", verified=True), acc(tag="#B", clan=None)]
    roles = repo.desired_roles(accounts, {"#C"}, MAPS)
    assert {1, 3, 40, 43, 44, 45} <= roles
    assert 42 not in roles  # not every account is in the family


def test_only_verified_clan_roles():
    roles = repo.desired_roles([acc()], {"#C"}, MAPS, only_verified_clan_roles=True)
    assert 2 not in roles and 3 not in roles


def test_no_accounts_no_roles():
    assert repo.desired_roles([], {"#C"}, MAPS) == set()


def test_role_diff_only_removes_managed():
    managed = repo.managed_roles(MAPS)
    assert 99 not in managed
    add, remove = repo.role_diff({2, 500, 99}, {3}, managed)
    assert add == {3} and remove == {2}


def test_pick_account():
    a = acc(tag="#A", th=14, default=True)
    b = acc(tag="#B", th=16)
    c = acc(tag="#X", th=17, clan="#OTHER")
    assert repo.pick_account([a, b, c], {"#C"}, "default") == (a, True)
    assert repo.pick_account([a, b, c], {"#C"}, "best") == (b, True)
    assert repo.pick_account([c], {"#C"}, "best") == (c, False)
    assert repo.pick_account([], {"#C"}) == (None, False)


def test_format_nickname():
    nick = repo.format_nickname("{NAME} | {ALIAS} {ROLE}", name="Alice", alias="AH", role="coLeader")
    assert nick == "Alice | AH Co-Lead"
    assert repo.format_nickname("{TH} {NAME}", name="Bob", th=16) == "16 Bob"
    long = repo.format_nickname("{NAME} {DISCORD}", name="A" * 30, discord="discorduser")
    assert len(long) <= 32
    assert repo.format_nickname("{NAME} | {ALIAS}", name="Solo") == "Solo"


def test_validate_format():
    assert repo.validate_format("{NAME} | {CLAN}", family=True) is None
    assert "include" in repo.validate_format("{TH}", family=True)
    assert "Non-family" in repo.validate_format("{NAME} {ALIAS}", family=False)
    assert "Unknown" in repo.validate_format("{NAME} {FOO}", family=True)


def test_parse_band():
    assert repo.parse_band("5200-5249") == (5200, 5249)
    assert repo.parse_band("x") is None


async def test_storage(db):
    await repo.set_mapping(db, 1, "town_hall", "", "16", 5)
    await repo.set_mapping(db, 1, "town_hall", "", "16", 6)
    await repo.set_mapping(db, 1, "war", "#C", "war", 7)
    rows = await repo.get_mappings(db, 1)
    assert [(r["kind"], r["role_id"]) for r in rows] == [("town_hall", 6), ("war", 7)]
    assert len(await repo.guilds_with_war_role(db, "#C")) == 1
    assert await repo.delete_kind(db, 1, "town_hall") == 1
    await repo.set_mapping(db, 1, "war", "#C", "war", None)
    assert await repo.get_mappings(db, 1) == []

    s = await repo.get_settings(db, 1)
    assert s["auto_update"] == 1
    await repo.update_settings(db, 1, removal_delay_hours=5, nick_enabled=None)
    assert (await repo.get_settings(db, 1))["removal_delay_hours"] == 5


async def test_pending_removals(db):
    await repo.schedule_removal(db, 1, 10, 5, "2026-01-02T00:00:00")
    assert await repo.due_removals(db, 1, 10, "2026-01-01T00:00:00") == set()
    assert await repo.due_removals(db, 1, 10, "2026-01-03T00:00:00") == {5}
    await repo.cancel_removals(db, 1, 10, [5])
    assert await repo.due_removals(db, 1, 10, "2026-01-03T00:00:00") == set()


async def test_accounts_by_user(db):
    await db.link_player("#A", 10, verified=True)
    await db.link_player("#B", 10)
    await db.execute(
        "INSERT INTO players (player_tag, name, town_hall, clan_tag, clan_role, updated_at) "
        "VALUES ('#A', 'Alice', 16, '#C', 'leader', 'x')"
    )
    out = await repo.accounts_by_user(db, [10])
    accounts = out[10]
    assert [a.tag for a in accounts] == ["#A", "#B"]
    assert accounts[0].verified and accounts[0].is_default and accounts[0].clan_role == "leader"
    assert accounts[1].name == "#B" and accounts[1].town_hall == 0
    assert await repo.accounts_by_user(db, []) == {}


def test_autorole_result_text():
    from clashhero.cogs.autoroles import settings_text, sync_text

    s = {
        "auto_update": 1,
        "removal_delay_hours": 24,
        "allow_not_linked": 0,
        "allow_non_family": 0,
        "only_verified_clan_roles": 1,
    }
    assert settings_text(s).split("\n") == [
        "✅ Auto update",
        "⏱️ Removal delay **24h**",
        "❌ Allow not linked",
        "❌ Non-family accounts count",
        "✅ Verified-only clan roles",
    ]
    assert sync_text(2, 1) == "Roles synced.\n▲ **2** added · ▼ **1** removed"
    assert (
        sync_text(0, 0, "<@1>", True)
        == "Roles synced for <@1>.\n▲ **0** added · ▼ **0** removed · nickname updated"
    )


def test_autorole_list_lines(monkeypatch):
    from clashhero.cogs.autoroles import _mapping_order, mapping_line
    from clashhero.services.emojis import EMOJIS

    monkeypatch.setattr(EMOJIS, "_by_name", {"th16": "<:th16:1>", "lt_crystal_league_ii": "<:lt_c2:2>"})
    names = {"#C": "Alpha"}

    def lines(*maps):
        return [mapping_line(m, names) for m in sorted(maps, key=_mapping_order)]

    assert lines(
        Mapping("town_hall", "", "9", 1), Mapping("town_hall", "", "16", 2), Mapping("town_hall", "", "10", 3)
    ) == [
        "<:th16:1>**16** → <@&2>",
        "TH **10** → <@&3>",
        "TH **9** → <@&1>",
    ]
    assert lines(Mapping("clan_role", "#C", "member", 1), Mapping("clan_role", "#C", "coLeader", 2)) == [
        "**Alpha** · Co-Leader → <@&2>",
        "**Alpha** · Member → <@&1>",
    ]
    assert lines(Mapping("clan_role", "#GONE", "everyone", 4)) == ["**#GONE** · Everyone → <@&4>"]
    assert (
        mapping_line(Mapping("league", "", "crystal league ii", 5), names)
        == "<:lt_c2:2> **Crystal League II** → <@&5>"
    )
    assert mapping_line(Mapping("trophy_band", "", "5200-5249", 6), names) == "🏆 **5,200–5,249** → <@&6>"
    assert mapping_line(Mapping("builder_hall", "", "10", 7), names) == "BH **10** → <@&7>"
    assert mapping_line(Mapping("family", "", "linked", 8), names) == "**Account linked** → <@&8>"
    assert mapping_line(Mapping("war", "#C", "war", 9), names) == "⚔️ **Alpha** → <@&9>"
