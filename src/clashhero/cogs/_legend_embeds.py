"""Pure embed builders for legend features (no I/O; unit-tested)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any

import discord

from clashhero.cogs import _log_embeds as L
from clashhero.repos.legends import BoardEntry, DayStats, Hit, SeasonSummary, WeekDay, has_details
from clashhero.services.battlelog import estimate_legend_result
from clashhero.services.emojis import EMOJIS
from clashhero.utils.fmt import chunk_lines, display_width, pad, player_link, signed, table, ts
from clashhero.utils.fmt import stars as text_stars
from clashhero.utils.seasons import Window, season_by_id, season_label
from clashhero.utils.style import SEP

ATTACK_COLOR = discord.Color.green()
DEFENSE_COLOR = discord.Color.red()
LATEST_BATTLES = 8


def sym(name: str, fallback: str) -> str:
    """A game-symbol emoji (``ui_sword``, ``ui_shield``), or ``fallback`` until it's uploaded."""
    return EMOJIS.get(name) or fallback


def kind_icon(kind: str) -> str:
    """The sword for an attack, the shield for a defense."""
    return sym("ui_sword", "⚔️") if kind == "attack" else sym("ui_shield", "🛡️")


def _hits(values: Sequence[int]) -> str:
    return " ".join(signed(v) for v in values) or "—"


def _esc(text: str | None, limit: int = 15) -> str:
    return discord.utils.escape_markdown((text or "?")[:limit])


def _stars(n: int) -> str:
    """Plain star characters, for monospace lines (icons don't line up there)."""
    return text_stars(max(0, min(3, n)))


def _player_embed(name: str, tag: str, color: discord.Color | None) -> discord.Embed:
    """An embed titled "Name (#TAG)" and linked to the player's in-game profile (just the tag
    when the name isn't known)."""
    title = tag if not name or name == tag else f"{discord.utils.escape_markdown(name)} ({tag})"
    return discord.Embed(title=title[:256], url=player_link(tag), color=color)


def _date(day: str) -> str:
    """A legend day as ``Nov 7`` (legend days run 05:00 to 05:00 UTC, so a Discord timestamp would
    show some viewers the day before)."""
    d = date.fromisoformat(day)
    return f"{d:%b} {d.day}"


def _profile_line(town_hall: int | None, league: str | None, trophies: int | None) -> str:
    """The player's Town Hall and trophies with their game icons (plain words until they're
    uploaded): ``{th} Town Hall 18 · {league} **5,252** trophies``."""
    parts = []
    if town_hall:
        th = EMOJIS.town_hall(town_hall)
        parts.append(f"{th} Town Hall {town_hall}" if th else f"Town Hall {town_hall}")
    if trophies is not None:
        icon = EMOJIS.league(league) or sym("res_trophy", "")
        parts.append(f"{icon} **{trophies:,}** trophies".strip())
    return SEP.join(parts)


def hit_line(h: Hit) -> str:
    """One battle: trophies, then stars, destruction and opponent with their Town Hall when read
    from the battle log (`` `+40` ★★★ 100% vs Name TH17``, as icons); inferred battles show only
    the trophies. The trophies stay a fixed-width chip so a list of battles lines up."""
    base = f"`{signed(h.trophies):>4}`"
    if h.autofill:
        return f"{base} auto-filled by the game (defense not received)"
    if not h.detailed:
        return base
    parts = (
        base,
        L.war_stars(h.stars or 0),
        f"{h.destruction or 0}%",
        "vs" if h.kind == "attack" else "by",
        _esc(h.opponent_name or h.opponent_tag),
        L.th_icon(h.opponent_th),
    )
    return " ".join(x for x in parts if x)


