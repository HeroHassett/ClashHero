from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import discord
import pytest

from clashhero.cogs._war_embeds import (
    history_embed,
    lineup_embed,
    period_bounds,
    th_columns,
    war_overview_embed,
)
from clashhero.repos import war as wr
from clashhero.services.emojis import EMOJIS
from clashhero.utils.fmt import clan_link, player_link

NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)


async def add_war(
    db, clan="#C", *, war_type="random", state="warEnded", days_ago=1, per=2, season=None, rnd=None, opp="Opp"
):
    prep = (NOW - timedelta(days=days_ago)).isoformat()
    return await db.execute(
        "INSERT INTO wars (clan_tag, opponent_tag, opponent_name, war_type, cwl_season, cwl_round, "
        "state, team_size, attacks_per_member, preparation_start) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (clan, "#O" + opp, opp, war_type, season, rnd, state, 2, per, prep),
    )


async def add_member(db, war_id, tag, name, th, pos, opp=False):
    await db.execute(
        "INSERT INTO war_members (war_id, player_tag, name, town_hall, map_position, is_opponent) "
        "VALUES (?,?,?,?,?,?)",
        (war_id, tag, name, th, pos, int(opp)),
    )


async def add_attack(
    db, war_id, order, att, dfn, stars, dest, att_th, def_th, *, opp=False, fresh=True, new_stars=None
):
    await db.execute(
        "INSERT INTO war_attacks (war_id, attack_order, attacker_tag, defender_tag, stars, "
        "destruction, is_fresh, new_stars, attacker_th, defender_th, is_opponent, seen_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            war_id,
            order,
            att,
            dfn,
            stars,
            dest,
            int(fresh),
            stars if new_stars is None else new_stars,
            att_th,
            def_th,
            int(opp),
            NOW.isoformat(),
        ),
    )


@pytest.fixture
async def seeded(db):
    """One ended regular war: Alice (TH16) 3★ up + 2★ even; Bob missed both; enemy tripled Bob."""
    w = await add_war(db)
    await add_member(db, w, "#A", "Alice", 16, 1)
    await add_member(db, w, "#B", "Bob", 15, 2)
    await add_member(db, w, "#X", "Xeno", 17, 1, opp=True)
    await add_member(db, w, "#Y", "Yan", 16, 2, opp=True)
    await add_attack(db, w, 1, "#A", "#X", 3, 100.0, 16, 17)
    await add_attack(db, w, 2, "#A", "#Y", 2, 80.0, 16, 16)
    await add_attack(db, w, 3, "#Y", "#B", 3, 100.0, 16, 15, opp=True)
    await add_attack(db, w, 4, "#X", "#A", 1, 50.0, 17, 16, opp=True)
    return w


def test_th_bucket():
    assert wr.th_bucket(15, 16) == "up"
    assert wr.th_bucket(16, 16) == "even"
    assert wr.th_bucket(16, 14) == "down"
    assert wr.th_bucket(None, 14) == "even"


def test_summarize_attacks_and_buckets():
    rows = [
        {"stars": 3, "destruction": 100, "is_fresh": 1, "attacker_th": 16, "defender_th": 17},
        {"stars": 2, "destruction": 80, "is_fresh": 0, "attacker_th": 16, "defender_th": 16},
        {"stars": 0, "destruction": 20, "is_fresh": 1, "attacker_th": 16, "defender_th": 15},
    ]
    s = wr.summarize_attacks(rows, missed=1)
    assert (s.attacks, s.triples, s.zeros, s.missed, s.possible) == (3, 1, 1, 1, 4)
    assert s.town_hall == 16  # from the latest attack
    assert s.avg_stars == pytest.approx(5 / 3)
    assert s.fresh_triple_rate == 0.5
    assert s.bucket_rate("up") == (1, 1.0)
    assert s.bucket_rate("down") == (1, 0.0)


def test_summarize_defenses():
    d = wr.summarize_defenses([{"stars": 3, "destruction": 100}, {"stars": 1, "destruction": 40}])
    assert d.hold_rate == 0.5 and d.avg_stars == 2 and d.town_hall is None
    d = wr.summarize_defenses(
        [{"stars": 1, "destruction": 40, "defender_th": 15}, {"stars": 0, "destruction": 9}]
    )
    assert d.town_hall == 15


