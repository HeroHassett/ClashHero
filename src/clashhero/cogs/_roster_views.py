"""Roster actions shared by slash commands, the persistent roster buttons and the auto-ping loop.

Signup / opt-out / refresh live here so the Signup button, ``/roster manage add`` and applying a
``/roster suggest`` lineup apply the same rules (closed lock, capacity, eligibility, linking,
per-user and per-category limits) with the same role and log side effects.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import coc
import discord
from discord.utils import escape_markdown

from clashhero.cogs._base import send_error
from clashhero.cogs._log_embeds import arrow, clan_author
from clashhero.repos import roster_scoring as scoring
from clashhero.repos import rosters as repo
from clashhero.services.emojis import EMOJIS
from clashhero.services.outbox import send_server_log, send_to_channel
from clashhero.services.snapshots import home_units
from clashhero.utils.checks import has_permission
from clashhero.utils.fmt import chunk_lines, display_width, make_embed, pad, ts
from clashhero.utils.style import ICONS, SEP, chip, fail_embed, ok_embed

log = logging.getLogger(__name__)

FIELD_LIMIT = 1024
# Discord allows 6000 characters over a whole embed; a roster post with icons that would go over
# falls back to plain text (app emoji markup is ~30 characters each).
EMBED_LIMIT = 5900

CATEGORY_LABELS = {"cwl": "CWL", "war": "War", "esports": "E-Sports", "trophy": "Trophy"}
CATEGORY_ICONS = {"cwl": "cwl", "war": "war", "trophy": "trophy", "esports": "🎮"}  # ICONS keys or emoji


def category_label(category: str) -> str:
    """The roster category with its game icon: ``🏆 CWL``."""
    key = CATEGORY_ICONS.get(category, "")
    return f"{ICONS.get(key, key)} {CATEGORY_LABELS.get(category, category.title())}".strip()


def icon_stat(icon: str, value: object, fallback: str) -> str:
    """``{icon}**16**``, or ``TH **16**`` when the icon isn't uploaded."""
    return f"{icon}**{value}**" if icon else f"{fallback} **{value}**"


def th_range(low: int | None, high: int | None) -> str:
    """A Town Hall requirement with the Town Hall icons: ``<th14>**14+**``, ``TH **14–16**``."""
    lo_icon, hi_icon = EMOJIS.town_hall(low), EMOJIS.town_hall(high)
    if low and high:
        if lo_icon and hi_icon:
            return f"{lo_icon}**{low}** – {hi_icon}**{high}**"
        return f"TH **{low}–{high}**"
    if low:
        return icon_stat(lo_icon, f"{low}+", "TH")
    return f"up to {icon_stat(hi_icon, high, 'TH')}" if high else ""


def clip_lines(lines: Sequence[str], limit: int = FIELD_LIMIT) -> str:
    """Join lines, dropping whole lines (with an "… N more" note) to stay under ``limit``."""
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    out: list[str] = []
    used = 0
    for i, line in enumerate(lines):
        more = f"… {len(lines) - i} more"
        if used + len(line) + 1 > limit - len(more) - 1:
            out.append(more)
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def names_text(names: Sequence[str], limit: int = 1500) -> str:
    """Bold, comma-separated names, cut short with "… N more" past ``limit`` characters."""
    out: list[str] = []
    used = 0
    for i, name in enumerate(names):
        item = f"**{name}**"
        if out and used + len(item) + 2 > limit:
            out.append(f"… {len(names) - i} more")
            break
        out.append(item)
        used += len(item) + 2
    return ", ".join(out)


