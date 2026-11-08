"""Pure embed builders for legend features (no I/O; unit-tested)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import discord

from clashhero.repos.legends import BoardEntry, DayStats, Hit, SeasonSummary, WeekDay, has_details
from clashhero.services.emojis import EMOJIS
from clashhero.utils.fmt import chunk_lines, display_width, pad, player_link, signed, stars, table, ts
from clashhero.utils.seasons import season_by_id, season_label
from clashhero.utils.style import SEP

ATTACK_COLOR = discord.Color.green()
DEFENSE_COLOR = discord.Color.red()
KIND_EMOJI = {"attack": "⚔️", "defense": "🛡️"}
LATEST_BATTLES = 8


def sym(name: str, fallback: str) -> str:
    """A game-symbol emoji (``ui_sword``, ``ui_shield``), or ``fallback`` until it's uploaded."""
    return EMOJIS.get(name) or fallback


def _hits(values: Sequence[int]) -> str:
    return " ".join(signed(v) for v in values) or "—"


def _esc(text: str | None, limit: int = 15) -> str:
    return discord.utils.escape_markdown((text or "?")[:limit])


def _stars(n: int) -> str:
    return stars(max(0, min(3, n)))


def opponent_text(name: str | None, th: int | None, limit: int = 15) -> str:
    return _esc(name, limit) + (f" TH{th}" if th else "")


def hit_line(h: Hit) -> str:
    """One battle: trophies, then stars, destruction and opponent when read from the battle log
    (`` `+40` ★★★ 100% · vs Name TH17``); inferred battles show only the trophies."""
    base = f"`{signed(h.trophies):>4}`"
    if h.autofill:
        return f"{base} auto-filled by the game (defense not received)"
    if not h.detailed:
        return base
    word = "vs" if h.kind == "attack" else "by"
    opp = opponent_text(h.opponent_name, h.opponent_th)
    return f"{base} {_stars(h.stars or 0)} {h.destruction or 0}% · {word} {opp}"


def hit_chunks(hits: Sequence[Hit], limit: int = 1024) -> list[str]:
    """Field values listing ``hits``: one line per battle when any has battle-log details,
    otherwise the compact trophy list."""
    if not has_details(hits) and not any(h.autofill for h in hits):
        return [_hits([h.trophies for h in hits])]
    return [c.rstrip() for c in chunk_lines([hit_line(h) for h in hits], limit)]


def source_note(hits: Sequence[Hit]) -> str:
    detailed = sum(1 for h in hits if h.detailed)
    if hits and detailed == len(hits):
        return "from the battle log"
    if detailed:
        return "some battles inferred from trophy changes"
    return "battles are inferred from trophy changes"


def _day_hits(stats: DayStats) -> list[Hit]:
    return stats.hits("attack") + stats.hits("defense")


def count(hits: Sequence[int], cap: int | None) -> str:
    """ "3/8" for tiers with a daily cap (Legend I), plain "3" otherwise."""
    return f"{len(hits)}/{cap}" if cap else str(len(hits))


def week_embed(
    *,
    name: str,
    tag: str,
    league: str | None,
    week_id: str,
    days: Sequence[WeekDay],
    weekly_cap: int | None,
    color: discord.Color | None = None,
) -> discord.Embed:
    """A ranked week (Monday 05:00 UTC to Monday): one row per legend day plus totals."""
    rows, att, dfn, net = [], 0, 0, 0
    for d in days:
        s = d.stats
        att += len(s.attacks)
        dfn += len(s.defenses)
        net += s.net
        rows.append(
            [
                d.day[5:],
                d.start if d.start is not None else "-",
                d.end if d.end is not None else "-",
                f"{len(s.attacks)} ({signed(s.offense)})",
                f"{len(s.defenses)} ({signed(s.defense)})",
                signed(s.net),
            ]
        )
    e = discord.Embed(title=f"{name} · Ranked week of {week_id}", url=player_link(tag), color=color)
    e.description = table(rows, ["Day", "Start", "End", "Att", "Def", "Net"])
    e.add_field(name="Attacks", value=f"{att}/{weekly_cap}" if weekly_cap else str(att))
    e.add_field(name="Defenses", value=str(dfn))
    e.add_field(name="Net", value=signed(net))
    if league:
        e.add_field(name="League", value=league)
    hits = [h for d in days for h in _day_hits(d.stats)]
    e.set_footer(text=f"{tag} · {source_note(hits)} · days reset 05:00 UTC")
    return e


