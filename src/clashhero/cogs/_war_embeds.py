"""Shared helpers for the war / CWL / capital / clan games cogs: periods, choices and pure
embed builders (they accept coc.py objects or anything with the same attributes).

Builders follow the style of the log posts (``_log_embeds``): the game's icons (Town Halls, war
stars, sword and shield, green and red arrows) in short labelled lines with bold numbers; clans
with their badges and both clans named in every score; players linked to their profiles. Custom
emojis don't render in code, so ranking tables stay plain text in code blocks, names last."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import discord
from discord import app_commands

from clashhero.cogs._log_embeds import arrow, clan_author, star_icon, th_icon, war_label, war_stars
from clashhero.repos.war import (
    AttackSummary,
    CWLMemberStats,
    DefenseSummary,
    PlayerWarHistory,
    Standing,
    th_spread,
    war_result,
)
from clashhero.services.emojis import EMOJIS
from clashhero.utils.fmt import clan_link, display_width, pad, player_link, table, ts
from clashhero.utils.resolve import resolve_player
from clashhero.utils.seasons import game_season, previous_game_season
from clashhero.utils.style import ICONS, SEP, chip, chip_width
from clashhero.utils.style import stars as text_stars

PERIOD_CHOICES = [
    app_commands.Choice(name="Last 7 days", value="7d"),
    app_commands.Choice(name="Last 30 days", value="30d"),
    app_commands.Choice(name="Last 60 days", value="60d"),
    app_commands.Choice(name="Last 90 days", value="90d"),
    app_commands.Choice(name="This season", value="season"),
    app_commands.Choice(name="Last season", value="last_season"),
    app_commands.Choice(name="All time", value="all"),
]

WAR_TYPE_CHOICES = [
    app_commands.Choice(name="All wars", value="all"),
    app_commands.Choice(name="Regular wars", value="random"),
    app_commands.Choice(name="CWL", value="cwl"),
    app_commands.Choice(name="Friendly wars", value="friendly"),
]

RESULT_WORDS = {"win": "Victory", "lose": "Defeat", "tie": "Tie"}
LIVE_WORDS = {"win": "Winning", "lose": "Losing", "tie": "Tied"}  # on battle day

# Discord limits.
FIELD_LIMIT = 1024
DESCRIPTION_LIMIT = 4096
MAX_FIELDS = 25
EMBED_LIMIT = 6000  # all titles, descriptions, fields, footers and authors in one message
BLANK = "\u200b"  # an empty field name


def period_bounds(
    period: str | None, now: datetime | None = None
) -> tuple[datetime | None, datetime | None, str]:
    """(since, until, label) for a PERIOD_CHOICES value. Defaults to 30 days."""
    now = now or datetime.now(UTC)
    period = period or "30d"
    if period.endswith("d") and period[:-1].isdigit():
        days = int(period[:-1])
        return now - timedelta(days=days), None, f"last {days} days"
    if period == "season":
        s = game_season(now)
        return s.start, None, "this season"
    if period == "last_season":
        s = previous_game_season(now)
        return s.start, s.end, "last season"
    return None, None, "all time"


def pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def bold_count(n: int, word: str) -> str:
    """``**12** attacks`` (``**1** attack``)."""
    return f"**{n:,}** {word}" if n == 1 else f"**{n:,}** {word}s"


def ordinal(n: int) -> str:
    """1st, 2nd, 3rd, 4th, 11th, 21st."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def escape(text: str | None) -> str:
    return discord.utils.escape_markdown(text or "?")


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _join(*parts: str) -> str:
    """The parts joined by spaces, empty ones left out (an icon that isn't uploaded drops out)."""
    return " ".join(p for p in parts if p)


def _th(th: int | None) -> str:
    return str(th) if th else "?"


def th_text(level: int | None) -> str:
    """The Town Hall's icon (``TH17`` when it isn't uploaded), ``TH?`` when unknown."""
    return th_icon(level) or "TH?"


def war_league_icon(name: str | None) -> str:
    """The war league's icon (``Crystal League I`` -> ``cwl_crystal_i``), empty if not uploaded."""
    if not name:
        return ""
    return EMOJIS.get("cwl_" + str(name).lower().replace(" league", "").replace(" ", "_"))


def result_arrow(result: str | None) -> str:
    """Green up for a win, red down for a loss, nothing for a tie."""
    return arrow("up") if result == "win" else arrow("down") if result == "lose" else ""


def state_value(war: Any) -> str:
    st = getattr(war, "state", None)
    return str(getattr(st, "value", st))


