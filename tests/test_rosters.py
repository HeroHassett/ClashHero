import discord
import pytest

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


# ------------------------------------------------------------------ suggestions


def fake_bot(db, channels=None):
    from types import SimpleNamespace as NS

    channels = channels or {}
    return NS(db=db, coc=None, get_channel=lambda cid: channels.get(cid))


def fake_guild():
    from types import SimpleNamespace as NS

    return NS(id=1, get_role=lambda _id: None, get_member=lambda _id: None)


async def add_player(db, tag, th=16, heroes=300, clan="#C", last_seen=None, link=None):
    await db.execute(
        "INSERT INTO players (player_tag, name, town_hall, heroes_total, clan_tag, last_seen, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'x')",
        (tag, tag[1:], th, heroes, clan, last_seen),
    )
    if link:
        await db.link_player(tag, link)


async def test_suggestion_candidates_and_other_rosters(db):
    await add_player(db, "#IN", link=1)
    await add_player(db, "#SIGNED", clan="#ELSE", link=2)
    await add_player(db, "#AWAY", clan="#ELSE")
    rid = await make(db, "Main")
    other = await make(db, "Other")
    await make(db, "Trophy", category="trophy")
    gid = await repo.default_group_id(db, rid)
    await repo.add_member(
        db, rid, "#SIGNED", user_id=None, group_id=gid, name=None, town_hall=None, heroes_total=None
    )
    await repo.add_member(
        db, rid, "#GHOST", user_id=9, group_id=None, name="Ghost", town_hall=14, heroes_total=200
    )
    await repo.add_member(
        db, other, "#IN", user_id=1, group_id=None, name="In", town_hall=16, heroes_total=300
    )
    rows = {
        r["player_tag"]: r for r in await repo.suggestion_candidate_rows(db, await repo.get_roster(db, rid))
    }
    assert set(rows) == {"#IN", "#SIGNED", "#GHOST"}
    assert not rows["#IN"]["signed_up"] and rows["#IN"]["user_id"] == 1
    assert rows["#SIGNED"]["signed_up"] and rows["#SIGNED"]["group_name"] == "Confirmed"
    assert (rows["#GHOST"]["name"], rows["#GHOST"]["town_hall"], rows["#GHOST"]["user_id"]) == (
        "Ghost",
        14,
        9,
    )
    assert await repo.tags_on_other_rosters(db, 1, "cwl", rid) == {"#IN": "Other"}
    assert await repo.tags_on_other_rosters(db, 1, "trophy", rid) == {}


async def test_suggestion_groups_fall_back_and_create(db):
    rid = await make(db)
    groups = await repo.list_groups(db, rid)
    assert await repo.suggestion_groups(db, rid) == (groups[0]["id"], groups[1]["id"])
    await repo.rename_group(db, groups[0]["id"], "Main")
    await repo.delete_group(db, groups[1]["id"])
    conf, sub = await repo.suggestion_groups(db, rid)
    assert conf == groups[0]["id"]
    assert [g["name"] for g in await repo.list_groups(db, rid)] == ["Main", "Substitute"]
    assert sub == (await repo.get_group_by_name(db, rid, "Substitute"))["id"]