def test_remaining_attacks():
    members = [{"tag": "#A", "name": "A", "map_position": 2}, {"tag": "#B", "name": "B", "map_position": 1}]
    left = wr.remaining_attacks(members, [{"attacker_tag": "#A"}, {"attacker_tag": "#A"}], 2)
    assert [(m["tag"], n) for m, n in left] == [("#B", 2)]


async def test_missed_attacks(db, seeded):
    missed = await wr.missed_attacks(db, seeded)
    assert [(m["player_tag"], m["used"], m["allowed"]) for m in missed] == [("#B", 0, 2)]


async def test_attack_leaderboard_includes_missers(db, seeded):
    rows = dict(await wr.attack_leaderboard(db, ["#C"]))
    assert rows["#A"].attacks == 2 and rows["#A"].triples == 1 and rows["#A"].missed == 0
    assert rows["#B"].attacks == 0 and rows["#B"].missed == 2
    assert await wr.attack_leaderboard(db, ["#C"], war_type="cwl") == []
    th15 = await wr.attack_leaderboard(db, ["#C"], town_hall=15)
    assert th15 == []  # TH filter applies to attacks; missers aren't listed


async def test_defense_leaderboard(db, seeded):
    rows = dict(await wr.defense_leaderboard(db, ["#C"]))
    assert rows["#B"].triples_allowed == 1 and rows["#B"].hold_rate == 0
    assert rows["#A"].hold_rate == 1
    assert (rows["#A"].town_hall, rows["#B"].town_hall) == (16, 15)


async def test_leaderboards_carry_town_hall(db, seeded):
    rows = dict(await wr.attack_leaderboard(db, ["#C"]))
    assert rows["#A"].town_hall == 16 and rows["#B"].town_hall is None  # Bob only missed


async def test_count_wars(db, seeded):
    await add_war(db, war_type="cwl", season="2026-11", rnd=1, days_ago=3, opp="R1")
    await add_war(db, state="preparation", days_ago=0, opp="Next")  # not started: not counted
    await add_war(db, days_ago=40, opp="Old")
    assert await wr.count_wars(db, ["#C"]) == 3
    assert await wr.count_wars(db, ["#C"], war_type="cwl") == 1
    assert await wr.count_wars(db, ["#C"], since=NOW - timedelta(days=30)) == 2
    assert await wr.count_wars(db, ["#C"], until=NOW - timedelta(days=30)) == 1
    assert await wr.count_wars(db, []) == 0


async def test_player_war_history(db, seeded):
    h = await wr.player_war_history(db, ["#A"], since=NOW - timedelta(days=30))
    assert h.wars == 1 and h.offense.attacks == 2 and h.defense.defenses == 1
    assert h.recent[0]["attack_order"] == 2  # most recent first
    old = await wr.player_war_history(db, ["#A"], since=NOW)
    assert old.wars == 0 and old.offense.attacks == 0


async def test_period_filter_excludes_old_wars(db, seeded):
    since, until, _ = period_bounds("7d", NOW)
    w = await add_war(db, days_ago=40)
    await add_member(db, w, "#A", "Alice", 16, 1)
    await add_attack(db, w, 1, "#A", "#Z", 0, 10.0, 16, 16)
    rows = dict(await wr.attack_leaderboard(db, ["#C"], since=since, until=until))
    assert rows["#A"].attacks == 2


def test_period_bounds():
    since, until, label = period_bounds("30d", NOW)
    assert since == NOW - timedelta(days=30) and until is None
    s, u, _ = period_bounds("last_season", NOW)
    assert s < u <= NOW
    assert period_bounds("all", NOW)[:2] == (None, None)