@dataclass
class SignupResult:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = []
        if self.added:
            lines.append("Signed up: " + ", ".join(self.added))
        if self.updated:
            lines.append("Updated group: " + ", ".join(self.updated))
        lines += [f"❌ {e}" for e in self.errors]
        return "\n".join(lines) or "Nothing changed."

    def embed(self) -> discord.Embed:
        """Green with what changed (and a "Skipped" field for errors), or red when nothing did."""
        if self.added or self.updated:
            parts = []
            if self.added:
                parts.append(f"Signed up {names_text(self.added)}.")
            if self.updated:
                parts.append(f"Updated the group for {names_text(self.updated)}.")
            e = ok_embed("\n".join(parts))
            if self.errors:
                e.add_field(
                    name=f"Skipped ({len(self.errors)})",
                    value=clip_lines([f"❌ {x}" for x in self.errors]),
                    inline=False,
                )
            return e
        if len(self.errors) == 1:
            return fail_embed(self.errors[0])
        if not self.errors:
            return fail_embed("Nothing changed.")
        e = fail_embed(f"Nobody was signed up ({len(self.errors)} skipped).")
        e.add_field(name="Why", value=clip_lines([f"• {x}" for x in self.errors]), inline=False)
        return e


def removed_embed(removed: Sequence[str], roster_name: str) -> discord.Embed:
    """Result of an opt-out / removal."""
    if not removed:
        return fail_embed(f"None of those were on **{roster_name}**.")
    return ok_embed(f"Removed {names_text(removed)} from **{roster_name}**.")


def apply_embed(roster_name: str, summary: str) -> discord.Embed:
    """Result of applying a suggested lineup (``summary`` from ``apply_suggestion``)."""
    e = ok_embed(f"Lineup applied to **{roster_name}**.")
    e.description += "\n\n" + summary[:3800]
    return e


# ------------------------------------------------------------------ player info


async def player_info(bot, tag: str) -> dict[str, Any] | None:
    """name / town_hall / heroes_total from the collector cache, falling back to the API."""
    row = await bot.db.get_player_row(tag)
    if row is not None and row["town_hall"]:
        return {"name": row["name"], "town_hall": row["town_hall"], "heroes_total": row["heroes_total"]}
    try:
        p = await bot.coc.get_player(tag)
    except coc.NotFound:
        return None
    except coc.HTTPException as e:
        log.warning("Player lookup failed for %s: %s", tag, e)
        return None
    heroes = sum(level for _, level in home_units(p, "heroes"))
    return {"name": p.name, "town_hall": p.town_hall, "heroes_total": heroes}


# ------------------------------------------------------------------ actions


async def signup(
    bot,
    guild: discord.Guild,
    roster,
    tags: Sequence[str],
    *,
    actor: discord.abc.User,
    group_id: int | None = None,
    as_manager: bool = False,
) -> SignupResult:
    """Sign accounts up. Self-signups need the accounts to be linked to ``actor``; manager
    adds bypass the closed lock and per-user limit but still respect eligibility."""
    db = bot.db
    res = SignupResult()
    if roster["closed"] and not as_manager:
        res.errors.append("This roster is closed.")
        return res
    if group_id is None:
        group_id = await repo.default_group_id(db, roster["id"])
    links = await db.links_for_tags(tags)
    for tag in tags:
        existing = await repo.get_member(db, roster["id"], tag)
        if existing is None and await repo.member_count(db, roster["id"]) >= roster["max_members"]:
            res.errors.append(f"`{tag}`: roster is full ({roster['max_members']}).")
            continue
        owner = links.get(tag)
        if not as_manager and owner != actor.id:
            res.errors.append(f"`{tag}` isn't linked to you. Use `/link create` first.")
            continue
        if owner is None and not roster["allow_unlinked"]:
            res.errors.append(f"`{tag}` isn't linked to a Discord user (roster requires linked accounts).")
            continue
        info = await player_info(bot, tag)
        if info is None:
            res.errors.append(f"`{tag}`: player not found.")
            continue
        reason = repo.check_eligibility(roster, info["town_hall"], info["heroes_total"])
        if reason:
            res.errors.append(f"{info['name']} `{tag}`: {reason}.")
            continue
        if not roster["allow_multi_signup"]:
            other = await repo.other_roster_in_category(
                db, roster["guild_id"], roster["category"], tag, roster["id"]
            )
            if other is not None:
                res.errors.append(f"{info['name']} is already on **{other['name']}** ({roster['category']}).")
                continue
        if (
            existing is None
            and owner is not None
            and roster["max_accounts_per_user"]
            and not as_manager
            and await repo.count_user_accounts(db, roster["id"], owner) >= roster["max_accounts_per_user"]
        ):
            res.errors.append(f"You can sign up at most {roster['max_accounts_per_user']} account(s).")
            continue
        added = await repo.add_member(
            db,
            roster["id"],
            tag,
            user_id=owner,
            group_id=group_id,
            name=info["name"],
            town_hall=info["town_hall"],
            heroes_total=info["heroes_total"],
        )
        (res.added if added else res.updated).append(info["name"])
        if owner is not None and roster["role_id"]:
            await _set_role(guild, owner, roster["role_id"], add=True)
    if res.added or res.updated:
        await _log_change(
            bot, guild, roster, actor, "joined" if res.added else "moved", res.added + res.updated
        )
        await refresh_post(bot, roster["id"])
    return res