async def test_apply_suggestion_end_to_end(db):
    from types import SimpleNamespace as NS

    from clashhero.cogs import _roster_views as views

    for i, tag in enumerate(("#A", "#B", "#C1", "#D", "#OLD")):
        await add_player(db, tag, th=16, link=10 + i)
    rid = await make(db, max_members=10)
    conf, sub = await repo.suggestion_groups(db, rid)
    await repo.add_member(
        db, rid, "#OLD", user_id=14, group_id=conf, name="OLD", town_hall=16, heroes_total=1
    )
    await repo.add_member(db, rid, "#B", user_id=11, group_id=sub, name="B", town_hall=16, heroes_total=1)
    await repo.add_member(db, rid, "#A", user_id=10, group_id=conf, name="A", town_hall=16, heroes_total=1)
    roster = await repo.get_roster(db, rid)
    actor = NS(id=1, mention="<@1>")

    text = await views.apply_suggestion(
        fake_bot(db), fake_guild(), roster, ["#A", "#B", "#C1"], ["#D"], actor=actor
    )
    groups = {m["player_tag"]: m["group_name"] for m in await repo.list_members(db, rid)}
    assert groups == {
        "#A": "Confirmed",  # already there: untouched
        "#B": "Confirmed",  # promoted
        "#C1": "Confirmed",  # signed up
        "#D": "Substitute",
        "#OLD": "Substitute",  # demoted, not removed
    }
    assert "Moved out of Confirmed**: OLD" in text and "Signed up: C1" in text
    again = await views.apply_suggestion(
        fake_bot(db), fake_guild(), roster, ["#A", "#B", "#C1"], ["#D"], actor=actor
    )
    assert again == "Roster already matches the suggestion."


def test_render_suggestion_embeds():
    from datetime import UTC, datetime

    from clashhero.cogs._roster_views import render_suggestion_embeds
    from clashhero.repos import roster_scoring as S

    now = datetime(2026, 11, 7, tzinfo=UTC)
    cands = [S.Candidate(f"#P{i}", f"Player`{i}", 17 - i % 3, 400, now.isoformat()) for i in range(8)]
    cands[0].signed_up = True
    hist = {"#P0": S.History(cwl_hits=4, cwl_triples=3, attacks=4, stars=11, used=4, available=4)}
    ranked = S.score_candidates(cands, hist, now)
    roster = {"name": "CWL", "clan_tag": "#C"}
    excluded = [(S.Candidate("#LOW", "Low", 12), "needs TH14+ (is TH12)")]
    embeds = render_suggestion_embeds(
        roster, ranked, excluded, size=5, substitutes=2, seasons=["2026-10"], color=discord.Color.blue()
    )
    assert len(embeds) == 1
    e = embeds[0]
    assert e.title == "Suggested lineup · CWL"
    assert "-- Confirmed (top 5)" in e.description and "-- Substitutes" in e.description
    assert "-- Not picked" in e.description and "3/4" in e.description and "Player'0*" in e.description
    assert "`" not in e.description.split("```")[1]  # names can't break the code block
    assert [f.name for f in e.fields] == ["How the score works", "Not eligible (1)"]
    assert "2026-10" in e.fields[0].value and "Low `#LOW`: needs TH14+" in e.fields[1].value
    empty = render_suggestion_embeds(
        roster, [], [], size=5, substitutes=0, seasons=[], color=discord.Color.blue()
    )
    assert "Nobody eligible" in empty[0].description


# ------------------------------------------------------------------ auto-ping


def test_autoping_due_windows():
    from datetime import UTC, datetime

    now = datetime(2026, 11, 7, 12, tzinfo=UTC)
    r = {
        "closed": 0,
        "start_time": "2026-11-08T08:00:00+00:00",
        "end_time": "2026-11-07T15:00:00+00:00",
        "autoping_start_hours": 24,
        "autoping_end_hours": 2,
        "autoping_start_sent": None,
        "autoping_end_sent": None,
    }
    assert repo.autoping_due(r, now) == [("start", r["start_time"])]  # end window opens at 13:00
    later = now.replace(hour=13, minute=30)
    assert repo.autoping_due(r, later) == [("start", r["start_time"]), ("end", r["end_time"])]
    sent = {**r, "autoping_start_sent": r["start_time"], "autoping_end_sent": r["end_time"]}
    assert repo.autoping_due(sent, later) == []
    moved = {**sent, "end_time": "2026-11-07T14:00:00+00:00"}  # editing the time re-arms it
    assert repo.autoping_due(moved, later) == [("end", "2026-11-07T14:00:00+00:00")]
    assert repo.autoping_due(r, now.replace(hour=16)) == [("start", r["start_time"])]  # end passed
    assert repo.autoping_due({**r, "closed": 1}, later) == []
    assert repo.autoping_due({"closed": 0, "start_time": None, "end_time": None}, now) == []


