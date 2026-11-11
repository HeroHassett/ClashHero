"""Rosters: CWL / war / esports / trophy sign-up lists with persistent
Signup / Opt-out / Refresh buttons, groups, eligibility rules, pings, CSV export, lineup
suggestions from war history and automatic pings before a roster starts or closes."""

from __future__ import annotations

import io
import logging
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import tasks

from clashhero.cogs import _roster_views as views
from clashhero.cogs._base import BaseCog, send_error
from clashhero.repos import roster_scoring as scoring
from clashhero.repos import rosters as repo
from clashhero.utils.checks import manager, require
from clashhero.utils.fmt import make_embed
from clashhero.utils.resolve import ResolveError, clan_autocomplete, player_autocomplete, resolve_clan
from clashhero.utils.style import SEP, chip, chip_width, fail_embed, ok_embed

log = logging.getLogger(__name__)

AUTOPING_INTERVAL_MINUTES = 5
DEFAULT_SUGGEST_SIZE = 15

CATEGORY_CHOICES = [
    app_commands.Choice(name=c.upper() if c != "esports" else "E-Sports", value=c) for c in repo.CATEGORIES
]
SORT_CHOICES = [app_commands.Choice(name=label, value=key) for key, label in repo.SORT_OPTIONS.items()]
PING_CHOICES = [
    app_commands.Choice(name="Unregistered (in clan, not signed up)", value="unregistered"),
    app_commands.Choice(name="Missing (signed up, not in clan)", value="missing"),
    app_commands.Choice(name="Everyone signed up", value="everyone"),
]


def parse_time(raw: str | None) -> str | None:
    """'YYYY-MM-DD HH:MM' (UTC) or a unix timestamp -> ISO string."""
    if not raw:
        return None
    raw = raw.strip()
    try:
        if raw.isdigit():
            return datetime.fromtimestamp(int(raw), UTC).isoformat()
        dt = datetime.fromisoformat(raw)
    except ValueError as e:
        raise ResolveError(
            f"Couldn't read time `{raw}`. Use `YYYY-MM-DD HH:MM` (UTC) or a unix timestamp."
        ) from e
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).isoformat()