async def opt_out(
    bot, guild: discord.Guild, roster, tags: Sequence[str], *, actor: discord.abc.User
) -> list[str]:
    """Remove accounts; drops the roster role from users with no accounts left. Returns names removed."""
    db = bot.db
    removed = []
    for tag in tags:
        row = await repo.get_member(db, roster["id"], tag)
        if row is None:
            continue
        await repo.remove_member(db, roster["id"], tag)
        removed.append(row["name"] or tag)
        uid = row["user_id"]
        if uid and roster["role_id"] and not await repo.user_has_accounts(db, roster["id"], uid):
            await _set_role(guild, uid, roster["role_id"], add=False)
    if removed:
        await _log_change(bot, guild, roster, actor, "left", removed)
        await refresh_post(bot, roster["id"])
    return removed


async def _set_role(guild: discord.Guild, user_id: int, role_id: int, *, add: bool) -> None:
    role = guild.get_role(role_id)
    member = guild.get_member(user_id)
    if role is None or member is None:
        return
    try:
        if add and role not in member.roles:
            await member.add_roles(role, reason="Roster signup")
        elif not add and role in member.roles:
            await member.remove_roles(role, reason="Roster opt-out")
    except (discord.Forbidden, discord.HTTPException) as e:
        log.warning("Roster role update failed: %s", e)


CHANGE_STYLES = {  # verb -> (arrow, colour, wording)
    "joined": ("up", discord.Color.green(), "signed up"),
    "left": ("down", discord.Color.red(), "left"),
}


def change_embed(
    roster,
    verb: str,
    names: Sequence[str],
    actor_mention: str,
    *,
    signed_up: int | None = None,
    clan_name: str | None = None,
    badge_url: str | None = None,
    at: datetime | None = None,
) -> discord.Embed:
    """A roster change for the server log: the clan (badge) as the author, the roster as the
    title, who signed up (green ▲), left (red ▼) or moved, and who did it."""
    direction, color, wording = CHANGE_STYLES.get(verb, ("", discord.Color.blue(), verb))
    who = names_text([escape_markdown(n) for n in names])
    line = " ".join(x for x in (arrow(direction) if direction else "", who, wording) if x)
    e = discord.Embed(title=roster["name"], description=f"{line}\nby {actor_mention}", color=color)
    clan_author(e, clan_name or roster["clan_tag"], roster["clan_tag"], badge_url)
    footer = f"Roster #{roster['id']}"
    if signed_up is not None:
        footer += f"{SEP}{signed_up}/{roster['max_members']} signed up"
    e.set_footer(text=footer)
    e.timestamp = at
    return e


async def _log_change(bot, guild: discord.Guild, roster, actor, verb: str, names: Sequence[str]) -> None:
    clan = await bot.db.get_clan_row(roster["clan_tag"])
    embed = change_embed(
        roster,
        verb,
        names,
        actor.mention,
        signed_up=await repo.member_count(bot.db, roster["id"]),
        clan_name=clan["name"] if clan else None,
        badge_url=clan["badge_url"] if clan else None,
        at=datetime.now(UTC),
    )
    await send_server_log(bot, guild.id, "roster_change", embed)


