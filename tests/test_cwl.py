import io
from types import SimpleNamespace as NS

from PIL import Image

from clashhero.cogs.cwl import CWL, current_round, cwl_movement, season_title
from clashhero.services import cards
from clashhero.utils.fmt import clan_link, player_link

SIZE = 5
A, B, C, D = "#2PP", "#2YY", "#2LL", "#2QQ"


def war(a, b, a_stars, b_stars, a_dest, b_dest, state="warEnded", tag=None):
    return {
        "war_tag": tag,
        "clan_tag": a,
        "clan_name": f"Clan {a}",
        "clan_stars": a_stars,
        "clan_destruction": a_dest,
        "clan_attacks": SIZE,
        "opp_tag": b,
        "opp_name": f"Clan {b}",
        "opp_stars": b_stars,
        "opp_destruction": b_dest,
        "opp_attacks": SIZE,
        "team_size": SIZE,
        "state": state,
    }


# After round 1: A 13, C 12, D 2, B 1. After round 2: C 25, B 14 (140%), A 14 (120%), D 2.
ROUND_1 = [war(A, B, 3, 1, 90, 40), war(C, D, 2, 2, 60, 50)]
ROUND_2 = [war(A, C, 1, 3, 30, 100), war(B, D, 3, 0, 100, 10)]
ROUND_3 = [war(A, D, 3, 0, 100, 0, state="inWar"), war(B, C, 0, 0, 0, 0, state="inWar")]
ROUND_4 = [war(A, B, 0, 0, 0, 0, state="preparation")]


def test_movement_compares_the_last_two_finished_rounds():
    assert cwl_movement([ROUND_1, ROUND_2, ROUND_3, ROUND_4]) == {
        C: "up",
        B: "up",
        A: "down",
        D: "down",
    }


def test_movement_is_unknown_before_two_finished_rounds():
    assert cwl_movement([]) == {}
    assert cwl_movement([ROUND_1]) == {}
    assert cwl_movement([ROUND_1, ROUND_3]) == {}


def test_movement_same_when_order_holds():
    round_2 = [war(A, C, 3, 0, 100, 0), war(B, D, 1, 2, 30, 50)]
    moves = cwl_movement([ROUND_1, round_2])
    assert moves[A] == "same"
    assert set(moves.values()) <= {"up", "down", "same"}


def test_current_round_and_season_title():
    assert current_round([ROUND_1, ROUND_2, ROUND_3, ROUND_4]) == 3
    assert current_round([ROUND_4]) == 0
    assert current_round([]) == 0
    assert season_title("2026-11") == "November 2026"
    assert season_title("soon") == "soon"


# ------------------------------------------------------------------ /cwl stats


def coc_war(w, war_tag):
    def side(prefix):
        return NS(
            tag=w[f"{prefix}_tag"],
            name=w[f"{prefix}_name"],
            stars=w[f"{prefix}_stars"],
            destruction=w[f"{prefix}_destruction"],
            attacks_used=SIZE,
        )

    return NS(
        war_tag=war_tag,
        clan=side("clan"),
        opponent=side("opp"),
        team_size=SIZE,
        state=w["state"],
    )


class FakeCoc:
    def __init__(self):
        rounds = [ROUND_1, ROUND_2, ROUND_3]
        self.wars = {
            f"#W{r}{i}": coc_war(w, f"#W{r}{i}") for r, rnd in enumerate(rounds) for i, w in enumerate(rnd)
        }
        self.group = NS(
            season="2026-11",
            number_of_rounds=7,
            rounds=[[f"#W{r}{i}" for i in range(len(rnd))] for r, rnd in enumerate(rounds)],
            clans=[
                NS(
                    tag=t,
                    name=f"Clan {t}",
                    badge=NS(medium=f"https://api-assets.clashofclans.com/b/{t[1:]}.png"),
                    members=[NS(town_hall=th) for th in (17, 16, 16)],
                )
                for t in (A, B, C, D)
            ],
        )

    async def get_league_group(self, tag):
        return self.group

    async def get_league_wars(self, tags):
        for t in reversed(tags):  # order shouldn't matter: wars are matched to rounds by war tag
            yield self.wars[t]

    async def get_clan(self, tag):
        return NS(war_league=NS(name="Crystal League I"))


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)


class FakeResponse:
    def __init__(self):
        self.sent = []

    async def defer(self, **kwargs):
        pass

    async def send_message(self, *args, **kwargs):
        self.sent.append(kwargs)


def fake_interaction(db):
    bot = NS(db=db, coc=FakeCoc())
    return NS(
        client=bot,
        guild_id=1,
        channel_id=10,
        user=NS(id=99, display_name="Ann"),
        response=FakeResponse(),
        followup=FakeFollowup(),
    )


async def run_stats(db):
    interaction = fake_interaction(db)
    await CWL.stats.callback(CWL(interaction.client), interaction, A)
    [msg] = interaction.followup.sent
    return msg