def parse_color(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return int(raw.strip().lstrip("#"), 16)
    except ValueError as e:
        raise ResolveError(f"`{raw}` isn't a hex color like `#ff8800`.") from e


def roster_count(r) -> str:
    return f"{r['member_count']}/{r['max_members']}"


def roster_list_line(r, width: int | None = None) -> str:
    """One /roster list row: ``` `12/15` **CWL main** · 🏆 CWL · Clan · 🔔 · 🔒```."""
    parts = [
        f"{chip(roster_count(r), width)} **{r['name']}**",
        views.category_label(r["category"]),
        r["clan_name"] or r["clan_tag"],
    ]
    if repo.describe_autoping(r):
        parts.append("🔔")
    if r["closed"]:
        parts.append("🔒")
    return SEP.join(parts)


async def roster_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    rows = await repo.list_rosters(interaction.client.db, interaction.guild_id)
    cur = current.lower()
    out = []
    for r in rows:
        label = f"{r['name']} · {r['clan_name'] or r['clan_tag']} ({r['member_count']}/{r['max_members']})"
        if cur in label.lower():
            out.append(app_commands.Choice(name=label[:100], value=str(r["id"])))
    return out[:25]


async def group_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    raw = getattr(interaction.namespace, "roster", None) or getattr(interaction.namespace, "to_roster", None)
    if not raw:
        return []
    roster = await _lookup_roster(interaction, str(raw))
    if roster is None:
        return []
    groups = await repo.list_groups(interaction.client.db, roster["id"])
    return [
        app_commands.Choice(name=g["name"], value=g["name"])
        for g in groups
        if current.lower() in g["name"].lower()
    ][:25]


async def _lookup_roster(interaction: discord.Interaction, raw: str):
    db = interaction.client.db
    row = await repo.get_roster(db, int(raw)) if raw.isdigit() else None
    if row is None:
        row = await repo.get_roster_by_name(db, interaction.guild_id, raw)
    if row is None or row["guild_id"] != interaction.guild_id:
        return None
    return row


async def get_roster_or_fail(interaction: discord.Interaction, raw: str):
    row = await _lookup_roster(interaction, raw)
    if row is None:
        raise ResolveError(f"No roster named `{raw}` in this server.")
    return row


async def group_id_or_fail(interaction: discord.Interaction, roster, name: str | None) -> int | None:
    if not name:
        return None
    g = await repo.get_group_by_name(interaction.client.db, roster["id"], name)
    if g is None:
        raise ResolveError(f"Roster **{roster['name']}** has no group `{name}`.")
    return g["id"]


class Rosters(BaseCog):
    roster = app_commands.Group(
        name="roster", description="Sign-up rosters for CWL, wars and events", guild_only=True
    )
    manage = app_commands.Group(
        name="manage", description="Add, remove or move roster members", parent=roster
    )
    groups = app_commands.Group(
        name="groups", description="Roster groups (e.g. Confirmed, Substitute)", parent=roster
    )
    autoping = app_commands.Group(
        name="autoping",
        description="Automatically ping unregistered clan members before a roster starts or closes",
        parent=roster,
    )

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(*views.DYNAMIC_ITEMS)
        self.autoping_loop.start()

    async def cog_unload(self) -> None:
        self.autoping_loop.cancel()
        self.bot.remove_dynamic_items(*views.DYNAMIC_ITEMS)

    # ------------------------------------------------------------------ auto-ping loop

    @tasks.loop(minutes=AUTOPING_INTERVAL_MINUTES)
    async def autoping_loop(self) -> None:
        try:
            await self.run_autopings(datetime.now(UTC))
        except Exception:
            log.exception("Roster auto-ping cycle failed")

    @autoping_loop.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    async def run_autopings(self, now: datetime) -> int:
        """Send every auto-ping whose window is open (DB only). Each window is marked before
        sending so it fires once even if sending fails. Returns users pinged."""
        pinged = 0
        for roster in await repo.autoping_rosters(self.db):
            for kind, when in repo.autoping_due(roster, now):
                try:
                    await repo.mark_autoping_sent(self.db, roster["id"], kind, when)
                    pinged += await views.send_autoping(self.bot, roster, kind, when)
                except Exception:
                    log.exception("Auto-ping (%s) failed for roster %s", kind, roster["id"])
        return pinged

    # ------------------------------------------------------------------ lifecycle

    @roster.command(name="create", description="Create a roster")
    @app_commands.describe(
        clan="Clan tag or alias",
        max_members="Default 65 (use 15 or 30 for CWL)",
        min_heroes="Minimum combined hero levels",
        roster_role="Role given to people who sign up",
        start_time="'YYYY-MM-DD HH:MM' UTC or unix time",
        end_time="Signup deadline: 'YYYY-MM-DD HH:MM' UTC or unix time",
        allow_multi_signup="Allow being on more than one roster of this category",
        import_members="Sign up the clan's current eligible members",
        color="Hex color, e.g. #ff8800",
    )
    @app_commands.choices(category=CATEGORY_CHOICES, sort_by=SORT_CHOICES)
    @app_commands.autocomplete(clan=clan_autocomplete)
    @manager("roster")
    async def create(
        self,
        interaction: discord.Interaction,
        clan: str,
        name: app_commands.Range[str, 1, 60],
        category: app_commands.Choice[str] | None = None,
        max_members: app_commands.Range[int, 1, 65] = 65,
        min_th: app_commands.Range[int, 1, 20] | None = None,
        max_th: app_commands.Range[int, 1, 20] | None = None,
        min_heroes: app_commands.Range[int, 0, 500] | None = None,
        max_accounts_per_user: app_commands.Range[int, 1, 25] | None = None,
        allow_unlinked: bool = False,
        allow_multi_signup: bool = False,
        allow_group_selection: bool = True,
        roster_role: discord.Role | None = None,
        sort_by: app_commands.Choice[str] | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        color: str | None = None,
        import_members: bool = False,
    ) -> None:
        tag = await resolve_clan(interaction, clan)
        if await repo.get_roster_by_name(self.db, interaction.guild_id, name):
            raise ResolveError(f"A roster named **{name}** already exists.")
        rid = await repo.create_roster(
            self.db,
            interaction.guild_id,
            tag,
            name,
            category=category.value if category else "cwl",
            max_members=max_members,
            min_th=min_th,
            max_th=max_th,
            min_heroes=min_heroes,
            max_accounts_per_user=max_accounts_per_user,
            allow_unlinked=int(allow_unlinked),
            allow_multi_signup=int(allow_multi_signup),
            allow_group_selection=int(allow_group_selection),
            role_id=roster_role.id if roster_role else None,
            sort_by=sort_by.value if sort_by else "th_heroes",
            start_time=parse_time(start_time),
            end_time=parse_time(end_time),
            color=parse_color(color),
        )
        msg = f"Created roster **{name}** `#{rid}`. Post it with `/roster post`."
        if import_members:
            await interaction.response.defer(ephemeral=True, thinking=True)
            roster = await repo.get_roster(self.db, rid)
            tags = [r["player_tag"] for r in await self.db.clan_member_rows(tag)]
            res = await views.signup(
                self.bot, interaction.guild, roster, tags, actor=interaction.user, as_manager=True
            )
            msg += f"\nImported **{len(res.added)}** member(s){SEP}skipped **{len(res.errors)}**."
            await interaction.followup.send(embed=ok_embed(msg), ephemeral=True)
            return
        await interaction.response.send_message(embed=ok_embed(msg), ephemeral=True)

    @roster.command(name="edit", description="Change a roster's settings")
    @app_commands.choices(category=CATEGORY_CHOICES, sort_by=SORT_CHOICES)
    @app_commands.autocomplete(roster=roster_autocomplete, clan=clan_autocomplete)
    @app_commands.describe(
        remove_role="Stop giving a roster role",
        start_time="'YYYY-MM-DD HH:MM' UTC",
        end_time="'YYYY-MM-DD HH:MM' UTC",
    )
    @manager("roster")
    async def edit(
        self,
        interaction: discord.Interaction,
        roster: str,
        name: app_commands.Range[str, 1, 60] | None = None,
        clan: str | None = None,
        category: app_commands.Choice[str] | None = None,
        max_members: app_commands.Range[int, 1, 65] | None = None,
        min_th: app_commands.Range[int, 0, 20] | None = None,
        max_th: app_commands.Range[int, 0, 20] | None = None,
        min_heroes: app_commands.Range[int, 0, 500] | None = None,
        max_accounts_per_user: app_commands.Range[int, 0, 25] | None = None,
        allow_unlinked: bool | None = None,
        allow_multi_signup: bool | None = None,
        allow_group_selection: bool | None = None,
        roster_role: discord.Role | None = None,
        remove_role: bool = False,
        sort_by: app_commands.Choice[str] | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        color: str | None = None,
    ) -> None:
        row = await get_roster_or_fail(interaction, roster)
        fields: dict = {}
        if name:
            other = await repo.get_roster_by_name(self.db, interaction.guild_id, name)
            if other and other["id"] != row["id"]:
                raise ResolveError(f"A roster named **{name}** already exists.")
            fields["name"] = name
        if clan:
            fields["clan_tag"] = await resolve_clan(interaction, clan)
        if category:
            fields["category"] = category.value
        # 0 clears a numeric limit
        for key, val in (
            ("max_members", max_members),
            ("min_th", min_th),
            ("max_th", max_th),
            ("min_heroes", min_heroes),
            ("max_accounts_per_user", max_accounts_per_user),
        ):
            if val is not None:
                fields[key] = val or (65 if key == "max_members" else None)
        for key, val in (
            ("allow_unlinked", allow_unlinked),
            ("allow_multi_signup", allow_multi_signup),
            ("allow_group_selection", allow_group_selection),
        ):
            if val is not None:
                fields[key] = int(val)
        if roster_role:
            fields["role_id"] = roster_role.id
        if remove_role:
            fields["role_id"] = None
        if sort_by:
            fields["sort_by"] = sort_by.value
        if start_time:
            fields["start_time"] = parse_time(start_time)
        if end_time:
            fields["end_time"] = parse_time(end_time)
        if color:
            fields["color"] = parse_color(color)
        if not fields:
            await interaction.response.send_message(embed=fail_embed("Nothing to change."), ephemeral=True)
            return
        await repo.update_roster(self.db, row["id"], **fields)
        await views.refresh_post(self.bot, row["id"])
        await interaction.response.send_message(
            embed=ok_embed(f"Updated roster **{fields.get('name', row['name'])}**."), ephemeral=True
        )

    @roster.command(name="clone", description="Copy a roster's settings and groups (not members)")
    @app_commands.autocomplete(roster=roster_autocomplete)
    @manager("roster")
    async def clone(
        self, interaction: discord.Interaction, roster: str, new_name: app_commands.Range[str, 1, 60]
    ) -> None:
        row = await get_roster_or_fail(interaction, roster)
        if await repo.get_roster_by_name(self.db, interaction.guild_id, new_name):
            raise ResolveError(f"A roster named **{new_name}** already exists.")
        rid = await repo.clone_roster(self.db, row["id"], new_name)
        await interaction.response.send_message(
            embed=ok_embed(f"Cloned **{row['name']}** → **{new_name}** `#{rid}`."), ephemeral=True
        )

    @roster.command(name="delete", description="Delete a roster and its signups")
    @app_commands.autocomplete(roster=roster_autocomplete)
    @manager("roster")
    async def delete(self, interaction: discord.Interaction, roster: str) -> None:
        row = await get_roster_or_fail(interaction, roster)
        await repo.delete_roster(self.db, row["id"])
        await interaction.response.send_message(
            embed=ok_embed(f"Deleted roster **{row['name']}** and its signups."), ephemeral=True
        )

    # ------------------------------------------------------------------ viewing / posting

    @roster.command(name="post", description="Post a roster with Signup / Opt-out buttons (kept up to date)")
    @app_commands.autocomplete(roster=roster_autocomplete)
    @manager("roster")
    async def post(
        self, interaction: discord.Interaction, roster: str, channel: discord.TextChannel | None = None
    ) -> None:
        row = await get_roster_or_fail(interaction, roster)
        target = channel or interaction.channel
        msg = await target.send(
            embed=await views.build_roster_embed(self.bot, row),
            view=views.roster_view(row["id"], closed=bool(row["closed"])),
        )
        await repo.update_roster(self.db, row["id"], channel_id=target.id, message_id=msg.id)
        await interaction.response.send_message(
            embed=ok_embed(f"Posted **{row['name']}** in {target.mention}."), ephemeral=True
        )

    @roster.command(name="show", description="Show a roster")
    @app_commands.autocomplete(roster=roster_autocomplete)
    async def show(self, interaction: discord.Interaction, roster: str) -> None:
        row = await get_roster_or_fail(interaction, roster)
        await interaction.response.send_message(embed=await views.build_roster_embed(self.bot, row))

    @roster.command(name="list", description="List rosters in this server")
    @app_commands.choices(category=CATEGORY_CHOICES)
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def list_(
        self,
        interaction: discord.Interaction,
        clan: str | None = None,
        category: app_commands.Choice[str] | None = None,
    ) -> None:
        tag = await resolve_clan(interaction, clan) if clan else None
        rows = await repo.list_rosters(
            self.db, interaction.guild_id, tag, category.value if category else None
        )
        width = chip_width(roster_count(r) for r in rows)
        lines = [roster_list_line(r, width) for r in rows]
        embed = await make_embed(
            self.bot,
            interaction.guild_id,
            title="Rosters",
            description="\n".join(lines)[:4000] or "No rosters yet. Use `/roster create`.",
        )
        if rows:
            embed.set_footer(text="🔔 auto-ping on · 🔒 closed")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @roster.command(name="export", description="Download a roster as CSV")
    @app_commands.autocomplete(roster=roster_autocomplete)
    async def export(self, interaction: discord.Interaction, roster: str) -> None:
        row = await get_roster_or_fail(interaction, roster)
        members = repo.sort_members(await repo.list_members(self.db, row["id"]), row["sort_by"])
        data = repo.members_csv(members).encode()
        file = discord.File(io.BytesIO(data), filename=f"roster-{row['name'].replace(' ', '_')}.csv")
        await interaction.response.send_message(
            embed=ok_embed(f"Exported **{row['name']}**: **{len(members)}** member(s)."),
            file=file,
            ephemeral=True,
        )

    # ------------------------------------------------------------------ self service

    @roster.command(name="signup", description="Sign one of your linked accounts up for a roster")
    @app_commands.autocomplete(
        roster=roster_autocomplete, group=group_autocomplete, account=player_autocomplete
    )
    async def signup_cmd(
        self, interaction: discord.Interaction, roster: str, account: str, group: str | None = None
    ) -> None:
        row = await get_roster_or_fail(interaction, roster)
        gid = await group_id_or_fail(interaction, row, group) if row["allow_group_selection"] else None
        await interaction.response.defer(ephemeral=True, thinking=True)
        res = await views.signup(
            self.bot, interaction.guild, row, [self.parse_tag(account)], actor=interaction.user, group_id=gid
        )
        await interaction.followup.send(embed=res.embed(), ephemeral=True)

    @roster.command(name="optout", description="Remove one of your accounts from a roster")
    @app_commands.autocomplete(roster=roster_autocomplete, account=player_autocomplete)
    async def optout_cmd(self, interaction: discord.Interaction, roster: str, account: str) -> None:
        row = await get_roster_or_fail(interaction, roster)
        tag = self.parse_tag(account)
        member = await repo.get_member(self.db, row["id"], tag)
        mine = tag in await self.db.get_linked_tags(interaction.user.id)
        if member is None:
            raise ResolveError(f"`{tag}` isn't on **{row['name']}**.")
        if (
            not mine
            and member["user_id"] != interaction.user.id
            and not await views.is_roster_manager(interaction)
        ):
            raise ResolveError("That account isn't yours.")
        removed = await views.opt_out(self.bot, interaction.guild, row, [tag], actor=interaction.user)
        await interaction.response.send_message(
            embed=views.removed_embed(removed, row["name"]), ephemeral=True
        )

    # ------------------------------------------------------------------ manager actions

    @roster.command(name="close", description="Lock a roster (no self signups)")
    @app_commands.autocomplete(roster=roster_autocomplete)
    @manager("roster")
    async def close(self, interaction: discord.Interaction, roster: str) -> None:
        await self._set_closed(interaction, roster, True)

    @roster.command(name="open", description="Unlock a roster")
    @app_commands.autocomplete(roster=roster_autocomplete)
    @manager("roster")
    async def open_(self, interaction: discord.Interaction, roster: str) -> None:
        await self._set_closed(interaction, roster, False)

    async def _set_closed(self, interaction: discord.Interaction, raw: str, closed: bool) -> None:
        row = await get_roster_or_fail(interaction, raw)
        await repo.update_roster(self.db, row["id"], closed=int(closed))
        await views.refresh_post(self.bot, row["id"])
        state = "closed 🔒 (no self signups)" if closed else "open 🔓 for signups"
        await interaction.response.send_message(
            embed=ok_embed(f"**{row['name']}** is now {state}."), ephemeral=True
        )

    @roster.command(name="clear", description="Remove every signup from a roster")
    @app_commands.autocomplete(roster=roster_autocomplete)
    @manager("roster")
    async def clear(self, interaction: discord.Interaction, roster: str) -> None:
        row = await get_roster_or_fail(interaction, roster)
        members = await repo.list_members(self.db, row["id"])
        removed = await views.opt_out(
            self.bot, interaction.guild, row, [m["player_tag"] for m in members], actor=interaction.user
        )
        await interaction.response.send_message(
            embed=ok_embed(f"Cleared **{len(removed)}** signup(s) from **{row['name']}**."), ephemeral=True
        )

    @roster.command(name="ping", description="Ping roster members (asks for confirmation)")
    @app_commands.choices(ping_option=PING_CHOICES)
    @app_commands.autocomplete(roster=roster_autocomplete, group=group_autocomplete)
    @app_commands.describe(group="Ping everyone in this group (overrides ping_option)")
    @manager("roster")
    async def ping(
        self,
        interaction: discord.Interaction,
        roster: str,
        ping_option: app_commands.Choice[str] | None = None,
        group: str | None = None,
        message: str | None = None,
    ) -> None:
        row = await get_roster_or_fail(interaction, roster)
        gid = await group_id_or_fail(interaction, row, group)
        members = await repo.list_members(self.db, row["id"])
        clan_tags = [r["player_tag"] for r in await self.db.clan_member_rows(row["clan_tag"])]
        links = await self.db.links_for_tags(set(clan_tags) | {m["player_tag"] for m in members})
        option = ping_option.value if ping_option else "everyone"
        users = repo.compute_ping_targets(option, members, clan_tags, links, gid)
        if not users:
            await interaction.response.send_message(embed=fail_embed("Nobody to ping."), ephemeral=True)
            return
        label = f"group **{group}**" if gid else f"**{option}**"
        view = _ConfirmPing(row["name"], users, message)
        await interaction.response.send_message(
            f"Ping {len(users)} user(s) ({label}) for **{row['name']}**?", view=view, ephemeral=True
        )

    # ------------------------------------------------------------------ suggestions

    @roster.command(
        name="suggest", description="Rank eligible players by war history, activity and TH/heroes"
    )
    @app_commands.autocomplete(roster=roster_autocomplete)
    @app_commands.describe(
        size=f"How many to confirm (default {DEFAULT_SUGGEST_SIZE}, capped at the roster size)",
        substitutes="How many substitutes after the top picks (default 5)",
        cwl_seasons="How many recent CWL seasons of history to use (default 3)",
        apply="Put the picks into Confirmed / Substitute (roster managers; asks first)",
    )
    async def suggest(
        self,
        interaction: discord.Interaction,
        roster: str,
        size: app_commands.Range[int, 1, 65] | None = None,
        substitutes: app_commands.Range[int, 0, 20] = 5,
        cwl_seasons: app_commands.Range[int, 1, 12] = scoring.DEFAULT_CWL_SEASONS,
        apply: bool = False,
    ) -> None:
        row = await get_roster_or_fail(interaction, roster)
        if apply:
            await require(interaction, "roster")
        await interaction.response.defer(ephemeral=True, thinking=True)
        now = datetime.now(UTC)
        rows = await repo.suggestion_candidate_rows(self.db, row)
        candidates = [scoring.Candidate.from_row(r) for r in rows]
        links = await self.db.links_for_tags([c.tag for c in candidates])
        others = (
            {}
            if row["allow_multi_signup"]
            else await repo.tags_on_other_rosters(self.db, row["guild_id"], row["category"], row["id"])
        )
        eligible, excluded = scoring.split_eligible(row, candidates, linked=links, other_rosters=others)
        histories, seasons = await scoring.load_histories(
            self.db, [c.tag for c in eligible], cwl_seasons=cwl_seasons, now=now
        )
        ranked = scoring.score_candidates(eligible, histories, now)
        size = min(size or DEFAULT_SUGGEST_SIZE, row["max_members"])
        substitutes = max(0, min(substitutes, row["max_members"] - size))
        color = (await make_embed(self.bot, interaction.guild_id)).color
        embeds = views.render_suggestion_embeds(
            row, ranked, excluded, size=size, substitutes=substitutes, seasons=seasons, color=color
        )
        if not apply or not ranked:
            await interaction.followup.send(embeds=embeds, ephemeral=True)
            return
        confirmed, subs = scoring.pick(ranked, size, substitutes)
        new = sum(not s.candidate.signed_up for s in confirmed + subs)
        free = row["max_members"] - await repo.member_count(self.db, row["id"])
        text = (
            f"Apply this lineup to **{row['name']}**? {len(confirmed)} → Confirmed, "
            f"{len(subs)} → Substitute; other signups in Confirmed move to Substitute (nobody is removed)."
        )
        if new > free:
            text += (
                f"\n⚠️ {new} pick(s) aren't signed up but only {max(free, 0)} slot(s) are free; the rest "
                "will fail as full. Remove signups or raise max_members first."
            )
        view = _ConfirmSuggest(
            row["id"], [s.candidate.tag for s in confirmed], [s.candidate.tag for s in subs]
        )
        await interaction.followup.send(text, embeds=embeds, view=view, ephemeral=True)

    # ------------------------------------------------------------------ auto-ping

    @autoping.command(
        name="set", description="Ping unregistered clan members before a roster starts or closes"
    )
    @app_commands.autocomplete(roster=roster_autocomplete)
    @app_commands.describe(
        hours_before_end="Ping this many hours before signups close (the roster's end time)",
        hours_before_start="Ping this many hours before the roster's start time",
        message="Text to send with the ping",
        channel="Where to ping (default: where the roster is posted, else here)",
    )
    @manager("roster")
    async def autoping_set(
        self,
        interaction: discord.Interaction,
        roster: str,
        hours_before_end: app_commands.Range[float, 0.25, 336.0] | None = None,
        hours_before_start: app_commands.Range[float, 0.25, 336.0] | None = None,
        message: app_commands.Range[str, 1, 300] | None = None,
        channel: discord.TextChannel | None = None,
    ) -> None:
        row = await get_roster_or_fail(interaction, roster)
        if hours_before_end is None and hours_before_start is None:
            raise ResolveError("Give `hours_before_end` and/or `hours_before_start`.")
        channel_id = channel.id if channel else (row["channel_id"] or interaction.channel_id)
        await repo.set_autoping(
            self.db,
            row["id"],
            start_hours=hours_before_start,
            end_hours=hours_before_end,
            message=message,
            channel_id=channel_id,
        )
        await views.refresh_post(self.bot, row["id"])
        updated = await repo.get_roster(self.db, row["id"])
        lines = [f"**{row['name']}**: {repo.describe_autoping(updated)}, in <#{channel_id}>."]
        if hours_before_end and not row["end_time"]:
            lines.append("⚠️ This roster has no end time yet; set one with `/roster edit end_time`.")
        if hours_before_start and not row["start_time"]:
            lines.append("⚠️ This roster has no start time yet; set one with `/roster edit start_time`.")
        if row["closed"]:
            lines.append("🔒 The roster is closed, so nothing is sent until it's opened again.")
        elif repo.autoping_due(updated, datetime.now(UTC)):
            lines.append(
                f"🔔 That window is already open: the first ping goes out within "
                f"{AUTOPING_INTERVAL_MINUTES} minutes."
            )
        await interaction.response.send_message(embed=ok_embed("\n".join(lines)), ephemeral=True)

    @autoping.command(name="off", description="Stop automatic pings for a roster")
    @app_commands.autocomplete(roster=roster_autocomplete)
    @manager("roster")
    async def autoping_off(self, interaction: discord.Interaction, roster: str) -> None:
        row = await get_roster_or_fail(interaction, roster)
        await repo.set_autoping(
            self.db, row["id"], start_hours=None, end_hours=None, message=None, channel_id=None
        )
        await views.refresh_post(self.bot, row["id"])
        await interaction.response.send_message(
            embed=ok_embed(f"Auto-ping is off for **{row['name']}**."), ephemeral=True
        )

    @manage.command(
        name="add", description="Add players to a roster (tags in any format, or a user's accounts)"
    )
    @app_commands.autocomplete(roster=roster_autocomplete, group=group_autocomplete)
    @app_commands.describe(
        tags="Player tags separated by spaces/commas/newlines", user="Add all of this user's accounts"
    )
    @manager("roster")
    async def manage_add(
        self,
        interaction: discord.Interaction,
        roster: str,
        tags: str | None = None,
        user: discord.Member | None = None,
        group: str | None = None,
    ) -> None:
        row = await get_roster_or_fail(interaction, roster)
        gid = await group_id_or_fail(interaction, row, group)
        found = repo.parse_tags(tags or "")
        if user:
            found += [t for t in await self.db.get_linked_tags(user.id) if t not in found]
        if not found:
            raise ResolveError("Give player tags or a user with linked accounts.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        res = await views.signup(
            self.bot, interaction.guild, row, found, actor=interaction.user, group_id=gid, as_manager=True
        )
        await interaction.followup.send(embed=res.embed(), ephemeral=True)

    @manage.command(name="remove", description="Remove players from a roster")
    @app_commands.autocomplete(roster=roster_autocomplete)
    @manager("roster")
    async def manage_remove(
        self,
        interaction: discord.Interaction,
        roster: str,
        tags: str | None = None,
        user: discord.Member | None = None,
    ) -> None:
        row = await get_roster_or_fail(interaction, roster)
        found = repo.parse_tags(tags or "")
        if user:
            found += [
                m["player_tag"]
                for m in await repo.list_members(self.db, row["id"])
                if m["user_id"] == user.id
            ]
        removed = await views.opt_out(self.bot, interaction.guild, row, found, actor=interaction.user)
        await interaction.response.send_message(
            embed=views.removed_embed(removed, row["name"]), ephemeral=True
        )

    @manage.command(name="move", description="Move players to another roster and/or group")
    @app_commands.autocomplete(
        roster=roster_autocomplete, to_roster=roster_autocomplete, group=group_autocomplete
    )
    @app_commands.describe(to_roster="Destination roster (default: same roster)", group="Destination group")
    @manager("roster")
    async def manage_move(
        self,
        interaction: discord.Interaction,
        roster: str,
        tags: str,
        to_roster: str | None = None,
        group: str | None = None,
    ) -> None:
        src = await get_roster_or_fail(interaction, roster)
        dst = await get_roster_or_fail(interaction, to_roster) if to_roster else src
        gid = await group_id_or_fail(interaction, dst, group) or await repo.default_group_id(
            self.db, dst["id"]
        )
        moved, missing = [], []
        for tag in repo.parse_tags(tags):
            if dst["id"] != src["id"] and await repo.get_member(self.db, dst["id"], tag) is None:
                if await repo.member_count(self.db, dst["id"]) >= dst["max_members"]:
                    missing.append(f"{tag} (destination full)")
                    continue
            ok = await repo.move_member(self.db, src["id"], tag, to_roster_id=dst["id"], group_id=gid)
            (moved if ok else missing).append(tag)
        for rid in {src["id"], dst["id"]}:
            await views.refresh_post(self.bot, rid)
        if moved:
            await views._log_change(self.bot, interaction.guild, dst, interaction.user, "moved", moved)
        await interaction.response.send_message(embed=move_embed(moved, missing, dst["name"]), ephemeral=True)

    # ------------------------------------------------------------------ groups

    @groups.command(name="create", description="Add a group to a roster")
    @app_commands.autocomplete(roster=roster_autocomplete)
    @manager("roster")
    async def group_create(
        self, interaction: discord.Interaction, roster: str, name: app_commands.Range[str, 1, 50]
    ) -> None:
        row = await get_roster_or_fail(interaction, roster)
        if await repo.get_group_by_name(self.db, row["id"], name):
            raise ResolveError(f"Group `{name}` already exists.")
        await repo.create_group(self.db, row["id"], name)
        await views.refresh_post(self.bot, row["id"])
        await interaction.response.send_message(
            embed=ok_embed(f"Added group **{name}** to **{row['name']}**."), ephemeral=True
        )

    @groups.command(name="rename", description="Rename a roster group")
    @app_commands.autocomplete(roster=roster_autocomplete, group=group_autocomplete)
    @manager("roster")
    async def group_rename(
        self,
        interaction: discord.Interaction,
        roster: str,
        group: str,
        new_name: app_commands.Range[str, 1, 50],
    ) -> None:
        row = await get_roster_or_fail(interaction, roster)
        gid = await group_id_or_fail(interaction, row, group)
        await repo.rename_group(self.db, gid, new_name)
        await views.refresh_post(self.bot, row["id"])
        await interaction.response.send_message(
            embed=ok_embed(f"Renamed group **{group}** → **{new_name}**."), ephemeral=True
        )

    @groups.command(name="delete", description="Delete a roster group (its members become ungrouped)")
    @app_commands.autocomplete(roster=roster_autocomplete, group=group_autocomplete)
    @manager("roster")
    async def group_delete(self, interaction: discord.Interaction, roster: str, group: str) -> None:
        row = await get_roster_or_fail(interaction, roster)
        gid = await group_id_or_fail(interaction, row, group)
        await repo.delete_group(self.db, gid)
        await views.refresh_post(self.bot, row["id"])
        await interaction.response.send_message(
            embed=ok_embed(f"Deleted group **{group}**. Its members are now ungrouped."), ephemeral=True
        )

    @groups.command(name="list", description="List a roster's groups")
    @app_commands.autocomplete(roster=roster_autocomplete)
    async def group_list(self, interaction: discord.Interaction, roster: str) -> None:
        row = await get_roster_or_fail(interaction, roster)
        groups = await repo.list_groups(self.db, row["id"])
        embed = await make_embed(
            self.bot,
            interaction.guild_id,
            title=f"Groups · {row['name']}",
            description=SEP.join(f"**{g['name']}**" for g in groups)[:4000]
            or "No groups. Add one with `/roster groups create`.",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


def move_embed(moved: list[str], missing: list[str], roster_name: str) -> discord.Embed:
    """Result of /roster manage move: green if anyone moved, with a "Not moved" field."""
    if not moved:
        e = fail_embed(f"Nobody was moved to **{roster_name}**.")
    else:
        e = ok_embed(f"Moved **{len(moved)}** to **{roster_name}**.")
    if missing:
        e.add_field(name=f"Not moved ({len(missing)})", value=views.clip_lines(missing), inline=False)
    return e


class _ConfirmPing(discord.ui.View):
    def __init__(self, roster_name: str, users: set[int], message: str | None) -> None:
        super().__init__(timeout=120)
        self.roster_name = roster_name
        self.users = sorted(users)
        self.message = message

    @discord.ui.button(label="Send ping", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        header = f"**{self.roster_name}**" + (f": {self.message}" if self.message else "")
        chunks = views.ping_chunks(header, self.users)
        await interaction.response.edit_message(
            content=None,
            embed=ok_embed(f"Pinging {len(self.users)} user(s) for **{self.roster_name}**."),
            view=None,
        )
        for chunk in chunks:
            await interaction.channel.send(chunk, allowed_mentions=discord.AllowedMentions(users=True))
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Cancelled.", view=None)
        self.stop()


class _ConfirmSuggest(discord.ui.View):
    """Apply / Cancel for ``/roster suggest apply:True`` (ephemeral, so only the invoker sees it)."""

    def __init__(self, roster_id: int, confirmed: list[str], substitutes: list[str]) -> None:
        super().__init__(timeout=300)
        self.roster_id = roster_id
        self.confirmed = confirmed
        self.substitutes = substitutes

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await send_error(interaction, error)

    @discord.ui.button(label="Apply lineup", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await require(interaction, "roster")
        roster = await repo.get_roster(interaction.client.db, self.roster_id)
        if roster is None:
            await interaction.response.edit_message(
                content=None, embed=fail_embed("That roster no longer exists."), view=None
            )
            return
        await interaction.response.edit_message(content=f"Applying to **{roster['name']}**…", view=None)
        self.stop()
        summary = await views.apply_suggestion(
            interaction.client,
            interaction.guild,
            roster,
            self.confirmed,
            self.substitutes,
            actor=interaction.user,
        )
        await interaction.followup.send(embed=views.apply_embed(roster["name"], summary), ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Cancelled.", view=None)
        self.stop()


async def setup(bot) -> None:
    await bot.add_cog(Rosters(bot))
