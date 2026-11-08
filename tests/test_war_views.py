"""Layout of the war / CWL / stats views: block layout, chip alignment and Discord size limits."""

import re

import discord

from clashhero.cogs._war_embeds import (
    BLANK,
    EMBED_LIMIT,
    FIELD_LIMIT,
    attack_rank_table,
    attack_totals,
    attacks_line,
    chip_rows,
    cwl_history_blocks,
    cwl_member_table,
    cwl_round_line,
    cwl_season_block,
    cwl_season_label,
    defense_rank_table,
    defense_totals,
    fill_cwl_history,
    fit_lines,
    pack_fields,
    rank_table,
    score_line,
    split_block,
    stats_footer,
)
from clashhero.repos.war import AttackSummary, CWLMemberStats, DefenseSummary
from clashhero.utils.fmt import display_width


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
    assert cwl_round_line(cwl_row(2, 2, 78.4, 21, 17), 4) == "`R2` ★★☆ ` 78%` → #21 · TH17"
    assert cwl_round_line(cwl_row(5, 3, 100.0, 1, 18), 4) == "`R5` ★★★ `100%` → #1 · TH18"
    assert cwl_round_line(cwl_row(1, 0, 8.0, None, None)) == "`R1` ☆☆☆ `8%` → #?"
    assert cwl_round_line(cwl_row(3)) == "`R3` missed"
    assert cwl_round_line(cwl_row(6, state="inWar")) == "`R6` not attacked yet"
    assert cwl_round_line(cwl_row(7, state="preparation")) == "`R7` preparation day"
    assert cwl_round_line(cwl_row(None)) == "`R?` missed"


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
            "🏆 **Nov 2026** (#15 · TH18)",
            "Heroes",
            "`R1` ★☆☆ ` 55%` → #9 · TH18",
            "`R2` ★★☆ ` 78%` → #21 · TH16",
            "`R3` ★★★ `100%` → #12 · TH17",
            "`R4` missed",
            "`R5` not attacked yet",
            "⚔️ 3/4 wars · 6★ · 233%",
            "🛡️ 2 defenses · 5★ given",
        ]
    )


def test_cwl_season_block_header_details():
    # Position: the most common one, ties going to the latest round; TH: the highest seen.
    rows = [
        cwl_row(1, 3, 100.0, 1, 17, map_position=12, town_hall=17),
        cwl_row(2, 3, 100.0, 1, 17, map_position=10, town_hall=18),
    ]
    block = cwl_season_block(rows, with_name=True)
    assert block.split("\n")[0] == "🏆 **Nov 2026** · Alice (#10 · TH18)"
    # Markdown in names is escaped; unknown position/TH just drop out of the header.
    odd = [cwl_row(1, clan_name="*Stars_*", map_position=None, town_hall=None, name="a_b")]
    head, clan = cwl_season_block(odd, with_name=True).split("\n")[:2]
    assert head == "🏆 **Nov 2026** · a\\_b" and clan == "\\*Stars\\_\\*"
    assert "⚔️ 0/1 wars · 0★ · 0%" in cwl_season_block(odd)
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
    assert heads == [
        "🏆 **Nov 2026** (#15 · TH18)",
        "🏆 **Oct 2026** (#15 · TH18)",
        "🏆 **Sep 2026** (#15 · TH18)",
    ]
    # One account: the headers don't repeat its name.


def test_cwl_history_blocks_name_each_account():
    rows = [cwl_row(1, 3, 100.0, 1, 18), cwl_row(1, 2, 80.0, 4, 16, tag="#B", name="Bob", map_position=4)]
    blocks = cwl_history_blocks(rows)
    assert [b.split("\n")[0] for b in blocks] == [
        "🏆 **Nov 2026** · Alice (#15 · TH18)",
        "🏆 **Nov 2026** · Bob (#4 · TH18)",
    ]


def test_cwl_history_embed_stays_under_limits():
    rows = [
        cwl_row(r, 3, 100.0, 50, 18, season=f"{2020 + i // 12}-{i % 12 + 1:02d}", clan_name="C" * 15)
        for i in range(40)
        for r in range(1, 8)
    ]
    e = discord.Embed(title="CWL history · Alice")
    e.set_author(
        name="🛠️ Clash of Clans API maintenance since 12:00 UTC: data is paused and may be out of date"
    )
    fill_cwl_history(e, rows)
    assert len(e) <= EMBED_LIMIT and len(e.fields) <= 25
    assert all(len(f.value) <= FIELD_LIMIT and f.name == BLANK for f in e.fields)
    assert e.fields[0].value.startswith("🏆 **Apr 2023**")  # the newest season
    assert e.footer.text.startswith(f"Newest first · showing {len(e.fields)} of 40")


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
    assert attack_totals([("#A", a), ("#B", b)]) == "⚔️ 45 hits · 95.6% 3★ · 2.91★ avg · 2 missed"
    assert attack_totals([]) == "⚔️ 0 hits"


def test_defense_rank_table():
    d = DefenseSummary(defenses=20, triples_allowed=4, stars_allowed=37, town_hall=16)
    lines = code_lines(defense_rank_table([("#A", d)], {"#A": "Alice"}))
    assert lines == ["#  TH  RATE  HITS   NAME", "1  16  80.0  16/20  Alice"]
    assert defense_totals([("#A", d)]) == "🛡️ 20 defenses · 80.0% held · 1.85★ avg"


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


# ------------------------------------------------------------------ chips


def test_score_and_attack_lines():
    assert score_line(42, 38, 98.24, 91.0) == "⚔️ 42★ – 38★ · 98.2% – 91.0%"
    assert attacks_line(28, 9, 30) == "🗡️ `28/30` – ` 9/30` attacks"


def test_chip_rows_align():
    assert chip_rows([(24, "hits"), ("62%", "3★ rate"), ("2.42", "avg ★")]) == (
        "`  24` hits\n` 62%` 3★ rate\n`2.42` avg ★"
    )