async def resolve_player_tags(
    interaction: discord.Interaction, player: str | None, user: discord.abc.User | None
) -> list[str]:
    """One explicit player, else every account linked to ``user`` (or the caller)."""
    if player:
        return [await resolve_player(interaction, player)]
    target = user or interaction.user
    tags = await interaction.client.db.get_linked_tags(target.id)
    if not tags:
        await resolve_player(interaction, None, target)  # raises a friendly error
    return tags


def war_sides(war: Any, clan_tag: str) -> tuple[Any, Any]:
    if getattr(war.clan, "tag", None) == clan_tag:
        return war.clan, war.opponent
    return war.opponent, war.clan


def side_badge(side: Any) -> str | None:
    """A coc.py clan's badge URL (the medium size), or None."""
    badge = getattr(side, "badge", None)
    url = getattr(badge, "url", None) or getattr(badge, "medium", None)
    return url if isinstance(url, str) else None


def set_clan(e: discord.Embed, name: str | None, tag: str | None, badge_url: str | None) -> discord.Embed:
    """The clan (badge and linked name) as the embed's author. When the author line already holds
    a notice (API maintenance, from ``make_embed``), the clan goes at the start of the footer
    instead, so call this after setting the footer."""
    if not e.author.name:
        return clan_author(e, name, tag, badge_url)
    if name or tag:
        e.set_footer(text=SEP.join(x for x in (name or tag, e.footer.text) if x), icon_url=badge_url or None)
    return e


def _when(t: Any) -> datetime | None:
    if t is None:
        return None
    raw = getattr(t, "time", t)
    return raw if raw.tzinfo else raw.replace(tzinfo=UTC)


# ------------------------------------------------------------------ layout helpers


def fit_lines(lines: Iterable[str], limit: int = FIELD_LIMIT) -> str:
    """Join whole lines, stopping before the text would pass ``limit`` characters."""
    out: list[str] = []
    size = 0
    for line in lines:
        cost = len(line) + (1 if out else 0)
        if size + cost > limit:
            if not out:
                out.append(line[:limit])
            break
        out.append(line)
        size += cost
    return "\n".join(out)


def fit_more(lines: Sequence[str], limit: int = FIELD_LIMIT) -> str:
    """Lines joined, the last ones replaced by "… and N more" when they'd pass ``limit``."""
    for keep in range(len(lines), -1, -1):
        rest = len(lines) - keep
        text = "\n".join([*lines[:keep], *([f"… and {rest} more"] if rest else [])])
        if len(text) <= limit:
            return text
    return f"{len(lines)} more"


def fit_sections(sections: Sequence[tuple[str, Sequence[str]]], limit: int = DESCRIPTION_LIMIT) -> str:
    """Headed sections separated by a blank line. Until it all fits ``limit`` characters, the
    longest section loses its last line (noting how many it left off)."""
    keep = [len(lines) for _, lines in sections]

    def render() -> str:
        parts = []
        for (head, lines), n in zip(sections, keep, strict=True):
            body = [*lines[:n], *([f"… and {len(lines) - n} more"] if n < len(lines) else [])]
            parts.append("\n".join([head, *body]))
        return "\n\n".join(parts)

    text = render()
    while len(text) > limit and any(keep):
        keep[max(range(len(keep)), key=lambda i: keep[i])] -= 1
        text = render()
    return text[:limit]


def split_block(block: str, limit: int = FIELD_LIMIT) -> list[str]:
    """``block`` in pieces of at most ``limit`` characters, split between lines."""
    pieces: list[str] = []
    cur: list[str] = []
    size = 0
    for line in block.split("\n"):
        line = line[:limit]
        if cur and size + 1 + len(line) > limit:
            pieces.append("\n".join(cur))
            cur, size = [], 0
        size += len(line) + (1 if cur else 0)
        cur.append(line)
    if cur:
        pieces.append("\n".join(cur))
    return pieces


def pack_fields(
    blocks: Sequence[str], *, budget: int = EMBED_LIMIT - 500, max_fields: int = MAX_FIELDS
) -> tuple[list[str], int]:
    """Field values for ``blocks``, one block per field (a block too long for one field continues
    in the next). Stops before passing ``max_fields`` or ``budget`` characters (field names
    included); returns (field values, number of whole blocks that fit)."""
    fields: list[str] = []
    used = 0
    for shown, block in enumerate(blocks):
        parts = split_block(block)
        cost = sum(len(p) + len(BLANK) for p in parts)
        if len(fields) + len(parts) > max_fields or used + cost > budget:
            return fields, shown
        fields += parts
        used += cost
    return fields, len(blocks)


