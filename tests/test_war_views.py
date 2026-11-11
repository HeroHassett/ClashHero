"""Layout of the war / CWL / stats views: block layout, icons, chip alignment and Discord size limits."""

import re
from datetime import UTC, datetime
from types import SimpleNamespace as NS

import discord

from clashhero.cogs._war_embeds import (
    BLANK,
    EMBED_LIMIT,
    FIELD_LIMIT,
    add_fields,
    attack_rank_table,
    attack_totals,
    calls_embed,
    cwl_attacks_summary,
    cwl_group_fields,
    cwl_history_blocks,
    cwl_member_table,
    cwl_round_line,
    cwl_season_block,
    cwl_season_label,
    cwl_stars_summary,
    defense_rank_table,
    defense_totals,
    fill_cwl_history,
    fit_lines,
    fit_more,
    fit_sections,
    ordinal,
    pack_fields,
    rank_table,
    remaining_embed,
    set_clan,
    split_block,
    standings_description,
    stats_footer,
    war_log_embed,
    war_log_field,
)
from clashhero.repos.war import AttackSummary, CWLMemberStats, DefenseSummary, Standing
from clashhero.services.emojis import EMOJIS
from clashhero.utils.fmt import clan_link, display_width

MAINTENANCE = "🛠️ Clash of Clans API maintenance since 12:00 UTC: data is paused and may be out of date"


def icons(monkeypatch, *names):
    """Pretend these app emojis are uploaded."""
    monkeypatch.setattr(EMOJIS, "_by_name", {n: f"<:{n}:1>" for n in names})


def cwl_row(rnd, stars=None, dest=0.0, pos=None, th=None, *, state="warEnded", season="2026-11", **kw):
    """A player_cwl_history row; ``stars=None`` means no attack."""
    attack = (
        None if stars is None else {"stars": stars, "destruction": dest, "target_pos": pos, "defender_th": th}
    )
    return {
        "cwl_season": season,
        "cwl_round": rnd,
        "state": state,
        "clan_tag": kw.get("clan_tag", "#C"),
        "clan_name": kw.get("clan_name", "Heroes"),
        "player_tag": kw.get("tag", "#A"),
        "name": kw.get("name", "Alice"),
        "town_hall": kw.get("town_hall", 18),
        "map_position": kw.get("map_position", 15),
        "attack": attack,
        "defense": kw.get("defense"),
    }


def code_lines(text: str) -> list[str]:
    assert text.startswith("```\n") and text.endswith("\n```")
    return text[4:-4].split("\n")


def name_column(lines: list[str], name: str) -> int:
    line = next(x for x in lines if x.endswith(name))
    return display_width(line[: line.index(name)])


# ------------------------------------------------------------------ /cwl history


def test_cwl_season_label():
    assert cwl_season_label("2026-11") == "Nov 2026"
    assert cwl_season_label("2027-01") == "Jan 2027"
    assert cwl_season_label(None) == "?" and cwl_season_label("odd") == "odd"


def test_cwl_round_lines():
    assert cwl_round_line(cwl_row(2, 2, 78.4, 21, 17), 4) == "`R2` ★★☆ ` 78%` → #21 TH17"
    assert cwl_round_line(cwl_row(5, 3, 100.0, 1, 18), 4) == "`R5` ★★★ `100%` → #1 TH18"
    assert cwl_round_line(cwl_row(1, 0, 8.0, None, None)) == "`R1` ☆☆☆ `8%` → #?"
    assert cwl_round_line(cwl_row(3)) == "`R3` ▼ missed"
    assert cwl_round_line(cwl_row(6, state="inWar")) == "`R6` not attacked yet"
    assert cwl_round_line(cwl_row(7, state="preparation")) == "`R7` preparation day"
    assert cwl_round_line(cwl_row(None)) == "`R?` ▼ missed"


def test_cwl_round_line_icons(monkeypatch):
    icons(monkeypatch, "ui_star_old", "ui_star_new", "ui_star_empty", "ui_arrow_down", "th17")
    row = cwl_row(2, 2, 78.4, 21, 17)
    row["attack"]["new_stars"] = 1  # one of the two stars was already taken
    line = cwl_round_line(row, 4)
    assert line == "`R2` <:ui_star_old:1><:ui_star_new:1><:ui_star_empty:1> ` 78%` → #21 <:th17:1>"
    assert all("<:" not in chip for chip in re.findall(r"`[^`]*`", line))  # icons stay out of the chips
    assert cwl_round_line(cwl_row(3)) == "`R3` <:ui_arrow_down:1> missed"


