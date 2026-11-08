"""Reminders for war, CWL, raid weekend and Clan Games."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import tasks

from clashhero.cogs._base import BaseCog
from clashhero.repos import reminders as R
from clashhero.services.outbox import send_to_channel
from clashhero.utils.checks import manager
from clashhero.utils.fmt import make_embed
from clashhero.utils.resolve import ResolveError, clan_autocomplete, resolve_clans
from clashhero.utils.seasons import clan_games

log = logging.getLogger(__name__)

TYPE_CHOICES = [
    app_commands.Choice(name="Clan War", value="war"),
    app_commands.Choice(name="Clan War League", value="cwl"),
    app_commands.Choice(name="Capital Raid Weekend", value="raid"),
    app_commands.Choice(name="Clan Games", value="games"),
]
TYPE_LABEL = {c.value: c.name for c in TYPE_CHOICES}
MODE_CHOICES = [
    app_commands.Choice(name="Opt-in: only ping members with the role", value="optin"),
    app_commands.Choice(name="Opt-out: don't ping members with the role", value="optout"),
]


def _dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    d = datetime.fromisoformat(iso)
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def build_messages(
    message: str,
    sections: list[tuple[str, list[R.Pending]]],
    links: dict[str, int],
    include_list: bool,
    allowed_user: callable = lambda uid: True,
    limit: int = 1900,
) -> list[str]:
    """Render reminder text. Each section is (clan name, pending players). Linked players are
    mentioned (grouped per Discord user); unlinked ones are listed by name."""
    head = message
    if not include_list:
        return [head]
    body: list[str] = []
    for clan_name, pending in sections:
        if not pending:
            continue
        body.append(f"\n**{clan_name}**: {len(pending)} remaining")
        by_user: dict[int, list[str]] = {}
        unlinked: list[str] = []
        for p in pending:
            uid = links.get(p.player_tag)
            if uid is not None and allowed_user(uid):
                by_user.setdefault(uid, []).append(f"{p.name} {p.detail}")
            elif uid is None:
                unlinked.append(f"{p.name} {p.detail}")
        for uid, accts in by_user.items():
            body.append(f"<@{uid}> " + ", ".join(accts))
        if unlinked:
            body.append("Not linked: " + ", ".join(unlinked))
    if not body:
        return []
    out, cur = [], head
    for line in body:
        if len(cur) + len(line) + 1 > limit:
            out.append(cur)
            cur = ""
        cur += line + "\n"
    out.append(cur)
    return out


class Reminders(BaseCog):
    reminders = app_commands.Group(name="reminders", description="Attack reminders", guild_only=True)

    async def cog_load(self) -> None:
        self.reminder_loop.start()

    async def cog_unload(self) -> None:
        self.reminder_loop.cancel()

    # ------------------------------------------------------------------ core

    async def _clan_name(self, clan_tag: str) -> str:
        row = await self.db.get_clan_row(clan_tag)
        return row["name"] if row else clan_tag

    async def _active_event(self, type_: str, clan_tag: str, now: datetime):
        """(event_key, end_time, pending list) for the clan's active event, or None."""
        if type_ in ("war", "cwl"):
            war = await R.active_war(self.db, clan_tag, cwl=type_ == "cwl")
            if war is None or not war["end_time"]:
                return None
            return f"war:{war['id']}", _dt(war["end_time"]), await R.war_pending(self.db, war)
        if type_ == "raid":
            raid = await R.active_raid(self.db, clan_tag)
            if raid is None:
                return None
            return (
                f"raid:{clan_tag}:{raid['start_time']}",
                _dt(raid["end_time"]),
                await R.raid_pending(self.db, clan_tag, raid["start_time"]),
            )
        if type_ == "games":
            window = clan_games(now)
            if not window.contains(now):
                return None
            return (
                f"games:{window.id}:{clan_tag}",
                window.end,
                await R.games_pending(self.db, clan_tag, window.id),
            )
        return None

    async def _role_filter(self, guild_id: int, type_: str):
        cfg = await R.get_config(self.db, guild_id, type_)
        guild = self.bot.get_guild(guild_id)
        if cfg is None or guild is None:
            return lambda uid: True
        role_id, optin = cfg["role_id"], cfg["mode"] == "optin"

        def allowed(uid: int) -> bool:
            member = guild.get_member(uid)
            if member is None:
                return not optin
            has = any(r.id == role_id for r in member.roles)
            return has if optin else not has

        return allowed

    async def _send(
        self,
        guild_id: int,
        type_: str,
        channel_id: int,
        message: str,
        sections: list[tuple[str, list[R.Pending]]],
        include_list: bool,
    ) -> bool:
        tags = [p.player_tag for _, pending in sections for p in pending]
        links = await self.db.links_for_tags(tags)
        msgs = build_messages(
            message, sections, links, include_list, await self._role_filter(guild_id, type_)
        )
        for text in msgs:
            await send_to_channel(self.bot, channel_id, content=text)
        return bool(msgs)

    @tasks.loop(minutes=1)
    async def reminder_loop(self) -> None:
        now = datetime.now(UTC)
        for rem in await R.all_reminders(self.db):
            for clan_tag in rem["clans"].split():
                try:
                    event = await self._active_event(rem["type"], clan_tag, now)
                    if event is None:
                        continue
                    key, end, pending = event
                    if end is None:
                        continue
                    key = f"{key}"
                    sent = await R.was_sent(self.db, rem["id"], key)
                    if not R.is_due(now, end, rem["duration_seconds"], sent):
                        continue
                    await R.mark_sent(self.db, rem["id"], key)
                    if not pending and not rem["exclude_participant_list"]:
                        continue
                    await self._send(
                        rem["guild_id"],
                        rem["type"],
                        rem["channel_id"],
                        rem["message"],
                        [(await self._clan_name(clan_tag), pending)],
                        include_list=not rem["exclude_participant_list"],
                    )
                except Exception:
                    log.exception("Reminder %s failed for %s", rem["id"], clan_tag)

    @reminder_loop.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------ commands

    @staticmethod
    def _duration(type_: str, raw: str) -> int:
        try:
            d = R.parse_duration(raw)
        except ValueError as e:
            raise ResolveError(f"`{raw}` isn't a duration. Use e.g. `2h`, `1h30m`, `1d`.") from e
        if d < R.MIN_DURATION:
            raise ResolveError("Reminders must be at least 15 minutes before the end.")
        if d > R.MAX_DURATION[type_]:
            raise ResolveError(f"That's longer than a {TYPE_LABEL[type_]} lasts.")
        return int(d.total_seconds())

    @reminders.command(name="create", description="Ping members who still have attacks before an event ends")
    @app_commands.describe(
        type="What to remind about",
        duration="Time left when the reminder fires, e.g. 2h, 1h30m, 1d",
        clans="Clan tags/aliases (space separated); empty = all clans",
        message="Message to send",
        channel="Channel to post in (default: here)",
        exclude_participant_list="Only send the message, without pinging members",
    )
    @app_commands.choices(type=TYPE_CHOICES)
    @manager("bot")
    async def create(
        self,
        interaction: discord.Interaction,
        type: app_commands.Choice[str],
        duration: str,
        message: str,
        clans: str | None = None,
        channel: discord.TextChannel | None = None,
        exclude_participant_list: bool = False,
    ) -> None:
        seconds = self._duration(type.value, duration)
        tags = await resolve_clans(interaction, clans)
        target = channel or interaction.channel
        rid = await R.create_reminder(
            self.db,
            interaction.guild_id,
            type.value,
            tags,
            seconds,
            message,
            target.id,
            exclude_participant_list,
            interaction.user.id,
        )
        await interaction.response.send_message(
            f"Reminder **#{rid}** created: {type.name}, {R.format_duration(seconds)} before the end, "
            f"in {target.mention} for {len(tags)} clan(s).",
            ephemeral=True,
        )

    @reminders.command(name="list", description="List reminders")
    @app_commands.choices(type=TYPE_CHOICES)
    async def list_(
        self, interaction: discord.Interaction, type: app_commands.Choice[str] | None = None
    ) -> None:
        rows = await R.list_reminders(self.db, interaction.guild_id, type.value if type else None)
        lines = [
            f"**#{r['id']}** {TYPE_LABEL.get(r['type'], r['type'])} · "
            f"{R.format_duration(r['duration_seconds'])} "
            f"before end · <#{r['channel_id']}> · {r['clans']}\n> {r['message'][:80]}"
            for r in rows
        ]
        embed = await make_embed(
            self.bot,
            interaction.guild_id,
            title="Reminders",
            description="\n".join(lines)[:4000] or "No reminders. Use `/reminders create`.",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @reminders.command(name="edit", description="Edit a reminder")
    @manager("bot")
    async def edit(
        self,
        interaction: discord.Interaction,
        id: int,
        duration: str | None = None,
        message: str | None = None,
        channel: discord.TextChannel | None = None,
        clans: str | None = None,
    ) -> None:
        rem = await R.get_reminder(self.db, interaction.guild_id, id)
        if rem is None:
            raise ResolveError(f"No reminder #{id}.")
        ok = await R.update_reminder(
            self.db,
            interaction.guild_id,
            id,
            duration_seconds=self._duration(rem["type"], duration) if duration else None,
            message=message,
            channel_id=channel.id if channel else None,
            clans=" ".join(await resolve_clans(interaction, clans)) if clans else None,
        )
        await interaction.response.send_message(
            f"Reminder #{id} updated." if ok else "Nothing to change.", ephemeral=True
        )

    @reminders.command(name="delete", description="Delete a reminder")
    @manager("bot")
    async def delete(self, interaction: discord.Interaction, id: int) -> None:
        ok = await R.delete_reminder(self.db, interaction.guild_id, id)
        await interaction.response.send_message(
            f"Deleted reminder #{id}." if ok else f"No reminder #{id}.", ephemeral=True
        )

    @reminders.command(name="now", description="Ping members with remaining attacks right now")
    @app_commands.choices(type=TYPE_CHOICES)
    @app_commands.autocomplete(clans=clan_autocomplete)
    @manager("bot")
    async def now(
        self,
        interaction: discord.Interaction,
        type: app_commands.Choice[str],
        message: str = "Reminder: please use your attacks!",
        clans: str | None = None,
    ) -> None:
        tags = await resolve_clans(interaction, clans)
        await interaction.response.defer()
        now = datetime.now(UTC)
        sections = []
        for tag in tags:
            event = await self._active_event(type.value, tag, now)
            if event:
                sections.append((await self._clan_name(tag), event[2]))
        if not sections:
            await interaction.followup.send(f"No active {type.name} for those clans.")
            return
        links = await self.db.links_for_tags([p.player_tag for _, ps in sections for p in ps])
        msgs = build_messages(
            message, sections, links, True, await self._role_filter(interaction.guild_id, type.value)
        )
        if not msgs:
            await interaction.followup.send("Everyone is done. 🎉")
            return
        for text in msgs:
            await interaction.followup.send(text, allowed_mentions=discord.AllowedMentions(users=True))

    @reminders.command(name="config", description="Limit who gets pinged with a role")
    @app_commands.choices(type=TYPE_CHOICES, mode=MODE_CHOICES)
    @manager("bot")
    async def config(
        self,
        interaction: discord.Interaction,
        type: app_commands.Choice[str],
        role: discord.Role | None = None,
        mode: app_commands.Choice[str] | None = None,
    ) -> None:
        if role is None:
            await R.set_config(self.db, interaction.guild_id, type.value, None, "optin")
            await interaction.response.send_message(f"{type.name} reminders ping everyone.", ephemeral=True)
            return
        m = mode.value if mode else "optin"
        await R.set_config(self.db, interaction.guild_id, type.value, role.id, m)
        verb = "only members with" if m == "optin" else "everyone except members with"
        await interaction.response.send_message(
            f"{type.name} reminders will ping {verb} {role.mention}.", ephemeral=True
        )


async def setup(bot) -> None:
    await bot.add_cog(Reminders(bot))