def hit_chunks(hits: Sequence[Hit], limit: int = 1024, max_chunks: int | None = None) -> list[str]:
    """Field values listing ``hits``: one line per battle when any has battle-log details,
    otherwise the compact trophy list. With ``max_chunks``, the oldest battles are left out (as
    "… N earlier") so the list takes at most that many fields: with the star and Town Hall icons
    a line is ~150 characters, and a busy day would pass the 6,000 an embed may hold."""
    if not has_details(hits) and not any(h.autofill for h in hits):
        return [_hits([h.trophies for h in hits])]
    lines = [hit_line(h) for h in hits]
    dropped = 0
    while True:
        shown = [f"… {dropped} earlier", *lines[dropped:]] if dropped else lines
        chunks = [c.rstrip() for c in chunk_lines(shown, limit)]
        if max_chunks is None or len(chunks) <= max_chunks or dropped >= len(lines) - 1:
            return chunks
        dropped += 1


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
    """A ranked week (Monday 05:00 UTC to Monday): the player as the title, the league (its icon)
    and the week's dates, one table row per legend day, then the attacks, defenses and net with
    green up / red down arrows."""
    rows, att, dfn, offense, defense = [], 0, 0, 0, 0
    for d in days:
        s = d.stats
        att += len(s.attacks)
        dfn += len(s.defenses)
        offense += s.offense
        defense += s.defense
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
    e = _player_embed(name, tag, color)
    start = date.fromisoformat(week_id)
    span = f"{_date(week_id)} – {_date((start + timedelta(days=7)).isoformat())}"
    if league:
        head = " ".join(x for x in (EMOJIS.league(league), f"**{league}**", f"· Week of {span}") if x)
    else:
        head = f"**Ranked week of {span}**"
    e.description = head + "\n" + table(rows, ["Day", "Start", "End", "Att", "Def", "Net"])
    attacks = f"{att}/{weekly_cap}" if weekly_cap else str(att)
    e.add_field(name="Attacks", value=f"**{attacks}** · {_trophies(offense)}")
    e.add_field(name="Defenses", value=f"**{dfn}** · {_trophies(defense)}")
    e.add_field(name="Net", value=_trophies(offense + defense))
    hits = [h for d in days for h in _day_hits(d.stats)]
    e.set_footer(text=f"{source_note(hits)} · days reset 05:00 UTC")
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
    clan_name: str | None = None,
    badge_url: str | None = None,
) -> discord.Embed:
    """A battle alert, laid out like the other player posts: "Name (#TAG)" linked as the title;
    attack or defense with the trophy change (green up / red down) and the new total; the stars,
    destruction and opponent when read from the battle log; the day so far; the clan's badge and
    name in the footer."""
    is_attack = kind == "attack"
    icon = sym("ui_sword", "⚔️") if is_attack else sym("ui_shield", "🛡️")
    arrow = L.arrow("up" if trophies > 0 else "down") if trophies else ""
    trophy = EMOJIS.get("res_trophy", "🏆")
    lines = [
        " ".join(
            x
            for x in (
                icon,
                "**Attack**" if is_attack else "**Defense**",
                "·",
                arrow,
                f"**{signed(trophies)}**",
                "→",
                trophy,
                f"**{trophies_after:,}**",
            )
            if x
        )
    ]
    if opponent_tag is not None:
        who = L.player_ref(opponent_name or opponent_tag, opponent_tag)
        th = L.th_icon(opponent_th)
        lines.append(
            " ".join(
                x
                for x in (
                    L.war_stars(stars or 0),
                    f"**{destruction or 0}%**",
                    "vs" if is_attack else "by",
                    who,
                    th,
                )
                if x
            )
        )
    e = discord.Embed(
        title=f"{discord.utils.escape_markdown(name)} ({tag})",
        url=player_link(tag),
        color=ATTACK_COLOR if is_attack else DEFENSE_COLOR,
        description="\n".join(lines),
    )
    e.add_field(name="Attacks", value=f"**{count(stats.attacks, stats.cap)}** · {_trophies(stats.offense)}")
    e.add_field(name="Defenses", value=f"**{count(stats.defenses, stats.cap)}** · {_trophies(stats.defense)}")
    e.add_field(name="Net today", value=_trophies(stats.net))
    kind_label = {"legend": "Legend I battle", "ranked": "Ranked battle"}.get(battle_type or "")
    footer = " · ".join(x for x in (clan_name, kind_label) if x)
    if footer:
        e.set_footer(text=footer, icon_url=badge_url if clan_name and badge_url else None)
    if occurred_at:
        e.timestamp = occurred_at
    return e


def _trophies(n: int) -> str:
    """A day's trophies with a green up or red down arrow."""
    if not n:
        return "**0**"
    return f"{L.arrow('up' if n > 0 else 'down')} **{signed(n)}**"


def _arrowed(n: int) -> str:
    """Trophies with a green up or red down arrow, not bold (for field names)."""
    return f"{L.arrow('up' if n > 0 else 'down')} {signed(n)}" if n else signed(n)