def test_cwl_season_block_layout():
    rows = [  # newest first, as the query returns them
        cwl_row(5, state="inWar"),
        cwl_row(4),
        cwl_row(3, 3, 100.0, 12, 17, defense={"stars": 2, "destruction": 80.0}),
        cwl_row(2, 2, 78.0, 21, 16, map_position=14),
        cwl_row(1, 1, 55.0, 9, 18, defense={"stars": 3, "destruction": 100.0}),
    ]
    assert cwl_season_block(rows) == "\n".join(
        [
            "🏆 **Nov 2026**",
            "Heroes · TH18 · map position **15**",
            "`R1` ★☆☆ ` 55%` → #9 TH18",
            "`R2` ★★☆ ` 78%` → #21 TH16",
            "`R3` ★★★ `100%` → #12 TH17",
            "`R4` ▼ missed",
            "`R5` not attacked yet",
            "⚔️ **3**/4 attacks · ★ **6** stars · **233%** destruction",
            "🛡️ **2** defenses · ★ **5** stars allowed",
        ]
    )


def test_cwl_season_block_header_details():
    # Position: the most common one, ties going to the latest round; TH: the highest seen.
    rows = [
        cwl_row(1, 3, 100.0, 1, 17, map_position=12, town_hall=17),
        cwl_row(2, 3, 100.0, 1, 17, map_position=10, town_hall=18),
    ]
    block = cwl_season_block(rows, with_name=True)
    assert block.split("\n")[:2] == ["🏆 **Nov 2026** · Alice", "Heroes · TH18 · map position **10**"]
    # Markdown in names is escaped; unknown position/TH just drop out of the clan line.
    odd = [cwl_row(1, clan_name="*Stars_*", map_position=None, town_hall=None, name="a_b")]
    head, clan = cwl_season_block(odd, with_name=True).split("\n")[:2]
    assert head == "🏆 **Nov 2026** · a\\_b" and clan == "\\*Stars\\_\\*"
    assert "⚔️ **0**/1 attacks · ★ **0** stars · **0%** destruction" in cwl_season_block(odd)
    # Nothing decided yet: no summary line.
    assert "⚔️" not in cwl_season_block([cwl_row(1, state="preparation")])


def test_cwl_destruction_chips_align_within_a_block():
    rows = [cwl_row(1, 3, 100.0, 1, 18), cwl_row(2, 1, 9.0, 2, 18), cwl_row(3, 2, 64.0, 3, 18)]
    chips = re.findall(r"`( *\d+%)`", cwl_season_block(rows))
    assert chips == ["100%", "  9%", " 64%"]
    # A season without a 100% hit uses narrower chips.
    assert re.findall(r"`( *\d+%)`", cwl_season_block(rows[1:])) == [" 9%", "64%"]


def test_cwl_history_blocks_newest_season_first():
    rows = [
        cwl_row(1, 3, 100.0, 1, 18, season="2026-09"),
        cwl_row(2, 2, 90.0, 2, 18, season="2026-11"),
        cwl_row(1, 1, 50.0, 3, 17, season="2026-10"),
    ]
    blocks = cwl_history_blocks(rows)
    heads = [b.split("\n")[0] for b in blocks]
    assert heads == ["🏆 **Nov 2026**", "🏆 **Oct 2026**", "🏆 **Sep 2026**"]
    # One account: the headers don't repeat its name.


def test_cwl_history_blocks_name_each_account():
    rows = [cwl_row(1, 3, 100.0, 1, 18), cwl_row(1, 2, 80.0, 4, 16, tag="#B", name="Bob", map_position=4)]
    blocks = cwl_history_blocks(rows)
    assert [b.split("\n")[:2] for b in blocks] == [
        ["🏆 **Nov 2026** · Alice", "Heroes · TH18 · map position **15**"],
        ["🏆 **Nov 2026** · Bob", "Heroes · TH18 · map position **4**"],
    ]


