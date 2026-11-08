"""Player flags (bans/strikes) and the flag alert when a flagged player joins a family clan."""

from __future__ import annotations

from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands

from clashhero.cogs._base import BaseCog
from clashhero.events import MemberJoined
from clashhero.repos import flags as repo
from clashhero.services.outbox import send_server_log
from clashhero.utils.checks import manager
from clashhero.utils.fmt import chunk_lines, make_embed, player_link, ts
from clashhero.utils.resolve import ResolveError, parse_tag, player_autocomplete, resolve_clans

TYPE_CHOICES = [
    app_commands.Choice(name="Ban", value="ban"),
    app_commands.Choice(name="Strike", value="strike"),
]


def flag_line(r) -> str:
    icon = "⛔" if r["flag_type"] == "ban" else "⚠️"
    expiry = f" · expires {ts(r['expires_at'], 'R')}" if r["expires_at"] else ""
    reason = f" — {r['reason']}" if r["reason"] else ""
    return (
        f"`#{r['id']}` {icon} **{r['name'] or '?'}** `{r['player_tag']}`{reason} "
        f"· by <@{r['created_by']}> {ts(r['created_at'], 'd')}{expiry}"
    )


class Flags(BaseCog):
    flag = app_commands.Group(name="flag", description="Ban/strike flags for players", guild_only=True)

    @flag.command(name="create", description="Flag a player (Flags Manager)")
    @app_commands.choices(flag_type=TYPE_CHOICES)
    @app_commands.describe(expiry_days="Leave empty for a permanent flag")
    @app_commands.autocomplete(player=player_autocomplete)
    @manager("flags")
    async def create(
        self,
        interaction: discord.Interaction,
        flag_type: app_commands.Choice[str],
        player: str,
        reason: str | None = None,
        expiry_days: app_commands.Range[int, 1, 3650] | None = None,
    ) -> None:
        tag = parse_tag(player)
        flag_id = await repo.create_flag(
            self.db,
            interaction.guild_id,
            tag,
            flag_type.value,
            reason,
            interaction.user.id,
            datetime.now(UTC),
            expiry_days,
        )
        await interaction.response.send_message(
            f"Created {flag_type.name.lower()} flag `#{flag_id}` on `{tag}`.", ephemeral=True
        )

    @flag.command(name="delete", description="Delete a flag by id (Flags Manager)")
    @manager("flags")
    async def delete(self, interaction: discord.Interaction, flag_id: int) -> None:
        if not await repo.delete_flag(self.db, interaction.guild_id, flag_id):
            raise ResolveError(f"No flag `#{flag_id}` in this server.")
        await interaction.response.send_message(f"Deleted flag `#{flag_id}`.", ephemeral=True)

    @flag.command(name="list", description="List active flags")
    @app_commands.choices(flag_type=TYPE_CHOICES)
    @app_commands.describe(clans="Only players currently in these clans (tags/aliases)")
    @app_commands.autocomplete(player=player_autocomplete)
    async def list_(
        self,
        interaction: discord.Interaction,
        flag_type: app_commands.Choice[str] | None = None,
        player: str | None = None,
        clans: str | None = None,
        include_expired: bool = False,
    ) -> None:
        clan_tags = await resolve_clans(interaction, clans) if clans else None
        rows = await repo.list_flags(
            self.db,
            interaction.guild_id,
            datetime.now(UTC),
            flag_type=flag_type.value if flag_type else None,
            player_tag=parse_tag(player) if player else None,
            clan_tags=clan_tags,
            include_expired=include_expired,
        )
        chunks = chunk_lines([flag_line(r) for r in rows] or ["No flags."])
        embeds = []
        for i, c in enumerate(chunks[:10]):
            e = await make_embed(self.bot, interaction.guild_id, title="Flags" if i == 0 else None)
            e.description = c
            embeds.append(e)
        await interaction.response.send_message(embeds=embeds, ephemeral=True)

    @commands.Cog.listener()
    async def on_clash_member_join(self, event: MemberJoined) -> None:
        now = datetime.now(UTC)
        for guild_id in await self.db.guilds_for_clan(event.clan_tag):
            active = await repo.active_flags(self.db, guild_id, event.member.tag, now)
            if not active:
                continue
            embed = await make_embed(
                self.bot,
                guild_id,
                title="🚩 Flagged player joined",
                url=player_link(event.member.tag),
                description=f"**{event.member.name}** (`{event.member.tag}`, "
                f"TH{event.member.town_hall}) joined **{event.clan_name}** (`{event.clan_tag}`)",
                color=discord.Color.red(),
            )
            embed.add_field(name="Flags", value="\n".join(flag_line(r) for r in active)[:1024], inline=False)
            await send_server_log(self.bot, guild_id, "flag_alert", embed)


async def setup(bot) -> None:
    await bot.add_cog(Flags(bot))