async def apply_suggestion(
    bot,
    guild: discord.Guild,
    roster,
    confirmed: Sequence[str],
    substitutes: Sequence[str],
    *,
    actor: discord.abc.User,
) -> str:
    """Make the Confirmed group exactly ``confirmed`` and put ``substitutes`` in Substitute, via the
    normal manager signup (rules, roles and logs apply). Other signups in Confirmed move to
    Substitute; nobody is removed. Returns a summary."""
    db = bot.db
    conf_gid, sub_gid = await repo.suggestion_groups(db, roster["id"])
    current = {m["player_tag"]: m for m in await repo.list_members(db, roster["id"])}
    picked = set(confirmed) | set(substitutes)
    demoted = []
    for tag, m in current.items():
        if m["group_id"] == conf_gid and tag not in picked:
            await repo.move_member(db, roster["id"], tag, to_roster_id=roster["id"], group_id=sub_gid)
            demoted.append(m["name"] or tag)
    lines = []
    for label, tags, gid in (("Confirmed", confirmed, conf_gid), ("Substitute", substitutes, sub_gid)):
        todo = [t for t in tags if t not in current or current[t]["group_id"] != gid]
        if not todo:
            continue
        res = await signup(bot, guild, roster, todo, actor=actor, group_id=gid, as_manager=True)
        lines.append(f"**{label}**\n{res.summary()}")
    if demoted:
        await _log_change(bot, guild, roster, actor, "moved to Substitute", demoted)
        await refresh_post(bot, roster["id"])
        lines.append("**Moved out of Confirmed**: " + ", ".join(demoted))
    return "\n".join(lines) or "Roster already matches the suggestion."


# ------------------------------------------------------------------ auto-ping


def ping_chunks(header: str, users: Iterable[int], limit: int = 1900) -> list[str]:
    """Header followed by user mentions, split into messages under ``limit`` characters."""
    chunks, cur = [], header + "\n"
    for uid in sorted(users):
        mention = f"<@{uid}> "
        if len(cur) + len(mention) > limit:
            chunks.append(cur.rstrip())
            cur = ""
        cur += mention
    chunks.append(cur.rstrip())
    return chunks


def autoping_header(roster, kind: str, when: str) -> str:
    if kind == "end":
        text = f"{ICONS['time']} Signups for **{roster['name']}** close {ts(when, 'R')}."
    else:
        text = f"{ICONS['time']} **{roster['name']}** starts {ts(when, 'R')}."
    text += " " + (roster["autoping_message"] or "You're in the clan but haven't signed up yet.")
    if roster["channel_id"] and roster["message_id"]:
        text += (
            f"\nSign up here: https://discord.com/channels/"
            f"{roster['guild_id']}/{roster['channel_id']}/{roster['message_id']}"
        )
    return text


async def autoping_targets(db, roster) -> set[int]:
    """Discord users with an eligible account in the roster's clan that isn't signed up."""
    members = await repo.list_members(db, roster["id"])
    clan_tags = [
        r["player_tag"]
        for r in await db.clan_member_rows(roster["clan_tag"])
        if repo.check_eligibility(roster, r["town_hall"], r["heroes_total"]) is None
    ]
    links = await db.links_for_tags(set(clan_tags) | {m["player_tag"] for m in members})
    return repo.compute_ping_targets("unregistered", members, clan_tags, links)


async def send_autoping(bot, roster, kind: str, when: str) -> int:
    """Ping unregistered members for one auto-ping window. Returns how many users were pinged."""
    channel_id = roster["autoping_channel_id"] or roster["channel_id"]
    if not channel_id:
        return 0
    users = await autoping_targets(bot.db, roster)
    if not users:
        return 0
    for chunk in ping_chunks(autoping_header(roster, kind, when), users):
        await send_to_channel(bot, channel_id, content=chunk)
    return len(users)


# ------------------------------------------------------------------ rendering