def test_cwl_history_embed_stays_under_limits():
    rows = [
        cwl_row(r, 3, 100.0, 50, 18, season=f"{2020 + i // 12}-{i % 12 + 1:02d}", clan_name="C" * 15)
        for i in range(40)
        for r in range(1, 8)
    ]
    e = discord.Embed(title="Alice (#A)", description="🏆 **CWL history** · every recorded season")
    e.set_author(name=MAINTENANCE)
    fill_cwl_history(e, rows)
    assert len(e) <= EMBED_LIMIT and len(e.fields) <= 25
    assert all(len(f.value) <= FIELD_LIMIT and f.name == BLANK for f in e.fields)
    assert e.fields[0].value.startswith("🏆 **Apr 2023**")  # the newest season
    assert e.footer.text.startswith(f"Newest first · showing {len(e.fields)} of 40")


def test_cwl_history_embed_with_icons_stays_under_limits(monkeypatch):
    # Real emoji markup is long (``<:ui_star_new:1234567890123456789>``): a season splits over two
    # fields rather than going past Discord's limits.
    names = (
        "ui_cwl_star",
        "ui_sword",
        "ui_shield",
        "ui_war_star",
        "ui_star_new",
        "ui_star_old",
        "ui_star_empty",
    )
    monkeypatch.setattr(EMOJIS, "_by_name", {n: f"<:{n}:1234567890123456789>" for n in (*names, "th18")})
    rows = [
        cwl_row(r, 3, 100.0, 50, 18, season=f"{2020 + i // 12}-{i % 12 + 1:02d}", defense={"stars": 3})
        for i in range(40)
        for r in range(1, 8)
    ]
    e = fill_cwl_history(discord.Embed(title="Alice (#A)"), rows)
    assert len(e) <= EMBED_LIMIT and len(e.fields) <= 25
    assert all(len(f.value) <= FIELD_LIMIT for f in e.fields)
    assert len(e.fields) >= 2 and "showing" in e.footer.text


def test_cwl_history_embed_empty():
    e = fill_cwl_history(discord.Embed(title="CWL history"), [])
    assert e.description == "No CWL wars recorded for these accounts." and not e.fields


# ------------------------------------------------------------------ splitting


def test_split_block_and_pack_fields():
    block = "\n".join(f"line {i:03d} " + "x" * 90 for i in range(25))  # ~2500 chars
    parts = split_block(block)
    assert len(parts) == 3 and all(len(p) <= FIELD_LIMIT for p in parts)
    assert "\n".join(parts) == block  # split only between lines
    assert split_block("y" * 2000) == ["y" * FIELD_LIMIT]

    fields, shown = pack_fields(["a" * 900] * 10, budget=2000)
    assert (len(fields), shown) == (2, 2)
    fields, shown = pack_fields(["a"] * 30)
    assert (len(fields), shown) == (25, 25)
    assert pack_fields([block, "b"]) == ([*parts, "b"], 2)


def test_fit_lines():
    assert fit_lines(["aaa", "bbb", "ccc"], 7) == "aaa\nbbb"
    assert fit_lines(["a" * 10], 4) == "aaaa"
    assert fit_lines([]) == ""


# ------------------------------------------------------------------ ranking tables


def test_rank_table_header_and_alignment():
    rows = [
        (1, 18, "100.0", "12/12", "Bob"),
        (2, 17, "93.9", "31/33", "海神 a very long name indeed"),
        (10, 9, "5.0", "1/20", "Carl"),
    ]
    lines = code_lines(rank_table(rows, ("#", "TH", "RATE", "HITS", "NAME")))
    assert lines[0] == "#   TH  RATE   HITS   NAME"
    assert lines[1:] == [
        " 1  18  100.0  12/12  Bob",
        " 2  17   93.9  31/33  海神 a very long name indeed",
        "10   9    5.0   1/20  Carl",
    ]
    # Names start in the same column whatever their length or script.
    assert {name_column(lines, n) for n in ("NAME", "Bob", "海神 a very long name indeed", "Carl")} == {22}


def test_rank_table_escapes_backticks_and_respects_limit():
    lines = code_lines(rank_table([(1, "x```y")], ("#", "NAME")))
    assert lines[1] == "1  x'''y"
    many = rank_table([(i, "n" * 30) for i in range(200)], ("#", "NAME"), limit=1000)
    assert len(many) <= 1000 and many.endswith("\n```")