def alert_embed(
    *,
    name: str,
    tag: str,
    kind: str,
    trophies: int,
    trophies_after: int,
    stats: DayStats,
    occurred_at: datetime | None = None,
    opponent_tag: str | None = None,
    opponent_name: str | None = None,
    opponent_th: int | None = None,
    stars: int | None = None,
    destruction: int | None = None,
    battle_type: str | None = None,
) -> discord.Embed:
    """A battle alert. The opponent/stars/destruction arguments are given for battles read from
    the battle log; inferred battles only show the trophy change."""
    is_attack = kind == "attack"
    lines = [f"**{signed(trophies)}** trophies → **{trophies_after}**"]
    if opponent_tag is not None:
        result = f"{_stars(stars or 0)} **{destruction or 0}%**"
        who = f"[{_esc(opponent_name, 32)}]({player_link(opponent_tag)})"
        th = f" · TH{opponent_th}" if opponent_th else ""
        lines.append(f"{result} {'vs' if is_attack else 'by'} {who}{th} · `{opponent_tag}`")
    e = discord.Embed(
        title=f"{'⚔️ Attack' if is_attack else '🛡️ Defense'} · {name}",
        url=player_link(tag),
        color=ATTACK_COLOR if is_attack else DEFENSE_COLOR,
        description="\n".join(lines),
    )
    e.add_field(name="Attacks", value=f"{count(stats.attacks, stats.cap)} ({signed(stats.offense)})")
    e.add_field(name="Defenses", value=f"{count(stats.defenses, stats.cap)} ({signed(stats.defense)})")
    e.add_field(name="Net today", value=signed(stats.net))
    kind_label = {"legend": "Legend I battle", "ranked": "Ranked battle"}.get(battle_type or "")
    e.set_footer(text=f"{tag} · {kind_label}" if kind_label else tag)
    if occurred_at:
        e.timestamp = occurred_at
    return e


def army_embed(
    *, battle: Any, fields: Sequence[tuple[str, str]], color: discord.Color | None = None
) -> discord.Embed:
    """Army used in a battle-log battle (a legend_events row with ``player_name``). On attacks the
    army is the tracked player's; on defenses it's the attacker's."""
    player = battle["player_name"] or battle["player_tag"]
    opponent = battle["opponent_name"] or battle["opponent_tag"] or "?"
    is_attack = battle["kind"] == "attack"
    attacker, target = (player, opponent) if is_attack else (opponent, player)
    e = discord.Embed(title=f"{attacker}'s army", color=color)
    if battle["stars"] is not None:
        e.description = (
            f"{_stars(battle['stars'])} **{battle['destruction'] or 0}%** vs {_esc(target, 32)} · "
            f"{signed(battle['trophies'])} trophies"
        )
    for title, value in fields:
        for i, chunk in enumerate(chunk_lines(value.split("\n"), 1000)):
            e.add_field(name=title if i == 0 else f"{title} (cont.)", value=chunk.rstrip(), inline=True)
    e.set_footer(text=f"{battle['player_tag']} · {'attack' if is_attack else 'defense'}")
    if battle["occurred_at"]:
        e.timestamp = datetime.fromisoformat(battle["occurred_at"])
    return e