def add_fields(
    e: discord.Embed, fields: Sequence[tuple[str, str]], *, inline: bool = False, reserve: int = 150
) -> int:
    """Add (name, value) fields while the embed stays within Discord's limits (``reserve``
    characters kept back for a footer); returns how many were added."""
    for i, (name, value) in enumerate(fields):
        name, value = _cut(name, 256), _cut(value, FIELD_LIMIT)
        if len(e.fields) >= MAX_FIELDS or len(e) + len(name) + len(value) + reserve > EMBED_LIMIT:
            return i
        e.add_field(name=name, value=value, inline=inline)
    return len(fields)


def rank_table(
    rows: Sequence[Sequence[object]], headers: Sequence[str], *, limit: int = DESCRIPTION_LIMIT - 200
) -> str:
    """Ranking table in a code block: uppercase header, two spaces between columns, values
    right-aligned so digits line up, and the last column (names) unpadded so a long name can't
    push the others out of line. Rows that would take it past ``limit`` characters are left off."""
    cells = [[str(c).replace("`", "'") for c in r] for r in rows]
    last = len(headers) - 1
    widths = [max(display_width(r[i]) for r in [list(headers), *cells]) for i in range(last)]

    def line(r: Sequence[str], *, head: bool = False) -> str:
        cols = [pad(c, widths[i], right=not head) for i, c in enumerate(r[:last])]
        return "  ".join([*cols, r[last]]).rstrip()

    lines = [line(headers, head=True)]
    size = len(lines[0]) + len("```\n\n```")
    for r in cells:
        text = line(r)
        if size + 1 + len(text) > limit:
            break
        lines.append(text)
        size += 1 + len(text)
    return "```\n" + "\n".join(lines) + "\n```"


def _plain(name: str | None, limit: int) -> str:
    """A name cut to ``limit`` for a code block (no backticks to break out of it)."""
    return (name or "?")[:limit].replace("`", "'")


# ------------------------------------------------------------------ live war


def _th_counts(members: Iterable[Any]) -> dict[int, int]:
    return dict(Counter(m.town_hall for m in members))


def th_columns(ours: Sequence[Any], theirs: Sequence[Any]) -> tuple[list[str], list[str]]:
    """For each side, a line per Town Hall that either side has, highest first: the Town Hall's
    icon and how many (– for none). Both lists have the same rows, so side by side they line up."""
    a, b = _th_counts(ours), _th_counts(theirs)
    ths = sorted(set(a) | set(b), reverse=True)

    def column(counts: dict[int, int]) -> list[str]:
        return [f"{th_text(th)} **{counts[th]}**" if counts.get(th) else f"{th_text(th)} –" for th in ths]

    return column(a), column(b)


def war_overview_embed(
    war: Any, clan_tag: str, color: discord.Color, *, label: str | None = None
) -> discord.Embed:
    """A live war: our clan (badge) as the author; the opponent, linked and with its badge as the
    thumbnail, in the title; the war's state and timer (and who's ahead); then a column per clan,
    ours first and named, with stars and destruction, attacks used and its Town Halls.
    ``label``: what the title calls the war (default "War" or "CWL")."""
    ours, theirs = war_sides(war, clan_tag)
    state = state_value(war)
    is_cwl = bool(getattr(war, "is_cwl", False))
    per = getattr(war, "attacks_per_member", None) or (1 if is_cwl else 2)
    total = war.team_size * per
    size = f"{war.team_size} vs {war.team_size}"
    if state == "preparation":
        lines = [f"**Preparation day** · {size}", f"Battle day starts {ts(_when(war.start_time))}"]
    else:
        result = war_result(ours.stars, theirs.stars, ours.destruction, theirs.destruction)
        if state == "inWar":
            ends = f"{LIVE_WORDS[result]} · ends {ts(_when(war.end_time))}"
            lines = [f"**Battle day** · {size}", _join(result_arrow(result), ends)]
        else:
            head = f"**{RESULT_WORDS[result]}** · {size}"
            lines = [_join(result_arrow(result), head), f"Ended {ts(_when(war.end_time))}"]
    title = f"{label or ('CWL' if is_cwl else 'War')} vs {escape(theirs.name)}"
    e = discord.Embed(
        title=_cut(title, 256),
        url=clan_link(theirs.tag) if getattr(theirs, "tag", None) else None,
        description="\n".join(lines),
        color=color,
    )
    clan_author(e, ours.name, ours.tag, side_badge(ours))
    if badge := side_badge(theirs):
        e.set_thumbnail(url=badge)
    for side, town_halls in zip((ours, theirs), th_columns(ours.members, theirs.members), strict=True):
        rows = []
        if state != "preparation":
            rows += [
                f"{star_icon()} **{side.stars}** · {side.destruction:.2f}%",
                f"{ICONS['war']} **{side.attacks_used}**/{total} attacks",
            ]
        if rows and town_halls:
            rows.append("")  # a blank line between the score and the Town Halls
        e.add_field(name=_cut(escape(side.name), 256), value=fit_lines([*rows, *town_halls]) or "—")
    return e