def test_attack_rank_table():
    a = AttackSummary(attacks=33, triples=31, stars=95, missed=2, town_hall=18)
    b = AttackSummary(attacks=12, triples=12, stars=36, town_hall=17)
    lines = code_lines(attack_rank_table([("#B", b), ("#A", a), ("#Z", AttackSummary())], {"#A": "Alice"}))
    assert lines == [
        "#  TH  RATE   HITS   NAME",
        "1  17  100.0  12/12  #B",
        "2  18   93.9  31/33  Alice",
        "3   ?    0.0    0/0  #Z",
    ]
    assert attack_totals([("#A", a), ("#B", b)]).split("\n") == [
        "⚔️ **45** attacks · ▼ **2** missed",
        "★★★ **95.6%** tripled · ★ **2.91** avg stars",
    ]
    assert attack_totals([]) == "⚔️ **0** attacks"


def test_defense_rank_table():
    d = DefenseSummary(defenses=20, triples_allowed=4, stars_allowed=37, town_hall=16)
    lines = code_lines(defense_rank_table([("#A", d)], {"#A": "Alice"}))
    assert lines == ["#  TH  RATE  HITS   NAME", "1  16  80.0  16/20  Alice"]
    assert defense_totals([("#A", d)]).split("\n") == [
        "🛡️ **20** defenses",
        "**80.0%** held, not tripled · ★ **1.85** avg stars allowed",
    ]
    assert defense_totals([]) == "🛡️ **0** defenses"


def test_totals_icons(monkeypatch):
    icons(monkeypatch, "ui_sword", "ui_shield", "ui_war_star", "ui_star_new", "ui_arrow_down")
    a = AttackSummary(attacks=4, triples=2, stars=10, missed=1)
    assert attack_totals([("#A", a)]).split("\n") == [
        "<:ui_sword:1> **4** attacks · <:ui_arrow_down:1> **1** missed",
        "<:ui_star_new:1>" * 3 + " **50.0%** tripled · <:ui_war_star:1> **2.50** avg stars",
    ]
    assert defense_totals([("#A", DefenseSummary(defenses=1))]).startswith("<:ui_shield:1> **1** defense\n")


def test_stats_footer():
    assert stats_footer(None, "last 30 days", 12) == "All wars · last 30 days · 12 wars"
    assert stats_footer("CWL", "this season", 1, 16) == "CWL · TH16 · this season · 1 war"


def test_cwl_member_table():
    rows = [
        CWLMemberStats("#A", "Alice", town_hall=18, rounds=7, attacks=7, stars=19, destruction=678.0),
        CWLMemberStats("#B", "Bob", town_hall=17, rounds=7, attacks=5, missed=2, stars=12, destruction=478.4),
    ]
    assert code_lines(cwl_member_table(rows)) == [
        "#  ★   DEST  HIT  TH  NAME",
        "1  19  678%  7/7  18  Alice",
        "2  12  478%  5/7  17  Bob",
    ]
    assert code_lines(cwl_member_table(rows, missed=True)) == [
        "#  ★   DEST  HIT  MISS  TH  NAME",
        "1  19  678%  7/7        18  Alice",
        "2  12  478%  5/7     2  17  Bob",
    ]


# ------------------------------------------------------------------ CWL summaries and group


def test_cwl_summaries():
    rows = [
        CWLMemberStats("#A", "Alice", rounds=7, attacks=7, stars=19, destruction=678.0, triples=5),
        CWLMemberStats("#B", "Bob", rounds=7, attacks=5, missed=2, stars=12, destruction=478.4, triples=2),
    ]
    assert cwl_attacks_summary(rows) == "⚔️ **12**/14 attacks used · ▼ **2** missed"
    assert cwl_attacks_summary(rows[:1]) == "⚔️ **7**/7 attacks used"
    assert cwl_stars_summary(rows) == "★ **31** stars · ★★★ **7** triples · **1,156%** destruction"


def test_standings_description_names_our_place(monkeypatch):
    rows = [Standing("#X", "Top", stars=30, wins=3), Standing("#C", "Us_", stars=25, wins=2, losses=1)]
    text = standings_description(
        rows, "#C", round_no=3, rounds=7, league="Crystal League I", movement={"#C": "up"}
    )
    head, ours, table_text = text.split("\n", 2)
    assert head == "**Crystal League I** · Round **3**/7"
    assert ours == "▲ **Us\\_** · **2nd** of 2 · ★ **45** stars"
    assert table_text.startswith("```\n# Clan ★  Dest W-L-T")
    # No league known, our clan didn't move: no icon, no arrow.
    head, ours, _ = standings_description(rows, "#C", round_no=1, rounds=7).split("\n", 2)
    assert head == "Round **1**/7" and ours.startswith("**Us\\_** · **2nd**")
    icons(monkeypatch, "cwl_crystal_i", "ui_arrow_down", "ui_war_star")
    head, ours, _ = standings_description(
        rows, "#C", round_no=3, rounds=7, league="Crystal League I", movement={"#C": "down"}
    ).split("\n", 2)
    assert head == "<:cwl_crystal_i:1> **Crystal League I** · Round **3**/7"
    assert ours.startswith("<:ui_arrow_down:1> **Us\\_**") and "<:ui_war_star:1> **45** stars" in ours