def day_embed(
    *,
    name: str,
    tag: str,
    day: str,
    stats: DayStats,
    start: int | None,
    end: int | None,
    color: discord.Color | None = None,
) -> discord.Embed:
    e = discord.Embed(title=f"{name} · Legend day {day}", url=player_link(tag), color=color)
    if start is not None:
        e.description = f"Trophies **{start}** → **{end if end is not None else start}**"
    for kind, title in (
        ("attack", f"Attacks {count(stats.attacks, stats.cap)} ({signed(stats.offense)})"),
        ("defense", f"Defenses {count(stats.defenses, stats.cap)} ({signed(stats.defense)})"),
    ):
        for i, value in enumerate(hit_chunks(stats.hits(kind))):
            e.add_field(name=title if i == 0 else f"{title} (cont.)", value=value, inline=False)
    e.add_field(name="Net", value=signed(stats.net))
    if stats.attacks_left is not None:
        e.add_field(name="Attacks left", value=str(stats.attacks_left))
    if has_details(stats.hits("attack")):
        e.add_field(name="3★", value=f"{stats.triples}/{len(stats.attacks)}")
    e.set_footer(text=f"{tag} · {source_note(_day_hits(stats))}")
    return e


def armies_embed(
    *,
    name: str,
    tag: str,
    day: str,
    kind: str,
    hits: Sequence[Hit],
    cap: int | None,
    trophies: int | None,
    color: discord.Color | None = None,
    battles: bool = True,
) -> discord.Embed:
    """The /legends armies reply: the day's totals for one side (defenses or attacks). The
    armies themselves are on the image card; with ``battles`` (the text fallback when the card
    can't be drawn) each battle is listed as a line instead."""
    noun = "Defenses" if kind == "defense" else "Attacks"
    e = discord.Embed(
        title=f"{name} · {'Defense' if kind == 'defense' else 'Attack'} armies",
        url=player_link(tag),
        color=color,
    )
    values = [h.trophies for h in hits]
    parts = [f"Legend day {day}"]
    if trophies is not None:
        parts.append(f"🏆 **{trophies}**")
    parts.append(f"{noun} {count(values, cap)} ({signed(sum(values))})")
    e.description = SEP.join(parts)
    if battles:
        chunks = hit_chunks(hits) if hits else ["None recorded for this day."]
        for i, value in enumerate(chunks):
            e.add_field(name=noun if i == 0 else f"{noun} (cont.)", value=value, inline=False)
    e.set_footer(text=f"{tag} · {source_note(hits)}" if hits else tag)
    return e


def season_rows(summary: SeasonSummary) -> list[list[Any]]:
    rows = []
    for d in summary.days:
        s = d.stats
        rows.append(
            [
                d.day[5:],
                d.start if d.start is not None else "?",
                d.end if d.end is not None else "?",
                signed(s.offense),
                signed(s.defense),
                f"{len(s.attacks)}/{len(s.defenses)}",
            ]
        )
    return rows


def season_embed(
    *, name: str, tag: str, season_id: str, summary: SeasonSummary, color: discord.Color | None = None
) -> discord.Embed:
    e = discord.Embed(
        title=f"{name} · Season {season_label(season_by_id(season_id))}", url=player_link(tag), color=color
    )
    if not summary.days:
        e.description = "No legend data recorded for this season."
        return e
    e.description = table(season_rows(summary), ["Day", "Start", "End", "Off", "Def", "A/D"])[:4000]
    a, d = summary.all_attacks, summary.all_defenses
    e.add_field(name="Attacks", value=f"{len(a)} · avg {summary.avg_offense:+.1f}")
    e.add_field(name="Defenses", value=f"{len(d)} · avg {summary.avg_defense:+.1f}")
    if summary.avg_attack_stars is not None:
        est = summary.estimated_triples
        e.add_field(
            name="3★ rate", value=f"{summary.triple_rate:.0%}" + (f" ({est} by trophies)" if est else "")
        )
    else:
        e.add_field(name="Max-trophy hits", value=f"{summary.triple_rate:.0%}")
    e.add_field(name="Avg lost on defense / day", value=f"{summary.avg_defense_per_day:+.1f}")
    star_avgs = [
        f"{label} {v:.2f}★"
        for label, v in (("atk", summary.avg_attack_stars), ("def", summary.avg_defense_stars))
        if v is not None
    ]
    if star_avgs:
        e.add_field(name="Avg stars", value=" · ".join(star_avgs))
    e.set_footer(text=tag)
    return e


