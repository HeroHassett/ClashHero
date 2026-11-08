from datetime import UTC, datetime, timedelta

import pytest

from clashhero.repos import roster_scoring as S

NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)


# ------------------------------------------------------------------ math


def test_wilson_lower_bound():
    assert S.wilson_lower_bound(0, 0) == 0.0
    assert S.wilson_lower_bound(0, 5) == 0.0
    assert S.wilson_lower_bound(5, 10, z=0) == pytest.approx(0.5)
    # a perfect tiny sample ranks below a strong large one
    assert S.wilson_lower_bound(3, 3) < S.wilson_lower_bound(40, 50)
    assert S.wilson_lower_bound(1, 1) < 0.5
    for s, n in ((1, 2), (7, 10), (30, 40), (50, 50)):
        assert 0 <= S.wilson_lower_bound(s, n) <= s / n
    # more evidence at the same rate -> tighter (higher) bound
    assert S.wilson_lower_bound(8, 10) < S.wilson_lower_bound(80, 100)


def test_shrink_and_activity_and_strength():
    assert S.shrink(None, 0, 0.4) == 0.4
    assert S.shrink(0.9, 0, 0.4) == 0.4
    assert S.shrink(1.0, 4, 0.5, k=4) == pytest.approx(0.75)
    assert S.shrink(0.0, 1000, 0.5) < 0.01

    assert S.activity_score(None) == S.NEUTRAL
    assert S.activity_score(0.5) == 1.0
    assert S.activity_score(S.INACTIVE_DAYS) == 0.0
    assert S.activity_score(7.5) == pytest.approx(0.5)
    assert S.days_since("2026-11-06T12:00:00+00:00", NOW) == pytest.approx(1.0)
    assert S.days_since(None, NOW) is None

    assert S.strength_score(17, 400, (15, 17), 400) == 1.0
    assert S.strength_score(15, 200, (15, 17), 400) == pytest.approx(0.25)
    assert S.strength_score(16, 0, (16, 16), 0) == 1.0  # one TH, no hero data in the pool
    assert S.strength_score(None, None, (15, 17), 400) == 0.0


# ------------------------------------------------------------------ history from seeded wars


class Seeder:
    def __init__(self, db):
        self.db = db
        self.order = 0

    async def war(self, type_="random", season=None, prep="2026-10-01", state="warEnded", clan="#C"):
        apm = 1 if type_ == "cwl" else 2
        return await self.db.execute(
            "INSERT INTO wars (clan_tag, war_type, cwl_season, state, preparation_start, "
            "attacks_per_member) VALUES (?, ?, ?, ?, ?, ?)",
            (clan, type_, season, state, f"{prep}T00:00:00+00:00", apm),
        )

    async def member(self, war_id, tag, th=16, opponent=0):
        await self.db.execute(
            "INSERT INTO war_members (war_id, player_tag, name, town_hall, map_position, is_opponent) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (war_id, tag, tag[1:], th, opponent),
        )

    async def attack(self, war_id, tag, stars, a_th=16, d_th=16, opponent=0):
        self.order += 1
        await self.db.execute(
            "INSERT INTO war_attacks (war_id, attack_order, attacker_tag, defender_tag, stars, "
            "destruction, is_fresh, attacker_th, defender_th, is_opponent, seen_at) "
            "VALUES (?, ?, ?, '#D', ?, 90, 1, ?, ?, ?, 'x')",
            (war_id, self.order, tag, stars, a_th, d_th, opponent),
        )