def test_ordinal():
    assert [ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 101, 111)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "101st", "111th",
    ]  # fmt: skip


def test_cwl_group_fields(monkeypatch):
    clans = [
        NS(tag="#X", name="Top", members=[NS(town_hall=17)] * 15),
        NS(tag="#C", name="Us", members=[NS(town_hall=t) for t in (18, 18, 17, 16)]),
    ]
    assert cwl_group_fields(clans, "#C") == [
        ("★ Us · 4 players", "TH18 **2**  TH17 **1**  TH16 **1**"),  # ours first, marked
        ("Top · 15 players", "TH17 **15**"),
    ]
    icons(monkeypatch, "ui_war_star", "th17", "th18")
    (ours, ours_ths), _ = cwl_group_fields(clans, "#C")
    assert ours == "<:ui_war_star:1> Us · 4 players"
    assert ours_ths == "<:th18:1> **2**  <:th17:1> **1**  TH16 **1**"  # no icon uploaded for TH16


# ------------------------------------------------------------------ war log, remaining, calls

NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)


def log_entry(result, ours=(42, 98.2), theirs=(38, 91.0), opp="Them", size=15):
    return NS(
        result=NS(value=result) if result else None,
        end_time=NS(time=NOW),
        team_size=size,
        is_league_entry=result is None,
        clan=NS(name="Us", stars=ours[0], destruction=ours[1]),
        opponent=NS(name=opp, stars=theirs[0], destruction=theirs[1]) if result else None,
    )


def test_war_log_fields():
    end = f"<t:{int(NOW.timestamp())}:R>"
    assert war_log_field(log_entry("win")) == (
        "▲ Victory vs Them (15 vs 15)",
        f"★ **42** – 38 · **98.2%** – 91.0% · ended {end}",
    )
    assert war_log_field(log_entry("lose", opp="a_b"))[0] == "▼ Defeat vs a\\_b (15 vs 15)"
    assert war_log_field(log_entry("tie"))[0] == "Tie vs Them (15 vs 15)"
    assert war_log_field(log_entry(None, ours=(210, 0))) == (
        "🏆 Clan War League",
        f"★ **210** stars · ended {end}",
    )


def test_war_log_embed_fits_and_says_what_it_left_off(monkeypatch):
    monkeypatch.setattr(
        EMOJIS, "_by_name", {n: f"<:{n}:1234567890123456789>" for n in ("ui_arrow_up", "ui_war_star")}
    )
    e = discord.Embed(title="War log")
    war_log_embed(e, [log_entry("win", opp="O" * 15)] * 25)
    assert len(e.fields) == 25 and len(e) <= EMBED_LIMIT and e.footer.text is None
    big = discord.Embed(title="War log", description="x" * 3000)
    war_log_embed(big, [log_entry("win", opp="O" * 15)] * 25)
    assert len(big) <= EMBED_LIMIT and big.footer.text == f"Showing the latest {len(big.fields)} of 25"
    empty = war_log_embed(discord.Embed(title="War log"), [])
    assert empty.description == "No wars in the log." and not empty.fields


def live_war(state="inWar", cwl=False):
    us = NS(tag="#C", name="Us", badge=NS(url="https://badge/us.png"))
    them = NS(tag="#O", name="Them")
    t = NS(time=NOW)
    return NS(state=state, clan=them, opponent=us, is_cwl=cwl, start_time=t, end_time=t)