def history_embed(
    *,
    name: str,
    tag: str,
    seasons: Sequence[Any],
    best_trophies: int | None,
    names: Sequence[str],
    color: discord.Color | None = None,
) -> discord.Embed:
    e = discord.Embed(title=f"{name} · Legend history", url=player_link(tag), color=color)
    if seasons:
        rows = [
            [s["season_id"], s["final_trophies"] or "?", s["peak_trophies"] or "?", s["days"], s["attacks"]]
            for s in seasons
        ]
        e.description = table(rows, ["Season", "Final", "Peak", "Days", "Hits"])[:4000]
    else:
        e.description = "No seasons recorded yet."
    if best_trophies:
        e.add_field(name="All-time best", value=str(best_trophies))
    if len(names) > 1:
        e.add_field(name="Name history", value=" → ".join(names)[:1024], inline=False)
    e.set_footer(text=tag)
    return e


def _board_has_stars(entries: Sequence[BoardEntry]) -> bool:
    return any(has_details(e.stats.hits("attack")) for e in entries)


def board_lines(entries: Sequence[BoardEntry]) -> list[list[Any]]:
    """Table rows; a 3★ column (today's triples) is added when any player has battle-log data."""
    with_stars = _board_has_stars(entries)
    rows = []
    for i, e in enumerate(entries, 1):
        row: list[Any] = [
            i,
            e.name[:15],
            e.town_hall or "?",
            e.trophies,
            signed(e.stats.net),
            count(e.stats.attacks, e.stats.cap),
            count(e.stats.defenses, e.stats.cap),
        ]
        if with_stars:
            row.append(e.stats.triples if has_details(e.stats.hits("attack")) else "-")
        rows.append(row)
    return rows


def latest_battles(entries: Sequence[BoardEntry], limit: int = LATEST_BATTLES) -> list[tuple[str, Hit]]:
    """The most recent battles across ``entries``, newest first, as (player name, hit)."""
    out = [(e.name, h) for e in entries for h in _day_hits(e.stats) if h.occurred_at]
    out.sort(key=lambda x: x[1].occurred_at or "", reverse=True)
    return out[:limit]


def latest_battles_value(battles: Sequence[tuple[str, Hit]]) -> str:
    lines = []
    for name, h in battles:
        when = ts(h.occurred_at, "t") + " " if h.occurred_at else ""
        lines.append(f"{when}{KIND_EMOJI.get(h.kind, '')} **{_esc(name)}** {hit_line(h)}")
    return chunk_lines(lines, 1024)[0].rstrip()


def board_embed(
    *,
    title: str,
    entries: Sequence[BoardEntry],
    updated: datetime | None = None,
    color: discord.Color | None = None,
    latest: int = 0,
) -> discord.Embed:
    """Leaderboard table; with ``latest`` > 0 also a "Latest battles" field listing that many of
    today's most recent battles (opponent, stars and % when known)."""
    e = discord.Embed(title=title, color=color)
    if entries:
        headers = ["#", "Name", "TH", "Cups", "Today", "Atk", "Def"]
        if _board_has_stars(entries):
            headers.append("3★")
        e.description = table(board_lines(entries), headers)[:4000]
    else:
        e.description = "No players yet."
    battles = latest_battles(entries, latest) if latest > 0 else []
    if battles:
        e.add_field(name="Latest battles", value=latest_battles_value(battles), inline=False)
    if updated:
        e.add_field(name="Updated", value=ts(updated))
    return e


