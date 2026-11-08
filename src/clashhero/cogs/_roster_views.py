"""Roster actions shared by slash commands, the persistent roster buttons and the auto-ping loop.

Signup / opt-out / refresh live here so the Signup button, ``/roster manage add`` and applying a
``/roster suggest`` lineup apply the same rules (closed lock, capacity, eligibility, linking,
per-user and per-category limits) with the same role and log side effects.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import coc
import discord

from clashhero.cogs._base import send_error
from clashhero.repos import roster_scoring as scoring
from clashhero.repos import rosters as repo
from clashhero.services.outbox import send_server_log, send_to_channel
from clashhero.utils.checks import has_permission
from clashhero.utils.fmt import chunk_lines, display_width, make_embed, pad, ts

log = logging.getLogger(__name__)

FIELD_LIMIT = 1024


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
        lines += [f"✗ {e}" for e in self.errors]
        return "\n".join(lines) or "Nothing changed."


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
    heroes = sum(h.level for h in (p.heroes or []) if getattr(h, "is_home_base", True))
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


async def _log_change(bot, guild: discord.Guild, roster, actor, verb: str, names: Sequence[str]) -> None:
    embed = await make_embed(
        bot,
        guild.id,
        title=f"Roster: {roster['name']}",
        description=f"{', '.join(names)} {verb} (by {actor.mention}).",
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
        text = f"Signups for **{roster['name']}** close {ts(when, 'R')}."
    else:
        text = f"**{roster['name']}** starts {ts(when, 'R')}."
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


def render_member_line(i: int, m) -> str:
    th = m["town_hall"] or "?"
    heroes = m["heroes_total"]
    hero_txt = f" · {heroes}h" if heroes else ""
    mention = f" <@{m['user_id']}>" if m["user_id"] else ""
    return f"`{i:>2}.` `TH{th}` {m['name'] or '?'}{hero_txt} `{m['player_tag']}`{mention}"


def render_roster_embed(
    roster, groups, members, clan_name: str | None, color: discord.Color
) -> discord.Embed:
    """Pure: build the roster embed from rows."""
    if roster["color"] is not None:
        color = discord.Color(roster["color"])
    title = f"{roster['name']}" + (f" · {clan_name}" if clan_name else "")
    info = [
        f"**{roster['category'].upper()}** roster for `{roster['clan_tag']}`",
        f"Members: **{len(members)}/{roster['max_members']}**",
    ]
    reqs = []
    if roster["min_th"] or roster["max_th"]:
        reqs.append(f"TH {roster['min_th'] or 1}–{roster['max_th'] or '∞'}")
    if roster["min_heroes"]:
        reqs.append(f"{roster['min_heroes']}+ hero levels")
    if reqs:
        info.append("Requirements: " + ", ".join(reqs))
    if roster["start_time"]:
        info.append(f"Starts {ts(roster['start_time'], 'f')}")
    if roster["end_time"]:
        info.append(f"Signups close {ts(roster['end_time'], 'R')}")
    autoping = repo.describe_autoping(roster)
    if autoping:
        info.append(f"🔔 {autoping}")
    if roster["closed"]:
        info.append("🔒 **Closed**")
    embed = discord.Embed(title=title, description="\n".join(info), color=color)
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
            lines.append(render_member_line(n, m))
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
    return render_roster_embed(roster, groups, members, clan["name"] if clan else None, color)


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
    head = (
        f"{len(scored)} eligible candidate(s) from `{roster['clan_tag']}` and current signups. "
        f"Top {size} → Confirmed, next {substitutes} → Substitute. `*` = already signed up."
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
                await interaction.followup.send("That roster no longer exists.", ephemeral=True)
                return
            res = await signup(
                interaction.client,
                interaction.guild,
                roster_now,
                asel.values,
                actor=interaction.user,
                group_id=self.group_id,
            )
            await interaction.followup.send(res.summary(), ephemeral=True)

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
                await interaction.response.send_message("That roster no longer exists.", ephemeral=True)
                return
            removed = await opt_out(
                interaction.client, interaction.guild, roster_now, sel.values, actor=interaction.user
            )
            await interaction.response.send_message(
                ("Removed: " + ", ".join(removed)) if removed else "Nothing removed.", ephemeral=True
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
                await interaction.response.send_message("This roster no longer exists.", ephemeral=True)
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
            await interaction.response.send_message("This roster is closed.", ephemeral=True)
            return
        db = interaction.client.db
        links = await db.get_links(interaction.user.id)
        if not links:
            await interaction.response.send_message(
                "You have no linked accounts. Use `/link create` first.", ephemeral=True
            )
            return
        eligible = [
            a
            for a in links
            if a["town_hall"] is None or repo.check_eligibility(roster, a["town_hall"], None) is None
        ]
        if not eligible:
            await interaction.response.send_message(
                "None of your linked accounts meet the requirements.", ephemeral=True
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
                "None of your accounts are on this roster.", ephemeral=True
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