def test_cwl_standings_win_bonus_and_prep():
    wars = [
        dict(
            clan_tag="#A",
            clan_name="A",
            clan_stars=20,
            clan_destruction=80.0,
            clan_attacks=15,
            opp_tag="#B",
            opp_name="B",
            opp_stars=18,
            opp_destruction=90.0,
            opp_attacks=15,
            team_size=15,
            state="warEnded",
        ),
        dict(
            clan_tag="#C",
            clan_name="C",
            clan_stars=25,
            clan_destruction=70.0,
            opp_tag="#D",
            opp_name="D",
            opp_stars=25,
            opp_destruction=70.0,
            team_size=15,
            state="warEnded",
        ),
        dict(
            clan_tag="#A",
            clan_name="A",
            clan_stars=5,
            clan_destruction=30.0,
            opp_tag="#C",
            opp_name="C",
            opp_stars=3,
            opp_destruction=20.0,
            team_size=15,
            state="inWar",
        ),
        dict(clan_tag="#E", clan_name="E", opp_tag="#E", opp_name="E", state="preparation"),
    ]
    rows = wr.cwl_standings(wars)
    by = {s.tag: s for s in rows}
    assert by["#A"].total_stars == 20 + 5 + 10 and by["#A"].wins == 1
    assert by["#B"].losses == 1 and by["#C"].ties == 1 and by["#D"].ties == 1
    assert by["#C"].total_stars == 28  # in-progress war counts stars but no bonus
    assert rows[0].tag == "#A" and by["#E"].total_stars == 0


def test_war_result():
    assert wr.war_result(10, 10, 80.0, 70.0) == "win"
    assert wr.war_result(9, 10, 99.0, 1.0) == "lose"
    assert wr.war_result(10, 10, 50.0, 50.0) == "tie"


async def test_cwl_member_stats_and_history(db):
    w1 = await add_war(db, war_type="cwl", season="2026-11", rnd=1, per=1, days_ago=5, opp="R1")
    w2 = await add_war(db, war_type="cwl", season="2026-11", rnd=2, per=1, days_ago=4, opp="R2")
    for w in (w1, w2):
        await add_member(db, w, "#A", "Alice", 16, 1)
        await add_member(db, w, "#E", "Enemy", 16, 1, opp=True)
    await add_attack(db, w1, 1, "#A", "#E", 3, 100.0, 16, 16)
    await add_attack(db, w1, 2, "#E", "#A", 2, 70.0, 16, 16, opp=True)
    stats = await wr.cwl_member_stats(db, "#C", "2026-11")
    a = stats[0]
    assert (a.rounds, a.attacks, a.missed, a.stars, a.triples, a.stars_allowed) == (2, 1, 1, 3, 1, 2)
    hist = await wr.player_cwl_history(db, ["#A"], "2026-11")
    assert [h["cwl_round"] for h in hist] == [2, 1]
    assert hist[1]["attack"]["stars"] == 3 and hist[1]["defense"]["stars"] == 2
    assert hist[0]["attack"] is None
    assert hist[1]["attack"]["target_pos"] == 1  # from war_members when defender_pos is missing
    assert hist[0]["clan_name"] is None  # clan not stored yet


async def test_cwl_history_clan_name_and_stored_position(db):
    await db.execute(
        "INSERT INTO clans (clan_tag, name, updated_at) VALUES ('#C', 'Heroes', ?)", (NOW.isoformat(),)
    )
    w = await add_war(db, war_type="cwl", season="2026-11", rnd=1, per=1, opp="R1")
    await add_member(db, w, "#A", "Alice", 16, 3)
    await add_member(db, w, "#E", "Enemy", 16, 1, opp=True)
    await add_attack(db, w, 1, "#A", "#E", 2, 80.0, 16, 16)
    await db.execute("UPDATE war_attacks SET defender_pos = 7 WHERE war_id = ?", (w,))
    (h,) = await wr.player_cwl_history(db, ["#A"])
    assert (h["clan_name"], h["map_position"], h["attack"]["target_pos"]) == ("Heroes", 3, 7)


async def test_callers(db, seeded):
    await wr.set_caller(db, 1, seeded, 1, player_tag="#A", user_id=5, note="hit me", assigned_by=5)
    await wr.set_caller(db, 1, seeded, 1, player_tag="#B", user_id=6, note=None, assigned_by=6)
    rows = await wr.get_callers(db, 1, seeded)
    assert [(r["position"], r["player_tag"]) for r in rows] == [(1, "#B")]
    assert await wr.clear_caller(db, 1, seeded, 1) == 1
    assert await wr.get_callers(db, 1, seeded) == []