def clan_day_embed(
    *, clan_name: str, clan_tag: str, day: str, rows: Sequence[dict], color: discord.Color | None = None
) -> discord.Embed:
    e = discord.Embed(title=f"{clan_name} · Legend day {day} summary", color=color)
    if not rows:
        e.description = "No legend activity recorded."
        return e
    with_stars = any(r.get("triples") is not None for r in rows)
    lines = []
    for i, r in enumerate(rows, 1):
        line = [
            i,
            r["name"][:15],
            r["start"],
            r["end"],
            signed(r["offense"]),
            signed(r["defense"]),
            signed(r["net"]),
        ]
        if with_stars:
            line.append("-" if r.get("triples") is None else r["triples"])
        lines.append(line)
    headers = ["#", "Name", "Start", "End", "Off", "Def", "Net"] + (["3★"] if with_stars else [])
    e.description = table(lines, headers)[:4000]
    tot_off = sum(r["offense"] for r in rows)
    tot_def = sum(r["defense"] for r in rows)
    e.add_field(name="Clan offense", value=signed(tot_off))
    e.add_field(name="Clan defense", value=signed(tot_def))
    e.set_footer(text=clan_tag)
    return e


def top_players_embed(
    *,
    title: str,
    players: Sequence[Any],
    page: int,
    pages: int,
    highlight: set[str],
    color: discord.Color | None = None,
) -> discord.Embed:
    rows = [
        [
            ("★" if p.tag in highlight else "") + str(p.rank),
            p.name[:15],
            p.trophies,
            (getattr(p.clan, "name", None) or "—")[:12],
        ]
        for p in players
    ]
    e = discord.Embed(title=title, color=color, description=table(rows, ["#", "Name", "Cups", "Clan"]))
    e.set_footer(text=f"Page {page + 1}/{max(pages, 1)} · ★ = tracked here")
    return e


def top_clans_embed(
    *,
    title: str,
    clans: Sequence[Any],
    page: int,
    pages: int,
    highlight: set[str],
    color: discord.Color | None = None,
) -> discord.Embed:
    rows = [
        [("★" if c.tag in highlight else "") + str(c.rank), c.name[:15], c.points, c.member_count]
        for c in clans
    ]
    e = discord.Embed(title=title, color=color, description=table(rows, ["#", "Clan", "Points", "Mem"]))
    e.set_footer(text=f"Page {page + 1}/{max(pages, 1)} · ★ = linked to this server")
    return e


# ------------------------------------------------------------------ /legends logs: the day at a glance

LOGS_DESC_LIMIT = 3800  # keep the description well under Discord's 4,096
NAME_COLUMNS = 20  # display columns for an opponent's name in a battle line


def _clip(text: str, columns: int) -> str:
    """``text`` cut to ``columns`` display columns (wide characters count 2), with an ellipsis."""
    if display_width(text) <= columns:
        return text
    out = ""
    for ch in text:
        if display_width(out + ch) > columns - 1:
            break
        out += ch
    return out + "…"


def battle_lines(hits: Sequence[Hit]) -> list[str]:
    """One monospace line per battle: ``+28  ★★☆   86%  name``, columns aligned; battles
    inferred from trophy changes and auto-filled defenses say so instead of stars and %."""
    width = max([3, *(len(signed(h.trophies)) for h in hits)])
    lines = []
    for h in hits:
        t = signed(h.trophies).rjust(width)
        if h.autofill:
            lines.append(f"{t}  ···  {'—'.center(4)}  auto-filled (no defense received)")
        elif not h.detailed:
            lines.append(f"{t}  ···  {'—'.center(4)}  inferred from trophies")
        else:
            name = _clip(h.opponent_name or h.opponent_tag or "?", NAME_COLUMNS)
            lines.append(f"{t}  {_stars(h.stars or 0)}  {f'{h.destruction or 0}%':>4}  {name}")
    return lines


