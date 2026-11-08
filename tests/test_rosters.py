import discord

from clashhero.cogs._roster_views import render_roster_embed
from clashhero.repos import rosters as repo


def roster_row(**kw):
    base = dict(min_th=None, max_th=None, min_heroes=None)
    return {**base, **kw}


def test_parse_tags_any_format():
    text = "#2PPQLRJ, 9yy8 | #2ppqlrj\nnot-a-tag #ABC #Q0L2"
    assert repo.parse_tags(text) == ["#2PPQLRJ", "#9YY8", "#Q0L2"]


def test_check_eligibility():
    r = roster_row(min_th=14, max_th=16, min_heroes=200)
    assert repo.check_eligibility(r, 13, 300).startswith("needs TH14")
    assert repo.check_eligibility(r, 17, 300).startswith("max TH16")
    assert "hero" in repo.check_eligibility(r, 15, 100)
    assert repo.check_eligibility(r, 15, 250) is None
    assert repo.check_eligibility(roster_row(), None, None) is None


def m(tag, user=None, group=None):
    return {"player_tag": tag, "user_id": user, "group_id": group}


def test_ping_targets():
    members = [m("#A", 1, 10), m("#B", None, 11), m("#GONE", 3, 10)]
    clan = ["#A", "#B", "#C", "#D"]
    links = {"#A": 1, "#B": 2, "#C": 4, "#GONE": 3}
    assert repo.compute_ping_targets("unregistered", members, clan, links) == {4}
    assert repo.compute_ping_targets("missing", members, clan, links) == {3}
    assert repo.compute_ping_targets("everyone", members, clan, links) == {1, 2, 3}
    assert repo.compute_ping_targets("everyone", members, clan, links, group_id=11) == {2}


async def make(db, name="CWL", **kw):
    return await repo.create_roster(db, 1, "#C", name, **kw)


async def test_create_has_default_groups_and_clone(db):
    rid = await make(db, category="cwl", max_members=15, min_th=12)
    groups = await repo.list_groups(db, rid)
    assert [g["name"] for g in groups] == ["Confirmed", "Substitute"]
    assert await repo.default_group_id(db, rid) == groups[0]["id"]
    await repo.add_member(
        db, rid, "#A", user_id=1, group_id=groups[0]["id"], name="A", town_hall=15, heroes_total=1
    )
    new = await repo.clone_roster(db, rid, "CWL 2")
    clone = await repo.get_roster(db, new)
    assert (clone["min_th"], clone["max_members"], clone["category"]) == (12, 15, "cwl")
    assert [g["name"] for g in await repo.list_groups(db, new)] == ["Confirmed", "Substitute"]
    assert await repo.member_count(db, new) == 0


async def test_members_move_and_category_rule(db):
    a = await make(db, "A")
    b = await make(db, "B")
    await make(db, "T", category="trophy")
    gid = await repo.default_group_id(db, a)
    assert await repo.add_member(
        db, a, "#P", user_id=7, group_id=gid, name="P", town_hall=16, heroes_total=300
    )
    assert not await repo.add_member(
        db, a, "#P", user_id=None, group_id=gid, name=None, town_hall=None, heroes_total=None
    )
    assert (await repo.get_member(db, a, "#P"))["user_id"] == 7
    other = await repo.other_roster_in_category(db, 1, "cwl", "#P", b)
    assert other["name"] == "A"
    assert await repo.other_roster_in_category(db, 1, "trophy", "#P", b) is None
    assert await repo.count_user_accounts(db, a, 7) == 1
    sub = (await repo.list_groups(db, b))[1]["id"]
    assert await repo.move_member(db, a, "#P", to_roster_id=b, group_id=sub)
    assert await repo.member_count(db, a) == 0
    rows = await repo.list_members(db, b)
    assert rows[0]["group_name"] == "Substitute" and rows[0]["name"] == "P"
    assert await repo.clear_members(db, b) == 1


async def test_list_members_prefers_live_player_data_and_links(db):
    rid = await make(db)
    await db.execute(
        "INSERT INTO players (player_tag, name, town_hall, heroes_total, updated_at) "
        "VALUES ('#P', 'Live', 17, 400, 'x')"
    )
    await db.link_player("#P", 99)
    await repo.add_member(
        db, rid, "#P", user_id=None, group_id=None, name="Old", town_hall=15, heroes_total=1
    )
    row = (await repo.list_members(db, rid))[0]
    assert (row["name"], row["town_hall"], row["user_id"]) == ("Live", 17, 99)