def lineup_embed(war: Any, clan_tag: str, color: discord.Color, *, label: str | None = None) -> discord.Embed:
    """Both lineups by map position in a table (plain text: it's a code block), with the stars
    our best attack took on each of their bases. Our clan as the author, the opponent's badge as
    the thumbnail."""
    ours, theirs = war_sides(war, clan_tag)
    rows = []
    our_members = sorted(ours.members, key=lambda m: m.map_position)
    their_members = sorted(theirs.members, key=lambda m: m.map_position)
    for i in range(max(len(our_members), len(their_members))):
        a = our_members[i] if i < len(our_members) else None
        b = their_members[i] if i < len(their_members) else None
        rows.append(
            (
                i + 1,
                f"TH{a.town_hall} {_plain(a.name, 12)}" if a else "",
                f"TH{b.town_hall} {_plain(b.name, 12)}" if b else "",
                _stars_on(war, b) if b else "",
            )
        )
    label = label or ("CWL" if getattr(war, "is_cwl", False) else "War")
    e = discord.Embed(
        title=_cut(f"{label} lineup vs {escape(theirs.name)}", 256),
        url=clan_link(theirs.tag) if getattr(theirs, "tag", None) else None,
        color=color,
    )
    clan_author(e, ours.name, ours.tag, side_badge(ours))
    if badge := side_badge(theirs):
        e.set_thumbnail(url=badge)
    e.description = table(rows, ["#", _plain(ours.name, 15), _plain(theirs.name, 15), "★"])[:4096]
    e.set_footer(text="★ = our best attack on each of their bases")
    return e


def _stars_on(war: Any, defender: Any) -> str:
    best = 0
    for a in getattr(war, "attacks", None) or []:
        if a.defender_tag == defender.tag:
            best = max(best, a.stars)
    return text_stars(best) if best else ""


def remaining_embed(
    e: discord.Embed,
    war: Any,
    clan_tag: str,
    left: Sequence[tuple[Mapping[str, Any], int]],
    mentions: Mapping[str, int] | None = None,
) -> discord.Embed:
    """Fill ``e`` with who still has attacks: the war and its timer, the total, then the players
    grouped by attacks left (map position, Town Hall icon, name and their Discord user when
    ``mentions`` has them). ``left``: (member with tag/name/map_position/town_hall, attacks left)."""
    mentions = mentions or {}
    ours, theirs = war_sides(war, clan_tag)
    label = "CWL" if getattr(war, "is_cwl", False) else "War"
    if state_value(war) == "preparation":
        when = f"battle day starts {ts(_when(war.start_time))}"
    else:
        when = f"ends {ts(_when(war.end_time))}"
    head = [f"{label} vs **{escape(theirs.name)}** · {when}"]
    if not left:
        head.append(f"{arrow('up')} Everyone has used all their attacks.")
    else:
        total = sum(n for _, n in left)
        head.append(f"{ICONS['war']} {bold_count(total, 'attack')} left · {bold_count(len(left), 'player')}")
    groups: dict[int, list[str]] = {}
    for m, n in left:
        user = f"<@{mentions[m['tag']]}>" if m["tag"] in mentions else ""
        line = _join(f"**{m['map_position']}.**", th_icon(m.get("town_hall")), escape(m["name"]), user)
        groups.setdefault(n, []).append(line)
    sections = [("\n".join(head), [])]
    sections += [
        (f"**{plural(n, 'attack')} left**", lines) for n, lines in sorted(groups.items(), reverse=True)
    ]
    e.description = fit_sections(sections, DESCRIPTION_LIMIT - 96)
    set_clan(e, ours.name, ours.tag, side_badge(ours))
    return e