def test_describe_autoping_and_format_hours():
    assert repo.format_hours(1.5) == "1h30m"
    assert repo.format_hours(0.25) == "15m"
    assert repo.format_hours(24) == "24h"
    assert repo.describe_autoping({"autoping_end_hours": None, "autoping_start_hours": None}) is None
    assert repo.describe_autoping({}) is None
    text = repo.describe_autoping({"autoping_end_hours": 6, "autoping_start_hours": 0.5})
    assert text == "Auto-ping unregistered members 6h before signups close and 30m before start"


async def test_autoping_columns_and_clone(db):
    rid = await make(db, end_time="2026-11-08T00:00:00+00:00")
    await repo.set_autoping(db, rid, start_hours=None, end_hours=3, message="go", channel_id=55)
    await repo.mark_autoping_sent(db, rid, "end", "2026-11-08T00:00:00+00:00")
    row = await repo.get_roster(db, rid)
    assert (row["autoping_end_hours"], row["autoping_message"], row["autoping_channel_id"]) == (3, "go", 55)
    assert [r["id"] for r in await repo.autoping_rosters(db)] == [rid]
    clone = await repo.get_roster(db, await repo.clone_roster(db, rid, "Next"))
    assert clone["autoping_end_hours"] == 3 and clone["autoping_end_sent"] is None
    await repo.set_autoping(db, rid, start_hours=None, end_hours=None, message=None, channel_id=None)
    row = await repo.get_roster(db, rid)
    assert row["autoping_end_hours"] is None and row["autoping_end_sent"] == "2026-11-08T00:00:00+00:00"
    assert [r["id"] for r in await repo.autoping_rosters(db)] == [clone["id"]]
    with pytest.raises(ValueError):
        await repo.mark_autoping_sent(db, rid, "middle", "x")


def test_ping_chunks_and_embed_line():
    from clashhero.cogs._roster_views import ping_chunks

    chunks = ping_chunks("Header", range(1000, 1400), limit=200)
    assert chunks[0].startswith("Header\n<@1000>")
    assert all(len(c) <= 200 for c in chunks)
    assert sum(c.count("<@") for c in chunks) == 400
    assert ping_chunks("Only", []) == ["Only"]

    roster = {
        "id": 1,
        "name": "CWL",
        "clan_tag": "#C",
        "category": "cwl",
        "max_members": 15,
        "min_th": None,
        "max_th": None,
        "min_heroes": None,
        "start_time": None,
        "end_time": "2026-11-08T00:00:00+00:00",
        "closed": 0,
        "color": None,
        "sort_by": "th",
        "autoping_end_hours": 6,
        "autoping_start_hours": None,
    }
    e = render_roster_embed(roster, [], [], None, discord.Color.blue())
    assert "🔔 Auto-ping unregistered members 6h before signups close" in e.description


async def test_run_autopings_pings_unregistered_once(db):
    from datetime import UTC, datetime
    from types import SimpleNamespace as NS

    from clashhero.cogs.rosters import Rosters

    sent = []

    async def send(**kw):
        sent.append(kw["content"])

    channel = NS(send=send)
    await add_player(db, "#SIGNED", link=1)
    await add_player(db, "#LAZY", link=2)
    await add_player(db, "#LAZY2", link=2)  # same user, one ping
    await add_player(db, "#SMALL", th=10, link=3)  # below the roster's minimum TH
    await add_player(db, "#NOLINK")
    await add_player(db, "#ELSEWHERE", clan="#X", link=4)
    rid = await make(db, min_th=14, end_time="2026-11-07T15:00:00+00:00")
    await repo.update_roster(db, rid, channel_id=77, message_id=88)
    await repo.add_member(
        db, rid, "#SIGNED", user_id=1, group_id=None, name="S", town_hall=16, heroes_total=300
    )
    await repo.set_autoping(db, rid, start_hours=None, end_hours=6, message=None, channel_id=None)

    cog = Rosters(fake_bot(db, {77: channel}))
    now = datetime(2026, 11, 7, 12, tzinfo=UTC)
    assert await cog.run_autopings(now) == 1
    assert len(sent) == 1
    assert "Signups for **CWL** close" in sent[0] and "<@2>" in sent[0]
    assert "<@1>" not in sent[0] and "<@3>" not in sent[0] and "<@4>" not in sent[0]
    assert "https://discord.com/channels/1/77/88" in sent[0]
    assert await cog.run_autopings(now) == 0 and len(sent) == 1  # once per window

    # a new end time is a new window; a custom message is used
    await repo.update_roster(db, rid, end_time="2026-11-07T16:00:00+00:00", autoping_message="Last call!")
    assert await cog.run_autopings(now) == 1
    assert "Last call!" in sent[-1]

    # closed rosters stay quiet
    await repo.update_roster(db, rid, end_time="2026-11-07T17:00:00+00:00", closed=1)
    assert await cog.run_autopings(now) == 0 and len(sent) == 2