def test_sort_and_csv_and_embed():
    rows = [
        {
            "player_tag": "#A",
            "name": "b",
            "town_hall": 15,
            "heroes_total": 200,
            "signed_up_at": "2",
            "group_id": 1,
            "group_name": "Confirmed",
            "user_id": 5,
            "trophies": 10,
        },
        {
            "player_tag": "#B",
            "name": "a",
            "town_hall": 16,
            "heroes_total": 100,
            "signed_up_at": "1",
            "group_id": 2,
            "group_name": "Substitute",
            "user_id": None,
            "trophies": 50,
        },
    ]
    assert [r["player_tag"] for r in repo.sort_members(rows, "th_heroes")] == ["#B", "#A"]
    assert [r["player_tag"] for r in repo.sort_members(rows, "heroes")] == ["#A", "#B"]
    assert [r["player_tag"] for r in repo.sort_members(rows, "signup_time")] == ["#B", "#A"]
    csv = repo.members_csv(rows)
    assert csv.splitlines()[0].startswith("player_tag,name") and "#B,a,16" in csv
    roster = {
        "id": 3,
        "name": "CWL",
        "clan_tag": "#C",
        "category": "cwl",
        "max_members": 15,
        "min_th": 14,
        "max_th": None,
        "min_heroes": None,
        "start_time": None,
        "end_time": None,
        "closed": 1,
        "color": None,
        "sort_by": "th",
    }
    groups = [{"id": 1, "name": "Confirmed"}, {"id": 2, "name": "Substitute"}]
    e = render_roster_embed(roster, groups, rows, "My Clan", discord.Color.blue())
    assert e.title == "CWL · My Clan"
    assert "2/15" in e.description and "Closed" in e.description and "TH 14" in e.description
    assert [f.name for f in e.fields] == ["Confirmed (1)", "Substitute (1)"]
    assert "<@5>" in e.fields[0].value


async def test_signup_rules_end_to_end(db):
    from types import SimpleNamespace as NS

    from clashhero.cogs import _roster_views as views

    bot = NS(db=db, coc=None, get_channel=lambda _id: None)
    guild = NS(id=1, get_role=lambda _id: None, get_member=lambda _id: None)
    me, other = NS(id=7, mention="<@7>"), NS(id=8, mention="<@8>")
    for tag, th in (("#A", 16), ("#B", 11), ("#U", 16)):
        await db.execute(
            "INSERT INTO players (player_tag, name, town_hall, heroes_total, updated_at) "
            "VALUES (?, ?, ?, 300, 'x')",
            (tag, tag[1:], th),
        )
    await db.link_player("#A", 7)
    await db.link_player("#B", 7)
    rid = await make(db, max_members=1, min_th=12, max_accounts_per_user=1)
    roster = await repo.get_roster(db, rid)

    res = await views.signup(bot, guild, roster, ["#A", "#B", "#U"], actor=me)
    assert res.added == ["A"]
    assert any("roster is full" in e for e in res.errors)

    await repo.update_roster(db, rid, max_members=10)
    roster = await repo.get_roster(db, rid)
    res = await views.signup(bot, guild, roster, ["#B", "#U"], actor=me)
    assert res.added == []
    assert any("needs TH12" in e for e in res.errors)
    assert any("isn't linked to you" in e for e in res.errors)

    res = await views.signup(bot, guild, roster, ["#A"], actor=other)
    assert any("isn't linked to you" in e for e in res.errors)

    res = await views.signup(bot, guild, roster, ["#U"], actor=other, as_manager=True)
    assert any("isn't linked to a Discord user" in e for e in res.errors)

    await repo.update_roster(db, rid, closed=1)
    roster = await repo.get_roster(db, rid)
    assert (await views.signup(bot, guild, roster, ["#A"], actor=me)).errors == ["This roster is closed."]

    assert await views.opt_out(bot, guild, roster, ["#A", "#ZZZ"], actor=me) == ["A"]


def test_dynamic_item_custom_ids():
    from clashhero.cogs._roster_views import OptOutButton, RefreshButton, SignupButton

    assert SignupButton(5).custom_id == "roster:signup:5"
    assert OptOutButton(5).custom_id == "roster:optout:5"
    assert RefreshButton(5).custom_id == "roster:refresh:5"
    assert SignupButton.__discord_ui_compiled_template__.fullmatch("roster:signup:42")["id"] == "42"