def war_log_field(entry: Any) -> tuple[str, str]:
    """A war log entry as a field: the result (green up / red down) and the opponent in the name;
    both clans' stars and destruction (ours first) and when it ended in the value. A CWL entry
    has no opponent: just the season's stars."""
    end = ts(_when(getattr(entry, "end_time", None)))
    if getattr(entry, "is_league_entry", False):
        return f"{ICONS['cwl']} Clan War League", f"{star_icon()} **{entry.clan.stars}** stars · ended {end}"
    res = str(getattr(entry.result, "value", entry.result) or "")
    size = f"{entry.team_size} vs {entry.team_size}"
    name = _join(
        result_arrow(res), f"{RESULT_WORDS.get(res, 'War')} vs {escape(entry.opponent.name)} ({size})"
    )
    c, o = entry.clan, entry.opponent
    value = (
        f"{star_icon()} **{c.stars}** – {o.stars} · **{c.destruction:.1f}%** – {o.destruction:.1f}%"
        f" · ended {end}"
    )
    return name, value


def war_log_embed(e: discord.Embed, entries: Sequence[Any]) -> discord.Embed:
    """Fill ``e`` with a field per war log entry (newest first), as many as fit."""
    fields = [war_log_field(x) for x in entries]
    shown = add_fields(e, fields)
    if not fields:
        e.description = "No wars in the log."
    elif shown < len(fields):
        e.set_footer(text=f"Showing the latest {shown} of {len(fields)}")
    return e


def calls_embed(
    e: discord.Embed,
    war: Mapping[str, Any],
    callers: Sequence[Mapping[str, Any]],
    enemies: Mapping[int, Mapping[str, Any]],
    best: Mapping[str, int],
    names: Mapping[str, str],
) -> discord.Embed:
    """Fill ``e`` with the claimed enemy bases by map position: the base (Town Hall icon and
    name) with the stars we already took on it, then who claimed it and their note. ``war`` is a
    wars row; ``enemies`` their members by map position; ``best`` our best stars by defender tag."""
    lines = []
    for c in callers:
        enemy = enemies.get(c["position"])
        base = _join(th_icon(enemy.get("town_hall")), escape(enemy["name"])) if enemy else "?"
        got = best.get(enemy["player_tag"], 0) if enemy else 0
        who = f"**{escape(names[c['player_tag']])}**" if names.get(c["player_tag"] or "") else ""
        who = _join(who, f"<@{c['user_id']}>" if c["user_id"] else "")
        note = f" · _{escape(c['note'])}_" if c["note"] else ""
        lines.append(_join(f"**{c['position']}.**", base, war_stars(got) if got else "", "→", who) + note)
    e.description = (
        fit_more(lines, DESCRIPTION_LIMIT - 96) if lines else "No bases claimed yet. Use `/caller assign`."
    )
    opp = war.get("opponent_name") or "?"
    e.title = _cut(f"{war_label(war)} calls vs {escape(opp)}", 256)
    e.url = clan_link(war["opponent_tag"]) if war.get("opponent_tag") else None
    if war.get("opponent_badge_url"):
        e.set_thumbnail(url=war["opponent_badge_url"])
    if war.get("team_size"):
        e.set_footer(text=f"{len(callers)} of {war['team_size']} bases claimed")
    return e


# ------------------------------------------------------------------ /war history


def history_embed(
    name: str, label: str, h: PlayerWarHistory, color: discord.Color, *, tag: str | None = None
) -> discord.Embed:
    """A player's (or a user's accounts') war record: "Name (#TAG)" linked to the profile as the
    title (one account), offense and defense side by side, the three-star rate overall, on first
    hits and by Town Hall difference, then the latest attacks."""
    o, d = h.offense, h.defense
    e = discord.Embed(
        title=_cut(f"{escape(name)} ({tag})" if tag else escape(name), 256),
        url=player_link(tag) if tag else None,
        description=f"{ICONS['war']} **War history** · {label} · {bold_count(h.wars, 'war')}",
        color=color,
    )
    offense = [f"{ICONS['war']} {bold_count(o.attacks, 'attack')}"]
    if o.missed:
        offense.append(f"{arrow('down')} **{o.missed}** missed")
    offense += [
        f"{star_icon()} **{o.avg_stars:.2f}** avg stars",
        f"**{o.avg_destruction:.1f}%** avg destruction",
    ]
    e.add_field(name="Offense", value="\n".join(offense))
    defense = [
        f"{ICONS['defense']} **{d.defenses}** times attacked",
        f"**{pct(d.hold_rate)}** held, not tripled",
        f"{star_icon()} **{d.avg_stars:.2f}** avg stars allowed",
    ]
    e.add_field(name="Defense", value="\n".join(defense))
    e.add_field(name="Three-star rate", value=triple_rate_lines(o), inline=False)
    if h.recent:
        recent = split_block(fit_lines(recent_attack_lines(h.recent), 2 * FIELD_LIMIT))
        for i, part in enumerate(recent):
            e.add_field(name="Recent attacks" if i == 0 else BLANK, value=part, inline=False)
    return e