def summary_block(
    attacks: Sequence[Hit], defenses: Sequence[Hit], cap: int | None, start: int | None, end: int | None
) -> str:
    """The ATTACK / DEFENSE / NET / TROPHIES block, in aligned columns."""
    a = [h.trophies for h in attacks]
    d = [h.trophies for h in defenses]
    rows = [
        ("ATTACK", signed(sum(a)), count(a, cap)),
        ("DEFENSE", signed(sum(d)), count(d, cap)),
        ("NET", signed(sum(a) + sum(d)), ""),
    ]
    if start is not None:
        rows.append(("TROPHIES", str(start), f"→ {end if end is not None else start}"))
    width = max(5, *(display_width(v) + 1 for _, v, _ in rows))
    out = []
    for label, value, extra in rows:
        line = pad(label, 10) + pad(value, width, right=True)
        if extra:
            line += (" " if label == "TROPHIES" else "   ") + extra
        out.append(line)
    return "\n".join(out)


def _block(lines: Sequence[str], dropped: int, empty: str) -> str:
    body = list(lines) or [empty]
    if dropped:
        body.insert(0, f"… {dropped} earlier")
    return "```\n" + "\n".join(body) + "\n```"


def legends_today_embed(
    *,
    author: str,
    name: str,
    tag: str,
    town_hall: int | None,
    trophies: int | None,
    attacks: Sequence[Hit],
    defenses: Sequence[Hit],
    cap: int | None,
    start: int | None,
    end: int | None,
    footer: str,
    now: datetime | None = None,
    color: discord.Color | None = None,
    limit: int = LOGS_DESC_LIMIT,
) -> discord.Embed:
    """The /legends logs "today" view: the player's line, a summary block, then each attack and
    defense as a line (the newest kept when it's too long, with "… N earlier")."""
    e = discord.Embed(title=f"{name} {tag}", url=player_link(tag), color=color)
    e.set_author(name=author.upper())
    first = []
    if town_hall:
        th = EMOJIS.town_hall(town_hall)
        first.append(f"{th} Town Hall {town_hall}" if th else f"Town Hall {town_hall}")
    if trophies is not None:
        trophy = sym("res_trophy", "")
        first.append(f"{trophy} **{trophies:,}** trophies".strip())
    a_values = [h.trophies for h in attacks]
    d_values = [h.trophies for h in defenses]
    a_head = f"{sym('ui_sword', '⚔️')} **Attacks {count(a_values, cap)} ({signed(sum(a_values))})**"
    d_head = f"{sym('ui_shield', '🛡️')} **Defenses {count(d_values, cap)} ({signed(sum(d_values))})**"
    summary = "```\n" + summary_block(attacks, defenses, cap, start, end) + "\n```"
    a_lines, d_lines = battle_lines(attacks), battle_lines(defenses)
    dropped = [0, 0]

    def build() -> str:
        parts = [SEP.join(first)] if first else []
        parts += [
            summary,
            a_head,
            _block(a_lines[dropped[0] :], dropped[0], "No attacks yet"),
            d_head,
            _block(d_lines[dropped[1] :], dropped[1], "No defenses yet"),
        ]
        return "\n".join(parts)

    text = build()
    while len(text) > limit:
        side = 0 if len(a_lines) - dropped[0] >= len(d_lines) - dropped[1] else 1
        if len((a_lines, d_lines)[side]) - dropped[side] <= 1:
            break
        dropped[side] += 1
        text = build()
    e.description = text
    hits = [*attacks, *defenses]
    note = f" · {source_note(hits)}" if hits and not all(h.detailed or h.autofill for h in hits) else ""
    e.set_footer(text=f"{footer} · Net {signed(sum(a_values) + sum(d_values))}{note}")
    e.timestamp = now or datetime.now().astimezone()
    return e