def test_remaining_embed_groups_by_attacks_left():
    left = [
        ({"tag": "#A", "name": "Alice", "map_position": 1, "town_hall": 16}, 1),
        ({"tag": "#B", "name": "B_b", "map_position": 2, "town_hall": 15}, 2),
        ({"tag": "#D", "name": "Dee", "map_position": 4, "town_hall": None}, 2),
    ]
    e = remaining_embed(discord.Embed(title="Remaining attacks"), live_war(), "#C", left, {"#B": 42})
    assert (e.author.name, e.author.icon_url) == ("Us", "https://badge/us.png")
    assert e.description.split("\n") == [
        f"War vs **Them** · ends <t:{int(NOW.timestamp())}:R>",
        "⚔️ **5** attacks left · **3** players",
        "",
        "**2 attacks left**",
        "**2.** TH15 B\\_b <@42>",
        "**4.** Dee",
        "",
        "**1 attack left**",
        "**1.** TH16 Alice",
    ]
    prep = remaining_embed(discord.Embed(), live_war("preparation", cwl=True), "#C", left[:1])
    assert prep.description.startswith("CWL vs **Them** · battle day starts <t:")


def test_remaining_embed_everyone_done_and_long_lists(monkeypatch):
    done = remaining_embed(discord.Embed(), live_war(), "#C", [])
    assert done.description.split("\n")[1] == "▲ Everyone has used all their attacks."
    many = [({"tag": f"#{i}", "name": "N" * 40, "map_position": i, "town_hall": 18}, 2) for i in range(1, 51)]
    monkeypatch.setattr(EMOJIS, "_by_name", {"th18": "<:th18:1234567890123456789>"})
    e = remaining_embed(discord.Embed(), live_war(), "#C", many, {f"#{i}": 10**18 + i for i in range(1, 51)})
    assert len(e.description) <= 4096 and e.description.endswith("more")


def test_set_clan_keeps_the_maintenance_notice():
    e = set_clan(discord.Embed(), "Us", "#C", "https://badge/us.png")
    assert (e.author.name, e.author.url, e.author.icon_url) == ("Us", clan_link("#C"), "https://badge/us.png")
    e = discord.Embed()
    e.set_author(name=MAINTENANCE)
    e.set_footer(text="All wars · last 30 days · 3 wars")
    set_clan(e, "Us", "#C", "https://badge/us.png")
    assert e.author.name == MAINTENANCE
    assert (e.footer.text, e.footer.icon_url) == (
        "Us · All wars · last 30 days · 3 wars",
        "https://badge/us.png",
    )


def test_calls_embed():
    war = {
        "clan_tag": "#C",
        "opponent_name": "Them",
        "opponent_tag": "#O",
        "opponent_badge_url": "https://badge/them.png",
        "team_size": 15,
        "war_type": "cwl",
        "cwl_round": 2,
    }
    callers = [
        {"position": 1, "player_tag": "#A", "user_id": 5, "note": "ring_base"},
        {"position": 2, "player_tag": None, "user_id": 6, "note": None},
        {"position": 9, "player_tag": None, "user_id": None, "note": None},
    ]
    enemies = {
        1: {"name": "Xeno", "player_tag": "#X", "town_hall": 16},
        2: {"name": "Yan", "player_tag": "#Y"},
    }
    e = calls_embed(discord.Embed(), war, callers, enemies, {"#X": 2}, {"#A": "Alice"})
    assert (e.title, e.url, e.thumbnail.url) == (
        "CWL round 2 calls vs Them",
        clan_link("#O"),
        "https://badge/them.png",
    )
    assert e.description.split("\n") == [
        "**1.** TH16 Xeno ★★☆ → **Alice** <@5> · _ring\\_base_",
        "**2.** Yan → <@6>",
        "**9.** ? →",
    ]
    assert e.footer.text == "3 of 15 bases claimed"
    empty = calls_embed(discord.Embed(), {**war, "war_type": "random"}, [], {}, {}, {})
    assert empty.title == "War calls vs Them" and empty.description.startswith("No bases claimed yet")


def test_fit_helpers():
    lines = ["a" * 10, "b" * 10, "c" * 10]
    assert fit_more(lines, 32) == "\n".join(lines)
    assert fit_more(lines, 30) == "a" * 10 + "\n… and 2 more"
    assert fit_more(lines, 12) == "… and 3 more"
    assert fit_more(["aaa"], 10) == "aaa"
    text = fit_sections([("**A**", ["x" * 10] * 5), ("**B**", ["y"])], 40)
    assert len(text) <= 40 and text.startswith("**A**\nxxxxxxxxxx") and text.endswith("**B**\ny")
    e = discord.Embed(description="x" * 5000)
    assert add_fields(e, [("a", "b" * 1000)] * 3) == 0  # no room left in the message