def triple_rate_lines(o: AttackSummary) -> str:
    """The share of attacks that took three stars: overall, as the first hit on a base, and
    against a higher, the same and a lower Town Hall."""

    def line(text: str, triples: int, n: int) -> str:
        rate = f"**{triples / n * 100:.0f}%**" if n else "**–**"
        return f"{rate} {text} ({triples}/{n})"

    lines = [
        f"{war_stars(3)} {line('of all attacks', o.triples, o.attacks)}",
        line("as the first hit on a base", o.fresh_triples, o.fresh),
    ]
    for key, text in (("up", "a higher"), ("even", "the same"), ("down", "a lower")):
        n, triples = o.buckets[key]
        lines.append(line(f"against {text} Town Hall", triples, n))
    return "\n".join(lines)


def recent_attack_lines(recent: Sequence[Mapping[str, Any]]) -> list[str]:
    """A line per attack, newest first: the stars (silver = already taken, gold = new),
    destruction, attacker's and defender's Town Halls, and the opponent (and CWL)."""
    return [
        _join(
            war_stars(a["stars"], a.get("new_stars")),
            f"**{a['destruction']:.0f}%**",
            f"{th_text(a['attacker_th'])} → {th_text(a['defender_th'])}",
            f"vs {escape(a['opponent_name'])}",
            "· CWL" if a["war_type"] == "cwl" else "",
        )
        for a in recent
    ]


# ------------------------------------------------------------------ /stats rankings

RANK_HEADERS = ("#", "TH", "RATE", "HITS", "NAME")


def attack_rank_table(rows: Sequence[tuple[str, AttackSummary]], names: dict[str, str]) -> str:
    """RATE is the 3★ rate, HITS is triples / attacks."""
    data = [
        (i + 1, _th(s.town_hall), f"{s.triple_rate * 100:.1f}", f"{s.triples}/{s.attacks}", names.get(t, t))
        for i, (t, s) in enumerate(rows)
    ]
    return rank_table(data, RANK_HEADERS)


def defense_rank_table(rows: Sequence[tuple[str, DefenseSummary]], names: dict[str, str]) -> str:
    """RATE is the share of defenses held (not tripled), HITS is held / times attacked."""
    data = [
        (
            i + 1,
            _th(s.town_hall),
            f"{s.hold_rate * 100:.1f}",
            f"{s.defenses - s.triples_allowed}/{s.defenses}",
            names.get(t, t),
        )
        for i, (t, s) in enumerate(rows)
    ]
    return rank_table(data, RANK_HEADERS)


def attack_totals(rows: Sequence[tuple[str, AttackSummary]]) -> str:
    """Across the ranked players: attacks and missed, then the three-star rate and average stars."""
    hits = sum(s.attacks for _, s in rows)
    triples = sum(s.triples for _, s in rows)
    star_total = sum(s.stars for _, s in rows)
    missed = sum(s.missed for _, s in rows)
    lines = [
        _join(
            ICONS["war"],
            bold_count(hits, "attack"),
            f"· {arrow('down')} **{missed}** missed" if missed else "",
        )
    ]
    if hits:
        lines.append(
            f"{war_stars(3)} **{triples / hits * 100:.1f}%** tripled"
            f" · {star_icon()} **{star_total / hits:.2f}** avg stars"
        )
    return "\n".join(lines)


def defense_totals(rows: Sequence[tuple[str, DefenseSummary]]) -> str:
    """Across the ranked players: defenses, then the hold rate and average stars allowed."""
    n = sum(s.defenses for _, s in rows)
    tripled = sum(s.triples_allowed for _, s in rows)
    star_total = sum(s.stars_allowed for _, s in rows)
    lines = [f"{ICONS['defense']} {bold_count(n, 'defense')}"]
    if n:
        lines.append(
            f"**{(n - tripled) / n * 100:.1f}%** held, not tripled"
            f" · {star_icon()} **{star_total / n:.2f}** avg stars allowed"
        )
    return "\n".join(lines)