def army_embed(
    *, battle: Any, fields: Sequence[tuple[str, str]], color: discord.Color | None = None
) -> discord.Embed:
    """Army used in a battle-log battle (a legend_events row with ``player_name``). On attacks the
    army is the tracked player's; on defenses it's the attacker's. The title names whose army it
    is (linked to their profile); the battle reads as in the alerts, from the tracked player's
    side (attack or defense with the trophies, then the stars, destruction and opponent), and the
    tracked player is in the footer."""
    tag = battle["player_tag"]
    player = battle["player_name"] or tag
    opp_tag = battle["opponent_tag"]
    opponent = battle["opponent_name"] or opp_tag or "?"
    is_attack = battle["kind"] == "attack"
    attacker, attacker_tag = (player, tag) if is_attack else (opponent, opp_tag)
    e = discord.Embed(
        title=f"{discord.utils.escape_markdown(attacker)}'s army"[:256],
        url=player_link(attacker_tag) if attacker_tag else None,
        color=color,
    )
    side = "**Attack**" if is_attack else "**Defense**"
    lines = [f"{kind_icon(battle['kind'])} {side} · {_trophies(battle['trophies'])}"]
    if battle["stars"] is not None:
        who = L.player_ref(opponent, opp_tag) if opp_tag else f"**{_esc(opponent, 32)}**"
        result = (
            L.war_stars(battle["stars"]),
            f"**{battle['destruction'] or 0}%**",
            "vs" if is_attack else "by",
            who,
            L.th_icon(battle["opponent_th"]),
        )
        lines.append(" ".join(x for x in result if x))
    e.description = "\n".join(lines)
    for title, value in fields:
        for i, chunk in enumerate(chunk_lines(value.split("\n"), 1000)):
            e.add_field(name=title if i == 0 else f"{title} (cont.)", value=chunk.rstrip(), inline=True)
    e.set_footer(text=tag if player == tag else f"{player} ({tag})")
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
    """A player's legend day as text (the /legends day reply when its image can't be drawn, and
    the live leaderboard's menu): the trophies over the day and the net, then each attack and
    defense, the totals with green up / red down arrows."""
    e = _player_embed(name, tag, color)
    lines = [f"**Legend day · {_date(day)}**"]
    if start is not None:
        trophy = EMOJIS.get("res_trophy", "🏆")
        after = end if end is not None else start
        lines.append(f"{trophy} **{start:,}** → **{after:,}** · {_trophies(stats.net)}")
    else:
        lines.append(f"Net {_trophies(stats.net)}")
    e.description = "\n".join(lines)
    for kind, label, values, total in (
        ("attack", "Attacks", stats.attacks, stats.offense),
        ("defense", "Defenses", stats.defenses, stats.defense),
    ):
        title = f"{kind_icon(kind)} {label} {count(values, stats.cap)} · {_arrowed(total)}"
        for i, value in enumerate(hit_chunks(stats.hits(kind), max_chunks=2)):
            e.add_field(name=title if i == 0 else f"{label} (cont.)", value=value, inline=False)
    if stats.attacks_left is not None:
        e.add_field(name="Attacks left", value=f"**{stats.attacks_left}**")
    if has_details(stats.hits("attack")):
        e.add_field(name="Triples", value=f"**{stats.triples}/{len(stats.attacks)}**")
    e.set_footer(text=source_note(_day_hits(stats)))
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
    can't be drawn) each battle is listed as a line instead. The player is the title; under it
    which armies these are and the day, then the trophies and the side's total (green up / red
    down)."""
    noun = "Defenses" if kind == "defense" else "Attacks"
    e = _player_embed(name, tag, color)
    values = [h.trophies for h in hits]
    side = "Defense" if kind == "defense" else "Attack"
    head = f"{kind_icon(kind)} **{side} armies** · Legend day {_date(day)}"
    totals = [f"{EMOJIS.get('res_trophy', '🏆')} **{trophies:,}**"] if trophies is not None else []
    totals += [f"**{count(values, cap)}** {noun.lower()}", _trophies(sum(values))]
    e.description = head + "\n" + SEP.join(totals)
    if battles:
        chunks = hit_chunks(hits, max_chunks=4) if hits else ["None recorded for this day."]
        for i, value in enumerate(chunks):
            e.add_field(name=noun if i == 0 else f"{noun} (cont.)", value=value, inline=False)
    if hits:
        e.set_footer(text=source_note(hits))
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
    """/legends history: the player as the title, a table of their seasons, their all-time best
    trophies and their earlier names."""
    e = _player_embed(name, tag, color)
    if seasons:
        rows = [
            [s["season_id"], s["final_trophies"] or "?", s["peak_trophies"] or "?", s["days"], s["attacks"]]
            for s in seasons
        ]
        e.description = "**Legend history**\n" + table(rows, ["Season", "Final", "Peak", "Days", "Hits"])
        if len(e.description) > 4000:  # the oldest seasons don't fit: keep the newest, close the block
            e.description = e.description[:3990].rsplit("\n", 1)[0] + "\n```"
    else:
        e.description = "**Legend history**\nNo seasons recorded yet."
    if best_trophies:
        e.add_field(name="All-time best", value=f"{EMOJIS.get('res_trophy', '🏆')} **{best_trophies:,}**")
    if len(names) > 1:
        shown = " → ".join(discord.utils.escape_markdown(n) for n in names)
        e.add_field(name="Name history", value=shown[:1024], inline=False)
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
    """One line per battle: the time, the sword or shield, the player and the battle."""
    lines = []
    for name, h in battles:
        when = ts(h.occurred_at, "t") + " " if h.occurred_at else ""
        lines.append(f"{when}{kind_icon(h.kind)} **{_esc(name)}** {hit_line(h)}")
    return chunk_lines(lines, 1024)[0].rstrip()


def board_embed(
    *,
    title: str,
    entries: Sequence[BoardEntry],
    updated: datetime | None = None,
    color: discord.Color | None = None,
    latest: int = 0,
    clan_name: str | None = None,
    clan_tag: str | None = None,
    badge_url: str | None = None,
) -> discord.Embed:
    """Leaderboard table; with ``latest`` > 0 also a "Latest battles" field listing that many of
    today's most recent battles (opponent, stars and % when known). A board of one clan has the
    clan (badge and name, linked) as its author."""
    e = discord.Embed(title=title, color=color)
    L.clan_author(e, clan_name, clan_tag, badge_url)
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
    *,
    clan_name: str,
    clan_tag: str,
    day: str,
    rows: Sequence[dict],
    color: discord.Color | None = None,
    badge_url: str | None = None,
) -> discord.Embed:
    """A clan's legend day, posted after the reset: the clan (badge and name) as the author, a
    table of its members' day, then the clan's offense, defense and net (green up / red down)."""
    e = discord.Embed(title=f"Legend day summary · {_date(day)}", color=color)
    L.clan_author(e, clan_name, clan_tag, badge_url)
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
    e.add_field(name="Clan offense", value=_trophies(tot_off))
    e.add_field(name="Clan defense", value=_trophies(tot_def))
    e.add_field(name="Net", value=_trophies(tot_off + tot_def))
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


def battle_lines(hits: Sequence[Hit], *, estimate: bool = False) -> list[str]:
    """One monospace line per battle: ``+28  ★★☆   86%  name``, columns aligned. Battles inferred
    from trophy changes say so; with ``estimate`` (Legend I, where the trophies fix the stars)
    they show the likely stars and ``~90%``. Auto-filled defenses say so instead of stars and %."""
    width = max([3, *(len(signed(h.trophies)) for h in hits)])
    rows: list[tuple[str, str, str | None, str]] = []
    for h in hits:
        t = signed(h.trophies).rjust(width)
        if h.autofill:
            rows.append((t, "···", None, "auto-filled (no defense received)"))
        elif not h.detailed:
            guess = estimate_legend_result(h.trophies) if estimate else None
            if guess:
                rows.append((t, _stars(guess[0]), f"~{guess[1]}%", "inferred from trophies"))
            else:
                rows.append((t, "···", None, "inferred from trophies"))
        else:
            name = _clip(h.opponent_name or h.opponent_tag or "?", NAME_COLUMNS)
            rows.append((t, _stars(h.stars or 0), f"{h.destruction or 0}%", name))
    pw = max([4, *(len(pct) for _, _, pct, _ in rows if pct)])
    return [f"{t}  {st}  {pct.rjust(pw) if pct else '—'.center(pw)}  {text}" for t, st, pct, text in rows]


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
    estimate: bool = False,
    league: str | None = None,
) -> discord.Embed:
    """The /legends logs "today" view: the player's line, a summary block, then each attack and
    defense as a line (the newest kept when it's too long, with "… N earlier"). Each list's
    heading has its total with a green up / red down arrow."""
    e = _player_embed(name, tag, color)
    e.set_author(name=author.upper())
    first = _profile_line(town_hall, league, trophies)
    a_values = [h.trophies for h in attacks]
    d_values = [h.trophies for h in defenses]
    a_head = f"{kind_icon('attack')} **Attacks {count(a_values, cap)}** · {_trophies(sum(a_values))}"
    d_head = f"{kind_icon('defense')} **Defenses {count(d_values, cap)}** · {_trophies(sum(d_values))}"
    summary = "```\n" + summary_block(attacks, defenses, cap, start, end) + "\n```"
    a_lines = battle_lines(attacks, estimate=estimate)
    d_lines = battle_lines(defenses, estimate=estimate)
    dropped = [0, 0]

    def build() -> str:
        parts = [first] if first else []
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


# ------------------------------------------------------------------ /legends logs: the season's history

SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def season_span(window: Window) -> str:
    """``2 Nov – 30 Nov 2026`` (the last day shown is the day the season resets)."""
    s, e = window.start, window.end
    first = f"{s.day} {s:%b}" + ("" if s.year == e.year else f" {s.year}")
    return f"{first} – {e.day} {e:%b %Y}"


def season_table(summary: SeasonSummary, window: Window) -> list[str]:
    """``DAY ATK DEF +/- INIT FINAL`` lines, right-aligned: one per tracked day, the attack and
    defense totals with their battle count as superscript digits (``+268⁸``)."""
    first = window.start.date()
    rows = [["DAY", "ATK", "DEF", "+/-", "INIT", "FINAL"]]
    for d in summary.days:
        s = d.stats
        rows.append(
            [
                str((date.fromisoformat(d.day) - first).days + 1),
                signed(s.offense) + str(len(s.attacks)).translate(SUPERSCRIPT),
                signed(s.defense) + str(len(s.defenses)).translate(SUPERSCRIPT),
                signed(s.net),
                str(d.start) if d.start is not None else "?",
                str(d.end) if d.end is not None else "?",
            ]
        )
    widths = [max(display_width(r[i]) for r in rows) for i in range(len(rows[0]))]
    return ["  ".join(pad(c, widths[i], right=True) for i, c in enumerate(r)) for r in rows]


def season_logs_embed(
    *,
    name: str,
    tag: str,
    town_hall: int | None,
    league: str | None,
    trophies: int | None,
    window: Window,
    summary: SeasonSummary,
    today: int | None,
    now: datetime | None = None,
    color: discord.Color | None = None,
    limit: int = LOGS_DESC_LIMIT,
) -> discord.Embed:
    """The /legends logs history view: the player's line, the attacks and defenses won, and a
    day-by-day table (the newest days kept when it's too long). ``today``: the day's index in
    the season while it runs, None once it's over."""
    e = _player_embed(name, tag, color)
    line = _profile_line(town_hall, league, trophies)
    span = season_span(window)
    attacks_won = sum(1 for h in summary.attack_hits if h.stars is not None and h.stars >= 1)
    defenses_won = sum(1 for h in summary.defense_hits if h.stars == 0 and not h.autofill)
    won = SEP.join(
        (
            f"{kind_icon('attack')} **{attacks_won}** attack{'s' if attacks_won != 1 else ''} won",
            f"{kind_icon('defense')} **{defenses_won}** defense{'s' if defenses_won != 1 else ''} won",
        )
    )
    head = [line] if line else []
    head += [f"**Legend Season Logs ({span})**", won]
    rows = season_table(summary, window)
    if len(rows) == 1:
        e.description = "\n".join([*head, "No legend days recorded for this season."])
    else:
        body, dropped = rows[1:], 0
        while True:
            text = "\n".join([*head, "```", rows[0], *body[dropped:], "```"])
            if len(text) <= limit or len(body) - dropped <= 1:
                break
            dropped += 1
        e.description = text
    days = (window.end.date() - window.start.date()).days
    e.set_footer(text=f"Day {today + 1 if today is not None else days}/{days} ({span})")
    e.timestamp = now or datetime.now().astimezone()
    return e