async def test_calls_are_private_to_each_server(db, seeded):
    # Server 1 tracks its own clan; server 2 (the opponent) adds the same clan to snoop.
    await wr.set_caller(db, 1, seeded, 3, player_tag="#A", user_id=5, note="ring base", assigned_by=5)
    assert await wr.get_callers(db, 2, seeded) == []  # can't see it
    assert await wr.clear_caller(db, 2, seeded) == 0  # can't clear it
    assert await wr.clear_caller(db, 2, seeded, 3) == 0
    assert [c["note"] for c in await wr.get_callers(db, 1, seeded)] == ["ring base"]


async def test_old_shared_calls_move_to_the_owning_server(tmp_path):
    from clashhero.db import Database

    path = tmp_path / "old.db"
    db = Database(path)
    await db.connect()
    await db.add_guild_clan(7, "#OURS")
    war_id = await db.execute(
        "INSERT INTO wars (clan_tag, war_type, state, preparation_start) "
        "VALUES ('#OURS', 'cwl', 'inWar', 'x')"
    )
    await db.execute(
        "INSERT INTO war_callers (war_id, position, player_tag, user_id, assigned_by) "
        "VALUES (?, 4, '#A', 5, 5)",
        (war_id,),
    )
    await db.close()
    db = Database(path)  # restart runs the one-time move
    await db.connect()
    assert [(c["position"], c["player_tag"]) for c in await wr.get_callers(db, 7, war_id)] == [(4, "#A")]
    assert await db.fetchval("SELECT COUNT(*) FROM war_callers") == 0
    await db.close()


async def test_current_war_and_names(db, seeded):
    w = await wr.current_war(db, "#C")
    assert w["id"] == seeded
    names = await wr.player_names(db, ["#A", "#X"])
    assert names == {"#A": "Alice", "#X": "Xeno"}


# ------------------------------------------------------------------ embed builders


def fake_war(state="inWar"):
    t = NS(time=NOW)
    ours = NS(
        tag="#C",
        name="Us",
        badge=NS(url="https://badge/us.png"),
        stars=5,
        destruction=60.0,
        attacks_used=3,
        members=[
            NS(tag="#A", name="Alice", town_hall=16, map_position=1),
            NS(tag="#B", name="Bob", town_hall=15, map_position=2),
        ],
    )
    theirs = NS(
        tag="#O",
        name="Them",
        badge=NS(url="https://badge/them.png"),
        stars=2,
        destruction=40.0,
        attacks_used=2,
        members=[
            NS(tag="#X", name="Xeno", town_hall=16, map_position=1),
            NS(tag="#Y", name="Yan", town_hall=16, map_position=2),
        ],
    )
    return NS(
        state=state,
        clan=theirs,
        opponent=ours,
        team_size=2,
        attacks_per_member=2,
        is_cwl=False,
        start_time=t,
        end_time=t,
        attacks=[NS(defender_tag="#X", stars=3)],
    )