def test_roster_list_line():
    from clashhero.cogs.rosters import roster_list_line

    r = {
        "name": "CWL main",
        "category": "cwl",
        "clan_name": None,
        "clan_tag": "#C",
        "member_count": 3,
        "max_members": 15,
        "closed": 1,
        "autoping_end_hours": 6,
    }
    assert roster_list_line(r, 5) == "` 3/15` **CWL main** · CWL · #C · 🔔 · 🔒"
    plain = {**r, "clan_name": "Clan", "closed": 0, "autoping_end_hours": None}
    assert roster_list_line(plain) == "`3/15` **CWL main** · CWL · Clan"


def test_signup_result_embeds():
    from clashhero.cogs._roster_views import SignupResult
    from clashhero.utils.style import FAILURE, SUCCESS

    ok = SignupResult(added=["A", "B"], updated=["C"], errors=["`#X`: player not found."])
    e = ok.embed()
    assert e.color == SUCCESS
    assert e.description == "✅ Signed up **A**, **B**.\nUpdated the group for **C**."
    assert e.fields[0].name == "Skipped (1)" and e.fields[0].value == "❌ `#X`: player not found."
    one = SignupResult(errors=["This roster is closed."]).embed()
    assert one.color == FAILURE and one.description == "❌ This roster is closed." and not one.fields
    many = SignupResult(errors=["a", "b"]).embed()
    assert many.description == "❌ Nobody was signed up (2 skipped)." and many.fields[0].value == "• a\n• b"
    assert SignupResult().embed().description == "❌ Nothing changed."
    assert "❌ `#X`" in ok.summary()


def test_roster_result_helpers():
    from clashhero.cogs import _roster_views as views
    from clashhero.cogs.rosters import move_embed
    from clashhero.utils.style import FAILURE, SUCCESS

    assert views.clip_lines(["a", "b"]) == "a\nb"
    clipped = views.clip_lines([f"line {i:03}" for i in range(300)], limit=100)
    assert len(clipped) <= 100 and clipped.endswith("more") and clipped.startswith("line 000\n")
    assert views.names_text(["A", "B"]) == "**A**, **B**"
    assert views.names_text([f"N{i}" for i in range(50)], limit=30) == "**N0**, **N1**, **N2**, … 47 more"
    assert views.removed_embed(["A"], "CWL").description == "✅ Removed **A** from **CWL**."
    assert views.removed_embed([], "CWL").color == FAILURE
    applied = views.apply_embed("CWL", "**Confirmed**\nSigned up: A")
    assert applied.description == "✅ Lineup applied to **CWL**.\n\n**Confirmed**\nSigned up: A"
    moved = move_embed(["#A"], ["#B (destination full)"], "Subs")
    assert moved.color == SUCCESS and moved.description == "✅ Moved `1` to **Subs**."
    assert moved.fields[0].name == "Not moved (1)" and moved.fields[0].value == "#B (destination full)"
    assert move_embed([], ["#B"], "Subs").color == FAILURE