async def seed(db):
    s = Seeder(db)
    # CWL: 2026-07 is outside the last 3 seasons but inside the war lookback
    for season, stars in (("2026-07", 0), ("2026-09", 2), ("2026-10", 3)):
        w = await s.war("cwl", season, prep=f"{season}-02")
        await s.member(w, "#ACE")
        await s.attack(w, "#ACE", stars)
    dip = await s.war("cwl", "2026-10", prep="2026-10-03")
    await s.member(dip, "#ACE")
    await s.attack(dip, "#ACE", 3, a_th=16, d_th=15)  # hit down: avg stars only
    await s.member(dip, "#MISS")  # in the lineup, never attacked
    live = await s.war("cwl", "2026-11", prep="2026-11-02", state="inWar")
    await s.member(live, "#ACE")
    await s.attack(live, "#ACE", 3)  # counts for rates; availability only once ended

    regular = await s.war("random", prep="2026-10-20")
    for tag in ("#ACE", "#MISS"):
        await s.member(regular, tag)
    await s.attack(regular, "#ACE", 3)
    await s.attack(regular, "#ACE", 3)
    await s.attack(regular, "#MISS", 1)
    await s.attack(regular, "#OPP", 3, opponent=1)  # their side

    ongoing = await s.war("random", prep="2026-11-06", state="inWar")
    await s.member(ongoing, "#MISS")  # no attacks yet, war not over

    friendly = await s.war("friendly", prep="2026-10-25")
    await s.member(friendly, "#ACE")
    await s.attack(friendly, "#ACE", 3)
    await s.attack(friendly, "#ACE", 3)

    old = await s.war("random", prep="2025-01-01")
    await s.member(old, "#ACE")
    await s.attack(old, "#ACE", 0)

    # another tracked clan's war where #ACE was the opponent
    theirs = await s.war("random", prep="2026-10-21", clan="#OTHER")
    await s.member(theirs, "#ACE", opponent=1)
    await s.attack(theirs, "#ACE", 0, opponent=1)


async def test_load_histories_from_seeded_wars(db):
    await seed(db)
    assert await S.recent_cwl_seasons(db, 3) == ["2026-11", "2026-10", "2026-09"]
    hist, seasons = await S.load_histories(db, ["#ACE", "#MISS", "#NOBODY"], cwl_seasons=3, now=NOW)
    assert seasons == ["2026-11", "2026-10", "2026-09"]
    assert hist["#ACE"] == S.History(
        cwl_hits=3,  # 09, 10, 11 (not 07: too old; not the dip)
        cwl_triples=2,
        war_hits=6,  # every CWL + regular fair hit in the last 180 days
        war_triples=4,
        attacks=7,  # + the dip, no friendlies, nothing older than the lookback
        stars=17,
        used=6,  # CWL 07, 09, 10, 10 (1 each) + regular (2); live CWL war excluded
        available=6,
    )
    assert hist["#MISS"] == S.History(war_hits=1, war_triples=0, attacks=1, stars=1, used=1, available=3)
    assert "#NOBODY" not in hist and "#OPP" not in hist

    hist1, seasons1 = await S.load_histories(db, ["#ACE"], cwl_seasons=1, now=NOW)
    assert seasons1 == ["2026-11"]
    assert (hist1["#ACE"].cwl_hits, hist1["#ACE"].cwl_triples) == (1, 1)


async def test_load_histories_empty_db(db):
    hist, seasons = await S.load_histories(db, ["#A"], now=NOW)
    assert hist == {} and seasons == []


# ------------------------------------------------------------------ scoring


def cand(tag, th=16, heroes=300, seen=NOW, **kw):
    return S.Candidate(tag, tag[1:], th, heroes, seen.isoformat() if seen else None, **kw)


def H(cwl=(0, 0), war=(0, 0), stars=(0, 0), used=(0, 0)):
    return S.History(
        cwl_triples=cwl[0],
        cwl_hits=cwl[1],
        war_triples=war[0],
        war_hits=war[1],
        stars=stars[0],
        attacks=stars[1],
        used=used[0],
        available=used[1],
    )