def test_war_embeds():
    color = discord.Color.blue()
    e = war_overview_embed(fake_war(), "#C", color)
    # Our clan (badge, linked) as the author; the opponent linked in the title, its badge as the thumbnail.
    assert (e.author.name, e.author.url, e.author.icon_url) == ("Us", clan_link("#C"), "https://badge/us.png")
    assert (e.title, e.url, e.thumbnail.url) == ("War vs Them", clan_link("#O"), "https://badge/them.png")
    state, timer = e.description.split("\n")
    assert state == "**Battle day** · 2 vs 2"
    assert timer.startswith("▲ Winning · ends <t:")
    # A column per clan, ours first and named: score, attacks, then Town Halls lined up row for row.
    assert [(f.name, f.inline) for f in e.fields] == [("Us", True), ("Them", True)]
    assert e.fields[0].value.split("\n") == [
        "★ **5** · 60.00%",
        "⚔️ **3**/4 attacks",
        "",
        "TH16 **1**",
        "TH15 **1**",
    ]
    assert e.fields[1].value.split("\n") == [
        "★ **2** · 40.00%",
        "⚔️ **2**/4 attacks",
        "",
        "TH16 **2**",
        "TH15 –",
    ]

    prep = war_overview_embed(fake_war("preparation"), "#C", color, label="CWL round 3")
    assert prep.title == "CWL round 3 vs Them"
    assert prep.description.split("\n")[1].startswith("Battle day starts <t:")
    assert "★" not in prep.fields[0].value and prep.fields[0].value == "TH16 **1**\nTH15 **1**"

    ended = war_overview_embed(fake_war("warEnded"), "#C", color)
    assert ended.description.startswith("▲ **Victory** · 2 vs 2\nEnded <t:")

    lineup = lineup_embed(fake_war(), "#C", color)
    assert lineup.title == "War lineup vs Them" and lineup.author.name == "Us"
    assert "Alice" in lineup.description and "★★★" in lineup.description


def test_war_overview_icons(monkeypatch):
    icons = ("ui_war_star", "ui_sword", "ui_arrow_down", "th15", "th16")
    monkeypatch.setattr(EMOJIS, "_by_name", {n: f"<:{n}:1>" for n in icons})
    w = fake_war()
    w.opponent.stars = 1  # we're behind now
    e = war_overview_embed(w, "#C", discord.Color.blue())
    assert e.description.split("\n")[1].startswith("<:ui_arrow_down:1> Losing · ends <t:")
    assert e.fields[0].value.split("\n") == [
        "<:ui_war_star:1> **1** · 60.00%",
        "<:ui_sword:1> **3**/4 attacks",
        "",
        "<:th16:1> **1**",
        "<:th15:1> **1**",
    ]
    assert "`" not in e.description + "".join(f.value for f in e.fields)  # icons never go in code


def test_th_columns():
    w = fake_war()
    assert th_columns(w.opponent.members, w.clan.members) == (
        ["TH16 **1**", "TH15 **1**"],
        ["TH16 **2**", "TH15 –"],
    )
    assert th_columns([], []) == ([], [])


async def test_history_embed(db, seeded):
    h = await wr.player_war_history(db, ["#A"])
    e = history_embed("Alice", "all time", h, discord.Color.blue(), tag="#A")
    assert (e.title, e.url) == ("Alice (#A)", player_link("#A"))
    assert e.description == "⚔️ **War history** · all time · **1** war"
    offense, defense, rates, recent = (f.value for f in e.fields)
    assert [f.name for f in e.fields] == ["Offense", "Defense", "Three-star rate", "Recent attacks"]
    assert offense.split("\n") == ["⚔️ **2** attacks", "★ **2.50** avg stars", "**90.0%** avg destruction"]
    assert defense.split("\n") == [
        "🛡️ **1** times attacked",
        "**100%** held, not tripled",
        "★ **1.00** avg stars allowed",
    ]
    assert rates.split("\n") == [
        "★★★ **50%** of all attacks (1/2)",
        "**50%** as the first hit on a base (1/2)",
        "**100%** against a higher Town Hall (1/1)",
        "**0%** against the same Town Hall (0/1)",
        "**–** against a lower Town Hall (0/0)",
    ]
    assert recent.split("\n") == ["★★☆ **80%** TH16 → TH16 vs Opp", "★★★ **100%** TH16 → TH17 vs Opp"]
    several = history_embed("Ann (2 accounts)", "all time", h, discord.Color.blue())
    assert (several.title, several.url) == ("Ann (2 accounts)", None)