def render_member_line(i: int, m, *, icons: bool = True) -> str:
    """One signup: number, Town Hall (its game icon, else ``TH16``), name, combined hero levels,
    tag and Discord user. Without ``icons`` it's plain text, for big rosters."""
    th = m["town_hall"]
    th_txt = (EMOJIS.town_hall(th) if icons and th else "") or chip(f"TH{th or '?'}")
    heroes = m["heroes_total"]
    if not heroes:
        hero_txt = ""
    else:
        hero_txt = f" {ICONS['heroes']}**{heroes}**" if icons else f" · {heroes}h"
    mention = f" <@{m['user_id']}>" if m["user_id"] else ""
    name = escape_markdown(m["name"] or "?")
    return f"`{i:>2}.` {th_txt} {name}{hero_txt} `{m['player_tag']}`{mention}"


def roster_info(roster, signed_up: int) -> list[str]:
    """The roster post's description: category and signups, requirements, times, auto-ping and
    whether it's closed."""
    info = [
        f"{category_label(roster['category'])} roster{SEP}**{signed_up}/{roster['max_members']}** signed up"
    ]
    reqs = [th_range(roster["min_th"], roster["max_th"])]
    if roster["min_heroes"]:
        reqs.append(f"{ICONS['heroes']} **{roster['min_heroes']}+** hero levels")
    if any(reqs):
        info.append("Requirements: " + SEP.join(x for x in reqs if x))
    times = []
    if roster["start_time"]:
        times.append(f"Starts {ts(roster['start_time'], 'f')}")
    if roster["end_time"]:
        times.append(f"{'signups' if times else 'Signups'} close {ts(roster['end_time'], 'R')}")
    if times:
        info.append(f"{ICONS['time']} " + SEP.join(times))
    autoping = repo.describe_autoping(roster)
    if autoping:
        info.append(f"🔔 {autoping}")
    if roster["closed"]:
        info.append("🔒 **Closed**: no self signups")
    return info


def render_roster_embed(
    roster,
    groups,
    members,
    clan_name: str | None,
    color: discord.Color,
    *,
    badge_url: str | None = None,
) -> discord.Embed:
    """Pure: build the roster embed from rows. The clan (with its badge) is the author, the roster
    name the title; each group is a field listing its signups. Big rosters drop the icons so the
    post stays within Discord's size limit."""
    embed = _render_roster_embed(roster, groups, members, clan_name, color, badge_url, icons=True)
    if len(embed) > EMBED_LIMIT:
        embed = _render_roster_embed(roster, groups, members, clan_name, color, badge_url, icons=False)
    return embed


def _render_roster_embed(
    roster,
    groups,
    members,
    clan_name: str | None,
    color: discord.Color,
    badge_url: str | None,
    *,
    icons: bool,
) -> discord.Embed:
    if roster["color"] is not None:
        color = discord.Color(roster["color"])
    embed = discord.Embed(
        title=roster["name"], description="\n".join(roster_info(roster, len(members))), color=color
    )
    clan_author(embed, clan_name or roster["clan_tag"], roster["clan_tag"], badge_url)
    ordered = repo.sort_members(members, roster["sort_by"])
    sections: list[tuple[str, list]] = [
        (g["name"], [m for m in ordered if m["group_id"] == g["id"]]) for g in groups
    ]
    known = {g["id"] for g in groups}
    ungrouped = [m for m in ordered if m["group_id"] not in known]
    if ungrouped:
        sections.append(("Ungrouped", ungrouped))
    n = 0
    for name, rows in sections:
        lines = []
        for m in rows:
            n += 1
            lines.append(render_member_line(n, m, icons=icons))
        chunks = chunk_lines(lines, FIELD_LIMIT - 10) if lines else ["—"]
        for i, chunk in enumerate(chunks):
            label = f"{name} ({len(rows)})" if i == 0 else f"{name} (cont.)"
            if len(embed.fields) < 25:
                embed.add_field(name=label, value=chunk.strip() or "—", inline=False)
    embed.set_footer(
        text=f"Roster #{roster['id']} · sorted by "
        + repo.SORT_OPTIONS.get(roster["sort_by"], roster["sort_by"])
    )
    return embed


async def build_roster_embed(bot, roster) -> discord.Embed:
    groups = await repo.list_groups(bot.db, roster["id"])
    members = await repo.list_members(bot.db, roster["id"])
    clan = await bot.db.get_clan_row(roster["clan_tag"])
    color = (await make_embed(bot, roster["guild_id"])).color
    return render_roster_embed(
        roster,
        groups,
        members,
        clan["name"] if clan else None,
        color,
        badge_url=clan["badge_url"] if clan else None,
    )