def stats_footer(war_type: str | None, period: str, wars: int, town_hall: int | None = None) -> str:
    """``Regular wars · TH16 · last 30 days · 12 wars``"""
    parts = (war_type or "All wars", f"TH{town_hall}" if town_hall else None, period, plural(wars, "war"))
    return SEP.join(p for p in parts if p)


# ------------------------------------------------------------------ CWL season tables


def cwl_season_label(season: str | None) -> str:
    """``2026-11`` -> ``Nov 2026``."""
    try:
        return datetime.strptime(season or "", "%Y-%m").strftime("%b %Y")
    except ValueError:
        return season or "?"


def cwl_member_table(rows: Sequence[CWLMemberStats], *, missed: bool = False) -> str:
    """Season table in the order given (stars, then destruction): total stars, total destruction,
    attacks / rounds in the lineup, Town Hall, name. ``missed`` adds a MISS column."""
    headers = ["#", "★", "DEST", "HIT", "TH", "NAME"]
    if missed:
        headers.insert(4, "MISS")
    data = []
    for i, m in enumerate(rows):
        row: list[object] = [i + 1, m.stars, f"{m.destruction:.0f}%", f"{m.attacks}/{m.rounds}"]
        if missed:
            row.append(m.missed or "")
        data.append([*row, _th(m.town_hall), m.name])
    return rank_table(data, headers)


def cwl_attacks_summary(rows: Sequence[CWLMemberStats]) -> str:
    """``⚔️ **95**/105 attacks used · ▼ **3** missed`` across the clan's season."""
    used, rounds, missed = (sum(getattr(m, k) for m in rows) for k in ("attacks", "rounds", "missed"))
    return _join(
        ICONS["war"],
        f"**{used}**/{rounds} attacks used",
        f"· {arrow('down')} **{missed}** missed" if missed else "",
    )


def cwl_stars_summary(rows: Sequence[CWLMemberStats]) -> str:
    """The clan's season stars, triples and total destruction."""
    stars = sum(m.stars for m in rows)
    triples = sum(m.triples for m in rows)
    destruction = sum(m.destruction for m in rows)
    return (
        f"{star_icon()} **{stars}** stars · {war_stars(3)} **{triples}** triples"
        f" · **{destruction:,.0f}%** destruction"
    )


def standings_table(rows: Sequence[Standing]) -> str:
    data = [
        (i + 1, _plain(s.name, 15), s.total_stars, f"{s.destruction:.0f}", f"{s.wins}-{s.losses}-{s.ties}")
        for i, s in enumerate(rows)
    ]
    return table(data, ["#", "Clan", "★", "Dest", "W-L-T"])


def standings_description(
    rows: Sequence[Standing],
    clan_tag: str,
    *,
    round_no: int,
    rounds: int,
    league: str | None = None,
    movement: Mapping[str, str] | None = None,
) -> str:
    """The war league (icon and name) and round; our clan's place (with a green up / red down
    arrow when it moved in the last round) and stars; then the standings table."""
    head = SEP.join(
        x
        for x in (
            _join(war_league_icon(league), f"**{league}**") if league else "",
            f"Round **{round_no}**/{rounds}",
        )
        if x
    )
    lines = [head]
    place = next((i for i, s in enumerate(rows) if s.tag == clan_tag), None)
    if place is not None:
        s = rows[place]
        move = (movement or {}).get(clan_tag)
        lines.append(
            _join(
                arrow(move) if move in ("up", "down") else "",
                f"**{escape(s.name)}** · **{ordinal(place + 1)}** of {len(rows)} ·",
                star_icon(),
                bold_count(s.total_stars, "star"),
            )
        )
    return "\n".join(lines) + "\n" + standings_table(rows)


def th_spread_line(town_halls: Iterable[int]) -> str:
    """How many of each Town Hall, highest first: ``<th18> **5**  <th17> **3**``."""
    return "  ".join(f"{th_text(th)} **{n}**" for th, n in th_spread(town_halls).items()) or "—"


def cwl_group_fields(clans: Sequence[Any], clan_tag: str) -> list[tuple[str, str]]:
    """A field per clan in a CWL group, ours first (marked with a war star): its name and roster
    size, then its Town Halls."""
    fields = []
    for c in sorted(clans, key=lambda c: c.tag != clan_tag):
        ths = [m.town_hall for m in c.members]
        name = _join(
            star_icon() if c.tag == clan_tag else "", f"{escape(c.name)} · {plural(len(ths), 'player')}"
        )
        fields.append((name, th_spread_line(ths)))
    return fields


