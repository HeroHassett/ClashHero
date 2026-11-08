from dataclasses import replace
from types import SimpleNamespace as NS

from clashhero.cogs._legend_embeds import count, week_embed
from clashhero.repos.legends import DayStats, WeekDay, board_entries, day_stats, legend_members
from clashhero.services.snapshots import MemberSnap, PlayerSnap, infer_battles
from clashhero.utils.ranked import (
    LEAGUE_TIERS,
    daily_attack_cap,
    is_legend_one,
    is_legend_tier,
    is_tracked_ranked,
    league_name,
    tier_number,
    weekly_attack_cap,
)


def api_obj(tier, legacy="Unranked", **kw):
    """Shaped like the live API: legacy league reads Unranked, leagueTier holds the real tier."""
    raw = {"leagueTier": {"id": 105000000 + tier_number(tier), "name": tier, "iconUrls": {"small": "s"}}}
    return NS(_raw_data=raw, league=NS(name=legacy), **kw)


def test_tier_catalogue():
    assert len(LEAGUE_TIERS) == 37
    assert tier_number("Unranked") == 0 and tier_number("Legend I") == 36
    assert tier_number("legend league") == 36  # legacy name


def test_tracked_tiers():
    assert not is_tracked_ranked("Titan League 27")
    assert is_tracked_ranked("Dragon League 28") and is_tracked_ranked("Electro League 33")
    assert is_tracked_ranked("Legend III") and is_tracked_ranked("Legend I")
    assert not is_tracked_ranked(None) and not is_tracked_ranked("Unranked")
    assert is_legend_tier("Legend II") and not is_legend_tier("Electro League 33")
    assert is_legend_one("Legend I") and not is_legend_one("Legend II")


def test_caps():
    assert daily_attack_cap("Legend I") == 8
    assert daily_attack_cap("Legend II") is None
    assert weekly_attack_cap("Legend II") == 30 and weekly_attack_cap("Legend III") == 24
    expected = {1: 6, 6: 6, 7: 8, 12: 8, 13: 10, 18: 10, 19: 12, 27: 12, 28: 14, 30: 14, 31: 18, 33: 18}
    for n, cap in expected.items():
        name = next(t for t in LEAGUE_TIERS if t.endswith(f" {n}"))
        assert weekly_attack_cap(name) == cap, name
    assert weekly_attack_cap("Legend I") is None  # daily cap instead
    assert weekly_attack_cap("Unranked") is None and weekly_attack_cap(None) is None


def test_league_name_prefers_league_tier():
    assert league_name(api_obj("Legend I")) == "Legend I"
    assert league_name(NS(league=NS(name="Legend League"))) == "Legend League"
    assert league_name(NS(league=None)) is None


def test_snapshots_read_league_tier():
    m = api_obj(
        "Electro League 32",
        tag="#A",
        name="A",
        role=None,
        town_hall=16,
        trophies=3000,
        donations=0,
        received=0,
    )
    assert MemberSnap.from_coc(m).league == "Electro League 32"


def _player(league, trophies=5000):
    return PlayerSnap(
        tag="#P",
        name="P",
        town_hall=17,
        exp_level=1,
        trophies=trophies,
        best_trophies=0,
        league=league,
        attack_wins=0,
        defense_wins=0,
        war_stars=0,
        war_opted_in=None,
        clan_tag=None,
        clan_role=None,
        games_points=0,
        capital_contributions=0,
        heroes_total=0,
        donations=0,
        received=0,
    )


def test_inference_on_lower_tiers_and_zero_win_counters():
    # The live API reports attackWins/defenseWins as 0, so inference must work from trophies alone.
    old = _player("Dragon League 29", 2500)
    assert [(b.kind, b.trophies) for b in infer_battles(old, replace(old, trophies=2538))] == [("attack", 38)]
    low = _player("Titan League 27", 2000)
    assert infer_battles(low, replace(low, trophies=2030)) == []


def test_count_display():
    assert count([1, 2, 3], 8) == "3/8"
    assert count([1, 2, 3], None) == "3"


def test_week_embed():
    days = [WeekDay(f"2026-11-0{2 + i}", 3000, 3040, DayStats("#P", [40], [], cap=None)) for i in range(7)]
    e = week_embed(name="P", tag="#P", league="Legend II", week_id="2026-11-02", days=days, weekly_cap=30)
    fields = {f.name: f.value for f in e.fields}
    assert fields["Attacks"] == "7/30" and fields["Net"] == "+280"


async def test_day_caps_follow_tier(db):
    await db.execute(
        "INSERT INTO players (player_tag, name, league, clan_tag, trophies, updated_at) VALUES "
        "('#L1','a','Legend I','#C',5000,'x'), ('#L2','b','Legend II','#C',4000,'x'), "
        "('#T','c','Titan League 27','#C',2000,'x')"
    )
    assert (await day_stats(db, "#L1", "2026-11-07")).cap == 8
    assert (await day_stats(db, "#L2", "2026-11-07")).cap is None
    entries = {e.player_tag: e.stats.cap for e in await board_entries(db, ["#L1", "#L2"], "2026-11-07")}
    assert entries == {"#L1": 8, "#L2": None}
    assert sorted(await legend_members(db, ["#C"])) == ["#L1", "#L2"]