SUGGEST_MAX_ROWS = 50
SUGGEST_CHUNK = 3500
SUGGEST_HEADERS = ("#", "Player", "TH", "Hero", "CWL3*", "War3*", "Avg*", "Used", "Seen", "Score")


def _ratio(num: int, den: int) -> str:
    return f"{num}/{den}" if den else "-"


def suggestion_row(rank: int, s: scoring.Scored) -> list[str]:
    c, h = s.candidate, s.history
    name = c.name.replace("`", "'")[:12] + ("*" if c.signed_up else "")
    return [
        str(rank),
        name,
        str(c.town_hall or "?"),
        str(c.heroes or "-"),
        _ratio(h.cwl_triples, h.cwl_hits),
        _ratio(h.war_triples, h.war_hits),
        f"{h.avg_stars:.1f}" if h.avg_stars is not None else "-",
        _ratio(h.used, h.available),
        f"{int(s.seen_days)}d" if s.seen_days is not None else "?",
        f"{s.score:.0f}",
    ]


def suggestion_table_lines(scored: Sequence[scoring.Scored], size: int, substitutes: int) -> list[str]:
    """Monospace table lines (no code fences) with section markers for the picks."""
    shown = list(scored[:SUGGEST_MAX_ROWS])
    rows = [suggestion_row(i, s) for i, s in enumerate(shown, 1)]
    widths = [
        max(display_width(r[i]) for r in [list(SUGGEST_HEADERS), *rows]) for i in range(len(SUGGEST_HEADERS))
    ]
    right = {0, 2, 3, 4, 5, 6, 7, 8, 9}

    def fmt(cells: Sequence[str]) -> str:
        return " ".join(pad(c, widths[i], right=i in right) for i, c in enumerate(cells)).rstrip()

    lines = [fmt(SUGGEST_HEADERS)]
    for i, row in enumerate(rows):
        if i == 0:
            lines.append(f"-- Confirmed (top {min(size, len(scored))})")
        elif i == size and substitutes:
            lines.append("-- Substitutes")
        elif i == size + substitutes:
            lines.append("-- Not picked")
        lines.append(fmt(row))
    if len(scored) > len(shown):
        lines.append(f"... {len(scored) - len(shown)} more not shown")
    return lines


def render_suggestion_embeds(
    roster,
    scored: Sequence[scoring.Scored],
    excluded: Sequence[tuple[scoring.Candidate, str]],
    *,
    size: int,
    substitutes: int,
    seasons: Sequence[str],
    color: discord.Color,
) -> list[discord.Embed]:
    """Pure: the ranked suggestion table plus how the score works and who was left out."""
    lines = suggestion_table_lines(scored, size, substitutes)
    blocks = chunk_lines(lines, SUGGEST_CHUNK) if scored else []
    head = "\n".join(
        (
            f"**{len(scored)}** eligible from `{roster['clan_tag']}` and current signups",
            f"Top **{size}** → Confirmed{SEP}next **{substitutes}** → Substitute",
            "-# `*` after a name: already signed up",
        )
    )
    embeds = [
        discord.Embed(
            title=f"Suggested lineup · {roster['name']}",
            description=head + ("\n```\n" + blocks[0] + "```" if blocks else "\n\nNobody eligible."),
            color=color,
        )
    ]
    for block in blocks[1:]:
        embeds.append(discord.Embed(description="```\n" + block + "```", color=color))
    last = embeds[-1]
    season_txt = ", ".join(sorted(seasons)) if seasons else "none recorded"
    last.add_field(
        name="How the score works",
        value=(
            f"0-100 = {scoring.describe_weights()}.\n"
            f"3* = triples / attacks on an equal or higher TH (CWL seasons: {season_txt}; "
            f"war: last {scoring.WAR_LOOKBACK_DAYS} days, no friendlies). Rates use a Wilson lower "
            "bound and lean toward the pool median when there are few attacks; no history = pool "
            "median. Used = attacks used / available in ended wars. Seen = days since last active."
        )[:1024],
        inline=False,
    )
    if excluded:
        text = "\n".join(f"{c.name} `{c.tag}`: {reason}" for c, reason in excluded)
        if len(text) > 1000:
            text = text[:1000].rsplit("\n", 1)[0] + "\n…"
        last.add_field(name=f"Not eligible ({len(excluded)})", value=text, inline=False)
    return embeds