async def test_history_embed_icons(db, seeded, monkeypatch):
    icons = ("ui_star_old", "ui_star_new", "ui_star_empty", "ui_arrow_down", "th16", "th17")
    monkeypatch.setattr(EMOJIS, "_by_name", {n: f"<:{n}:1>" for n in icons})
    await db.execute("UPDATE war_attacks SET new_stars = 1 WHERE attack_order = 2")  # 1 of its 2 stars new
    h = await wr.player_war_history(db, ["#A"])
    h.offense.missed = 1
    e = history_embed("Alice", "all time", h, discord.Color.blue(), tag="#A")
    assert "<:ui_arrow_down:1> **1** missed" in e.fields[0].value
    # Silver for a star an earlier attack already took, gold for new ones, empty for the rest.
    assert e.fields[3].value.split("\n")[0] == (
        "<:ui_star_old:1><:ui_star_new:1><:ui_star_empty:1> **80%** <:th16:1> → <:th16:1> vs Opp"
    )


# ------------------------------------------------------------------ commands


class Sent:
    def __init__(self):
        self.sent = []

    async def defer(self, **kwargs):
        pass

    async def send(self, content=None, **kwargs):
        self.sent.append({"content": content, **kwargs})

    async def send_message(self, content=None, **kwargs):
        self.sent.append({"content": content, **kwargs})


def interaction_for(db, coc=None):
    from clashhero.cogs.war import War

    bot = NS(db=db, coc=coc)
    reply = Sent()
    interaction = NS(client=bot, guild_id=1, channel_id=10, user=NS(id=5), response=reply, followup=reply)
    return War(bot), interaction, reply.sent


async def test_remaining_pings_in_the_message_when_asked(db):
    from clashhero.cogs.war import War

    w = fake_war()
    w.opponent.attacks = [NS(attacker_tag="#A")]  # Alice has 1 left, Bob 2
    await db.link_player("#B", 77)
    await db.add_guild_clan(1, "#C")  # the server's only clan, so the command picks it

    class Coc:
        async def get_current_war(self, tag):
            return w

    cog, interaction, sent = interaction_for(db, Coc())
    await War.remaining.callback(cog, interaction, None, True)
    [msg] = sent
    assert msg["content"] == "<@77>"  # mentions inside an embed never notify
    assert msg["allowed_mentions"].users is True
    assert "**2.** TH15 Bob <@77>" in msg["embed"].description
    assert msg["embed"].author.name == "Us"
    await War.remaining.callback(cog, interaction, None, False)
    assert sent[1]["content"] is None and "<@" not in sent[1]["embed"].description


async def test_war_log_command(db):
    from clashhero.cogs.war import War

    entry = NS(
        result=NS(value="win"),
        end_time=NS(time=NOW),
        team_size=15,
        is_league_entry=False,
        clan=NS(name="Us", stars=40, destruction=99.0, badge=NS(url="https://badge/us.png")),
        opponent=NS(name="Them", stars=30, destruction=80.0),
    )

    class Coc:
        async def get_war_log(self, tag, limit):
            return [entry]

    cog, interaction, sent = interaction_for(db, Coc())
    await War.log.callback(cog, interaction, "#2PP", 10)
    e = sent[0]["embed"]
    assert e.title == "War log"
    assert (e.author.name, e.author.icon_url) == (
        "Us",
        "https://badge/us.png",
    )  # untracked: the log's own clan
    assert [f.name for f in e.fields] == ["▲ Victory vs Them (15 vs 15)"]


async def test_calls_show_the_clan_from_the_clan_row_for_old_wars(db, seeded):
    from clashhero.cogs.war import War

    await db.add_guild_clan(1, "#C")
    await db.execute(
        "INSERT INTO clans (clan_tag, name, badge_url, updated_at) "
        "VALUES ('#C', 'Heroes', 'https://b/h.png', 'x')"
    )
    await db.execute("UPDATE wars SET state = 'inWar' WHERE id = ?", (seeded,))
    await wr.set_caller(db, 1, seeded, 2, player_tag="#A", user_id=5, note=None, assigned_by=5)
    cog, interaction, sent = interaction_for(db)
    await War.caller_list.callback(cog, interaction, None)
    e = sent[0]["embed"]
    assert (e.author.name, e.author.icon_url) == ("Heroes", "https://b/h.png")
    assert e.title == "War calls vs Opp"
    assert e.description == "**2.** TH16 Yan ★★☆ → **Alice** <@5>"  # Alice took 2★ on Yan
    assert e.footer.text == "1 of 2 bases claimed"