# ------------------------------------------------------------------ /cwl history


def cwl_round_line(r: Mapping[str, Any], dest_width: int | None = None) -> str:
    """Round chip, stars (silver = already taken, gold = new), destruction chip, the enemy base's
    map position and Town Hall (``R2 ★★☆ 78% → #21 <th17>``), or missed / not played yet."""
    rnd = chip(f"R{r.get('cwl_round') or '?'}")
    a = r.get("attack")
    if a:
        dest = chip(f"{a['destruction']:.0f}%", dest_width)
        target = f"#{a['target_pos']}" if a.get("target_pos") else "#?"
        return _join(
            rnd, war_stars(a["stars"], a.get("new_stars")), dest, "→", target, th_icon(a.get("defender_th"))
        )
    state = r.get("state")
    if state == "warEnded":
        return f"{rnd} {arrow('down')} missed"
    if state == "preparation":
        return f"{rnd} preparation day"
    return f"{rnd} not attacked yet"


def cwl_season_block(rows: Sequence[Mapping[str, Any]], *, with_name: bool = False) -> str:
    """One account's CWL season from its ``player_cwl_history`` rows (any order): the season (and
    the account), the clan with the account's Town Hall and usual map position, a line per round,
    then the attack and defense totals."""
    rows = sorted(rows, key=lambda r: r.get("cwl_round") or 0)
    latest = rows[-1]
    # Most common map position; on a tie, the latest round's (Counter keeps first-seen order).
    positions = Counter(r["map_position"] for r in reversed(rows) if r.get("map_position"))
    pos = positions.most_common(1)[0][0] if positions else None
    th = max((r["town_hall"] for r in rows if r.get("town_hall")), default=None)
    name = f"· {escape(latest.get('name'))}" if with_name else ""
    lines = [_join(ICONS["cwl"], f"**{cwl_season_label(latest.get('cwl_season'))}**", name)]
    where = (
        escape(latest.get("clan_name") or latest.get("clan_tag")),
        th_icon(th),
        f"map position **{pos}**" if pos else "",
    )
    lines.append(SEP.join(x for x in where if x))

    attacks = [r["attack"] for r in rows if r.get("attack")]
    w = chip_width(f"{a['destruction']:.0f}%" for a in attacks) or None
    lines += [cwl_round_line(r, w) for r in rows]

    decided = len(attacks) + sum(1 for r in rows if not r.get("attack") and r.get("state") == "warEnded")
    if decided:
        lines.append(
            f"{ICONS['war']} **{len(attacks)}**/{decided} attacks"
            f" · {star_icon()} {bold_count(sum(a['stars'] for a in attacks), 'star')}"
            f" · **{sum(a['destruction'] for a in attacks):.0f}%** destruction"
        )
    defenses = [r["defense"] for r in rows if r.get("defense")]
    if defenses:
        given = sum(d["stars"] for d in defenses)
        lines.append(
            f"{ICONS['defense']} {bold_count(len(defenses), 'defense')}"
            f" · {star_icon()} {bold_count(given, 'star')} allowed"
        )
    return "\n".join(lines)


def cwl_history_blocks(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """A block per CWL season and account, newest season first. With several accounts, each
    header names the account."""
    groups: dict[tuple, list[Mapping[str, Any]]] = {}
    for r in rows:
        key = (r.get("cwl_season") or "", r.get("player_tag"), r.get("clan_tag"))
        groups.setdefault(key, []).append(r)
    with_name = len({r.get("player_tag") for r in rows}) > 1
    keys = sorted(groups, key=lambda k: k[0], reverse=True)  # stable: keeps the rows' order
    return [cwl_season_block(groups[k], with_name=with_name) for k in keys]


def fill_cwl_history(e: discord.Embed, rows: Sequence[Mapping[str, Any]]) -> discord.Embed:
    """Add the season blocks to ``e`` as fields, as many as fit in one message, with a footer
    saying how many were left off."""
    blocks = cwl_history_blocks(rows)
    if not blocks:
        e.description = "No CWL wars recorded for these accounts."
        return e
    fields, shown = pack_fields(blocks, budget=EMBED_LIMIT - len(e) - 150)
    for value in fields:
        e.add_field(name=BLANK, value=value, inline=False)
    if shown < len(blocks):
        e.set_footer(text=f"Newest first · showing {shown} of {len(blocks)}; pick a season to see older ones")
    return e