def roster_view(roster_id: int, *, closed: bool = False) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(SignupButton(roster_id, disabled=closed))
    view.add_item(OptOutButton(roster_id))
    view.add_item(RefreshButton(roster_id))
    return view


async def refresh_post(bot, roster_id: int) -> None:
    """Re-render the posted roster message, if any."""
    roster = await repo.get_roster(bot.db, roster_id)
    if roster is None or not roster["channel_id"] or not roster["message_id"]:
        return
    channel = bot.get_channel(roster["channel_id"])
    if channel is None:
        return
    try:
        msg = channel.get_partial_message(roster["message_id"])
        await msg.edit(
            embed=await build_roster_embed(bot, roster),
            view=roster_view(roster_id, closed=bool(roster["closed"])),
        )
    except discord.NotFound:
        await repo.update_roster(bot.db, roster_id, channel_id=None, message_id=None)
    except (discord.Forbidden, discord.HTTPException) as e:
        log.warning("Roster post refresh failed: %s", e)


# ------------------------------------------------------------------ ephemeral pickers


class _SignupPicker(discord.ui.View):
    def __init__(self, roster, accounts: list, groups: list) -> None:
        super().__init__(timeout=180)
        self.roster = roster
        self.group_id: int | None = None
        if roster["allow_group_selection"] and len(groups) > 1:
            gsel = discord.ui.Select(
                placeholder="Group (optional)",
                options=[
                    discord.SelectOption(label=g["name"][:100], value=str(g["id"])) for g in groups[:25]
                ],
                row=0,
            )

            async def on_group(interaction: discord.Interaction) -> None:
                self.group_id = int(gsel.values[0])
                await interaction.response.defer()

            gsel.callback = on_group
            self.add_item(gsel)
        options = [
            discord.SelectOption(
                label=f"{a['name'] or a['player_tag']}"[:100],
                description=f"TH{a['town_hall'] or '?'} · {a['player_tag']}",
                value=a["player_tag"],
            )
            for a in accounts[:25]
        ]
        limit = roster["max_accounts_per_user"] or len(options)
        asel = discord.ui.Select(
            placeholder="Choose account(s) to sign up",
            options=options,
            min_values=1,
            max_values=max(1, min(len(options), limit)),
            row=1,
        )

        async def on_accounts(interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True, thinking=True)
            roster_now = await repo.get_roster(interaction.client.db, self.roster["id"])
            if roster_now is None:
                await interaction.followup.send(
                    embed=fail_embed("That roster no longer exists."), ephemeral=True
                )
                return
            res = await signup(
                interaction.client,
                interaction.guild,
                roster_now,
                asel.values,
                actor=interaction.user,
                group_id=self.group_id,
            )
            await interaction.followup.send(embed=res.embed(), ephemeral=True)

        asel.callback = on_accounts
        self.add_item(asel)


class _OptOutPicker(discord.ui.View):
    def __init__(self, roster, members: list) -> None:
        super().__init__(timeout=180)
        sel = discord.ui.Select(
            placeholder="Choose account(s) to remove",
            options=[
                discord.SelectOption(label=(m["name"] or m["player_tag"])[:100], value=m["player_tag"])
                for m in members[:25]
            ],
            min_values=1,
            max_values=min(25, len(members)),
        )

        async def on_pick(interaction: discord.Interaction) -> None:
            roster_now = await repo.get_roster(interaction.client.db, roster["id"])
            if roster_now is None:
                await interaction.response.send_message(
                    embed=fail_embed("That roster no longer exists."), ephemeral=True
                )
                return
            removed = await opt_out(
                interaction.client, interaction.guild, roster_now, sel.values, actor=interaction.user
            )
            await interaction.response.send_message(
                embed=removed_embed(removed, roster_now["name"]), ephemeral=True
            )

        sel.callback = on_pick
        self.add_item(sel)