async def test_stats_attaches_the_card(db, monkeypatch):
    seen = {}

    async def no_badges(urls):
        urls = list(urls)
        seen["urls"] = urls
        return dict.fromkeys(urls)

    real_render = cards.render_cwl_standings

    def render(**kwargs):
        seen["render"] = kwargs
        return real_render(**kwargs)

    monkeypatch.setattr(cards, "fetch_badges", no_badges)
    monkeypatch.setattr(cards, "render_cwl_standings", render)
    msg = await run_stats(db)

    assert msg["embed"].image.url == "attachment://cwl.png"
    assert msg["file"].filename == "cwl.png"
    im = Image.open(io.BytesIO(msg["file"].fp.read()))
    assert im.size == (1000, cards.card_height(4))
    e = msg["embed"]
    assert e.title == "CWL standings · November 2026"
    assert (e.author.name, e.author.icon_url) == (
        f"Clan {A}",
        "https://api-assets.clashofclans.com/b/2PP.png",
    )
    head, ours = e.description.split("\n")[:2]
    assert head == "**Crystal League I** · Round **3**/7"  # round 3 is on its battle day
    assert ours == f"▼ **Clan {A}** · **2nd** of 4 · ★ **17** stars"  # dropped from 1st after round 2
    assert seen["urls"] == [f"https://api-assets.clashofclans.com/b/{t[1:]}.png" for t in (A, B, C, D)]

    kw = seen["render"]
    assert (kw["season"], kw["round_label"], kw["league"], kw["highlight"]) == (
        "November 2026",
        "3/7",
        "Crystal League I",
        A,
    )
    assert [(r.rank, r.tag, r.movement) for r in kw["rows"]][:2] == [(1, C, "up"), (2, A, "down")]


async def test_stats_falls_back_to_text_when_the_card_fails(db, monkeypatch, caplog):
    async def no_badges(urls):
        return dict.fromkeys(urls)

    def broken(**kwargs):
        raise RuntimeError("no fonts")

    monkeypatch.setattr(cards, "fetch_badges", no_badges)
    monkeypatch.setattr(cards, "render_cwl_standings", broken)
    msg = await run_stats(db)

    assert "file" not in msg
    assert msg["embed"].image.url is None
    assert "Round **3**/7" in msg["embed"].description
    assert any("standings card" in r.message for r in caplog.records)


async def test_members_lists_each_clans_town_halls_ours_first(db):
    interaction = fake_interaction(db)
    await CWL.members.callback(CWL(interaction.client), interaction, C)
    [msg] = interaction.followup.sent
    e = msg["embed"]
    assert e.title == "CWL group · November 2026"
    assert (e.author.name, e.author.url) == (f"Clan {C}", clan_link(C))
    assert [f.name for f in e.fields][:2] == [f"★ Clan {C} · 3 players", f"Clan {A} · 3 players"]
    assert e.fields[0].value == "TH17 **1**  TH16 **2**"


async def add_cwl_season(db):
    await db.add_guild_clan(1, A)
    await db.execute(
        "INSERT INTO clans (clan_tag, name, badge_url, updated_at) "
        "VALUES (?, 'Heroes', 'https://b/h.png', 'x')",
        (A,),
    )
    for rnd in (1, 2):
        w = await db.execute(
            "INSERT INTO wars (clan_tag, opponent_tag, opponent_name, war_type, cwl_season, cwl_round, "
            "state, team_size, attacks_per_member, preparation_start) "
            "VALUES (?, ?, 'Opp', 'cwl', '2026-11', ?, 'warEnded', 1, 1, ?)",
            (A, B, rnd, f"2026-11-0{rnd}T00:00:00+00:00"),
        )
        await db.execute(
            "INSERT INTO war_members (war_id, player_tag, name, town_hall, map_position, is_opponent) "
            "VALUES (?, '#2PQ', 'Pat', 17, 1, 0), (?, '#2EE', 'Enemy', 17, 1, 1)",
            (w, w),
        )
        if rnd == 1:
            await db.execute(
                "INSERT INTO war_attacks (war_id, attack_order, attacker_tag, defender_tag, stars, "
                "destruction, is_fresh, new_stars, attacker_th, defender_th, is_opponent, seen_at) "
                "VALUES (?, 1, '#2PQ', '#2EE', 3, 100.0, 1, 3, 17, 17, 0, 'x')",
                (w,),
            )


async def test_season_tables_show_the_clan_and_a_summary(db):
    await add_cwl_season(db)
    interaction = fake_interaction(db)
    cog = CWL(interaction.client)
    await CWL.attacks.callback(cog, interaction, A, "2026-11")
    await CWL.stars_.callback(cog, interaction, A, "2026-11")
    attacks, stars = (m["embed"] for m in interaction.response.sent)
    assert attacks.title == "CWL attacks · November 2026" and stars.title == "CWL stars · November 2026"
    for e in (attacks, stars):
        assert (e.author.name, e.author.icon_url) == ("Heroes", "https://b/h.png")
    assert attacks.description.startswith("⚔️ **1**/2 attacks used · ▼ **1** missed\n```")
    assert stars.description.startswith("★ **3** stars · ★★★ **1** triples · **100%** destruction\n```")


async def test_history_titles_the_player(db):
    await add_cwl_season(db)
    interaction = fake_interaction(db)
    await CWL.history.callback(CWL(interaction.client), interaction, "#2PQ", None, "2026-11")
    [msg] = interaction.response.sent
    e = msg["embed"]
    assert (e.title, e.url) == ("Pat (#2PQ)", player_link("#2PQ"))
    assert e.description == "🏆 **CWL history** · November 2026"
    assert e.fields[0].value.split("\n")[:2] == ["🏆 **Nov 2026**", "Heroes · TH17 · map position **1**"]