def test_score_ranks_proven_over_unknown_over_poor():
    hist = {
        "#VET": H(cwl=(12, 15), war=(30, 40), stars=(110, 40), used=(40, 40)),
        "#BAD": H(cwl=(3, 15), war=(10, 40), stars=(70, 40), used=(30, 40)),
        "#LUCKY": H(cwl=(1, 1), war=(1, 1), stars=(3, 1), used=(1, 1)),
    }
    cands = [cand("#BAD"), cand("#NEW"), cand("#LUCKY"), cand("#VET")]
    ranked = S.score_candidates(cands, hist, NOW)
    order = [s.candidate.tag for s in ranked]
    assert order[0] == "#VET" and order[-1] == "#BAD"
    assert order.index("#LUCKY") > order.index("#VET")
    new = next(s for s in ranked if s.candidate.tag == "#NEW")
    assert new.history.empty
    # no history = the pool's typical value on every history part
    for part in S.HISTORY_PARTS:
        values = sorted(s.parts[part] for s in ranked if s.candidate.tag != "#NEW")
        assert values[0] <= new.parts[part] <= values[-1]
    assert all(0 <= v <= 1 for s in ranked for v in s.parts.values())
    assert all(0 <= s.score <= 100 for s in ranked)


def test_score_without_history_uses_strength_and_activity():
    cands = [
        cand("#LOW", th=15, heroes=250),
        cand("#IDLE", th=17, heroes=450, seen=NOW - timedelta(days=20)),
        cand("#TOP", th=17, heroes=450),
        cand("#UNSEEN", th=17, heroes=450, seen=None),
    ]
    ranked = S.score_candidates(cands, {}, NOW)
    assert [s.candidate.tag for s in ranked] == ["#TOP", "#UNSEEN", "#IDLE", "#LOW"]
    top = ranked[0]
    assert top.parts["activity"] == 1.0 and top.parts["strength"] == 1.0
    assert top.seen_days == 0 and ranked[1].seen_days is None
    # with no history anywhere, history parts are the neutral constant
    assert {top.parts[p] for p in S.HISTORY_PARTS} == {S.NEUTRAL}


def test_score_weights_sum_to_one_and_ties_break_on_strength():
    assert sum(S.WEIGHTS.values()) == pytest.approx(1.0)
    assert set(S.WEIGHTS) == set(S.PART_LABELS)
    a, b = cand("#A", th=16, heroes=300), cand("#B", th=16, heroes=300)
    ranked = S.score_candidates([b, a], {}, NOW)
    assert [s.candidate.tag for s in ranked] == ["#A", "#B"]  # equal: by name
    assert "CWL 3★ 30%" in S.describe_weights()


def test_split_eligible_and_pick():
    roster = {"min_th": 14, "max_th": None, "min_heroes": None, "allow_unlinked": 0, "allow_multi_signup": 0}
    cands = [cand("#LOWTH", th=13), cand("#NOLINK"), cand("#ELSEWHERE"), cand("#OK")]
    ok, excluded = S.split_eligible(
        roster, cands, linked={"#LOWTH", "#ELSEWHERE", "#OK"}, other_rosters={"#ELSEWHERE": "CWL 2"}
    )
    assert [c.tag for c in ok] == ["#OK"]
    reasons = {c.tag: r for c, r in excluded}
    assert reasons["#LOWTH"].startswith("needs TH14")
    assert reasons["#NOLINK"] == "not linked to a Discord user"
    assert reasons["#ELSEWHERE"] == "already on CWL 2"

    relaxed = {**roster, "allow_unlinked": 1, "allow_multi_signup": 1}
    ok, excluded = S.split_eligible(relaxed, cands, linked=set(), other_rosters={"#ELSEWHERE": "x"})
    assert len(ok) == 3 and [c.tag for c, _ in excluded] == ["#LOWTH"]

    ranked = S.score_candidates([cand(f"#P{i}", th=10 + i) for i in range(6)], {}, NOW)
    top, subs = S.pick(ranked, 3, 2)
    assert [s.candidate.tag for s in top] == ["#P5", "#P4", "#P3"]
    assert [s.candidate.tag for s in subs] == ["#P2", "#P1"]


def test_candidate_from_row():
    row = {
        "player_tag": "#A",
        "name": None,
        "town_hall": 16,
        "heroes_total": 320,
        "last_seen": None,
        "user_id": 5,
        "group_name": "Confirmed",
        "signed_up": 1,
    }
    c = S.Candidate.from_row(row)
    assert (c.name, c.town_hall, c.heroes, c.user_id, c.group, c.signed_up) == (
        "#A",
        16,
        320,
        5,
        "Confirmed",
        True,
    )