# ------------------------------------------------------------------ persistent buttons


class _RosterButton:
    """Mixin for the roster DynamicItems (DynamicItem subclasses must each declare a template)."""

    action = ""

    def __init__(
        self, roster_id: int, *, label: str, style: discord.ButtonStyle, disabled: bool = False
    ) -> None:
        super().__init__(
            discord.ui.Button(
                label=label, style=style, custom_id=f"roster:{self.action}:{roster_id}", disabled=disabled
            )
        )
        self.roster_id = roster_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            roster = await repo.get_roster(interaction.client.db, self.roster_id)
            if roster is None:
                await interaction.response.send_message(
                    embed=fail_embed("This roster no longer exists."), ephemeral=True
                )
                return
            await self.run(interaction, roster)
        except Exception as e:  # buttons have no command error handler
            await send_error(interaction, e)

    async def run(self, interaction: discord.Interaction, roster) -> None:
        raise NotImplementedError


class SignupButton(
    _RosterButton, discord.ui.DynamicItem[discord.ui.Button], template=r"roster:signup:(?P<id>\d+)"
):
    action = "signup"

    def __init__(self, roster_id: int, disabled: bool = False) -> None:
        super().__init__(roster_id, label="Sign up", style=discord.ButtonStyle.success, disabled=disabled)

    async def run(self, interaction: discord.Interaction, roster) -> None:
        if roster["closed"]:
            await interaction.response.send_message(
                embed=fail_embed("This roster is closed."), ephemeral=True
            )
            return
        db = interaction.client.db
        links = await db.get_links(interaction.user.id)
        if not links:
            await interaction.response.send_message(
                embed=fail_embed("You have no linked accounts. Use `/link create` first."), ephemeral=True
            )
            return
        eligible = [
            a
            for a in links
            if a["town_hall"] is None or repo.check_eligibility(roster, a["town_hall"], None) is None
        ]
        if not eligible:
            await interaction.response.send_message(
                embed=fail_embed("None of your linked accounts meet the requirements."), ephemeral=True
            )
            return
        groups = await repo.list_groups(db, roster["id"])
        await interaction.response.send_message(
            f"Sign up for **{roster['name']}**:", view=_SignupPicker(roster, eligible, groups), ephemeral=True
        )


class OptOutButton(
    _RosterButton, discord.ui.DynamicItem[discord.ui.Button], template=r"roster:optout:(?P<id>\d+)"
):
    action = "optout"

    def __init__(self, roster_id: int) -> None:
        super().__init__(roster_id, label="Opt out", style=discord.ButtonStyle.danger)

    async def run(self, interaction: discord.Interaction, roster) -> None:
        db = interaction.client.db
        mine = set(await db.get_linked_tags(interaction.user.id))
        members = [
            m
            for m in await repo.list_members(db, roster["id"])
            if m["player_tag"] in mine or m["user_id"] == interaction.user.id
        ]
        if not members:
            await interaction.response.send_message(
                embed=fail_embed("None of your accounts are on this roster."), ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Opt out of **{roster['name']}**:", view=_OptOutPicker(roster, members), ephemeral=True
        )


class RefreshButton(
    _RosterButton, discord.ui.DynamicItem[discord.ui.Button], template=r"roster:refresh:(?P<id>\d+)"
):
    action = "refresh"

    def __init__(self, roster_id: int) -> None:
        super().__init__(roster_id, label="Refresh", style=discord.ButtonStyle.secondary)

    async def run(self, interaction: discord.Interaction, roster) -> None:
        await interaction.response.edit_message(
            embed=await build_roster_embed(interaction.client, roster),
            view=roster_view(roster["id"], closed=bool(roster["closed"])),
        )


DYNAMIC_ITEMS = (SignupButton, OptOutButton, RefreshButton)


async def is_roster_manager(interaction: discord.Interaction) -> bool:
    return await has_permission(interaction, "roster")
