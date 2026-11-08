"""Player flags (bans / weighted strikes) and the flag alert when a flagged player joins a family clan."""

from __future__ import annotations

import logging
from collections.abc import Iterable
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

log = logging.getLogger(__name__)

TYPE_CHOICES = [
    app_commands.Choice(name="Ban", value="ban"),
    app_commands.Choice(name="Strike", value="strike"),
]


def flag_line(r) -> str:
    icon = "⛔" if r["flag_type"] == "ban" else "⚠️"
    strength = f" ×{r['weight']}" if r["flag_type"] == "strike" and r["weight"] != 1 else ""
    expiry = f" · expires {ts(r['expires_at'], 'R')}" if r["expires_at"] else ""
    reason = f" — {r['reason']}" if r["reason"] else ""
    return (
        f"`#{r['id']}` {icon}{strength} **{r['name'] or '?'}** `{r['player_tag']}`{reason} "
        f"· by <@{r['created_by']}> {ts(r['created_at'], 'd')}{expiry}"
    )


def summary_line(rank: int, r) -> str:
    ban = " ⛔ banned" if r["banned"] else ""
    plural = "s" if r["strikes"] != 1 else ""
    return (
        f"`{rank:>2}.` **{r['total']}** · {r['name'] or '?'} `{r['player_tag']}` · "
        f"{r['strikes']} strike{plural}, last {ts(r['last_strike'], 'd')}{ban}"
    )


def dm_embed(
    *,
    guild_name: str,
    player_name: str,
    player_tag: str,
    flag_type: str,
    reason: str | None,
    weight: int,
    expires_at: str | None,
    total: int | None = None,
) -> discord.Embed:
    """What a flagged player's linked Discord user receives."""
    if flag_type == "ban":
        title = f"You were banned from {guild_name}'s clans"
    else:
        title = f"You received a strike in {guild_name}"
    e = discord.Embed(
        title=title[:256],
        description=f"Account: **{player_name}** (`{player_tag}`)",
        color=discord.Color.red() if flag_type == "ban" else discord.Color.orange(),
    )
    e.add_field(name="Reason", value=(reason or "No reason given.")[:1024], inline=False)
    if flag_type == "strike":
        e.add_field(name="Weight", value=str(weight))
        if total is not None:
            e.add_field(name="Active strike total", value=str(total))
    e.add_field(name="Expires", value=ts(expires_at, "R") if expires_at else "Never")
    return e


async def dm_users(bot, user_ids: Iterable[int], embed: discord.Embed) -> tuple[list[int], list[int]]:
    """DM each user. Returns (delivered, failed) user ids; failures are logged, never raised."""
    sent, failed = [], []
    for uid in user_ids:
        try:
            user = bot.get_user(uid) or await bot.fetch_user(uid)
            await user.send(embed=embed)
            sent.append(uid)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as e:
            log.info("Could not DM flag notice to %s: %s", uid, e)
            failed.append(uid)
    return sent, failed


class Flags(BaseCog):
    flag = app_commands.Group(name="flag", description="Ban/strike flags for players", guild_only=True)

    @flag.command(name="create", description="Flag a player (Flags Manager)")
    @app_commands.choices(flag_type=TYPE_CHOICES)
    @app_commands.describe(
        expiry_days="Leave empty for a permanent flag",
        weight="Strike weight 1-10 (strikes only, default 1)",
        dm="DM the Discord user linked to this player with the reason",
    )
    @app_commands.autocomplete(player=player_autocomplete)
    @manager("flags")
    async def create(
        self,
        interaction: discord.Interaction,
        flag_type: app_commands.Choice[str],
        player: str,
        reason: str | None = None,
        expiry_days: app_commands.Range[int, 1, 3650] | None = None,
        weight: app_commands.Range[int, repo.MIN_WEIGHT, repo.MAX_WEIGHT] = 1,
        dm: bool = False,
    ) -> None:
        tag = parse_tag(player)
        if dm:
            await interaction.response.defer(ephemeral=True)
        now = datetime.now(UTC)
        weight = weight if flag_type.value == "strike" else 1
        flag_id = await repo.create_flag(
            self.db,
            interaction.guild_id,
            tag,
            flag_type.value,
            reason,
            interaction.user.id,
            now,
            expiry_days,
            weight,
        )
        msg = f"Created {flag_type.name.lower()} flag `#{flag_id}` on `{tag}`"
        total = None
        if flag_type.value == "strike":
            total = await repo.strike_total(self.db, interaction.guild_id, tag, now)
            msg += f" (weight {weight}; active strike total {total})"
        msg += "."
        if not dm:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        msg += "\n" + await self._notify(interaction, tag, flag_id, total)
        await interaction.followup.send(msg, ephemeral=True)

    async def _notify(
        self, interaction: discord.Interaction, tag: str, flag_id: int, total: int | None
    ) -> str:
        """DM the users linked to ``tag`` about flag ``flag_id``; returns a status line."""
        user_ids = sorted(set((await self.db.links_for_tags([tag])).values()))
        if not user_ids:
            return "No Discord account is linked to that player, so no DM was sent."
        flag = await self.db.fetchone("SELECT * FROM flags WHERE id = ?", (flag_id,))
        player = await self.db.get_player_row(tag)
        embed = dm_embed(
            guild_name=interaction.guild.name if interaction.guild else "this server",
            player_name=player["name"] if player else tag,
            player_tag=tag,
            flag_type=flag["flag_type"],
            reason=flag["reason"],
            weight=flag["weight"],
            expires_at=flag["expires_at"],
            total=total,
        )
        sent, failed = await dm_users(self.bot, user_ids, embed)
        parts = []
        if sent:
            parts.append("DM sent to " + ", ".join(f"<@{u}>" for u in sent) + ".")
        if failed:
            parts.append(
                "Couldn't DM " + ", ".join(f"<@{u}>" for u in failed) + " (DMs closed or user not found)."
            )
        return " ".join(parts)

    @flag.command(name="delete", description="Delete a flag by id (Flags Manager)")
    @manager("flags")
    async def delete(self, interaction: discord.Interaction, flag_id: int) -> None:
        if not await repo.delete_flag(self.db, interaction.guild_id, flag_id):
            raise ResolveError(f"No flag `#{flag_id}` in this server.")
        await interaction.response.send_message(f"Deleted flag `#{flag_id}`.", ephemeral=True)

    @flag.command(name="list", description="List active flags")
    @app_commands.choices(flag_type=TYPE_CHOICES)
    @app_commands.describe(
        clans="Only players currently in these clans (tags/aliases)",
        include_expired="Also show expired flags",
        min_strike_weight="Only players whose strike weight total is at least this",
    )
    @app_commands.autocomplete(player=player_autocomplete)
    async def list_(
        self,
        interaction: discord.Interaction,
        flag_type: app_commands.Choice[str] | None = None,
        player: str | None = None,
        clans: str | None = None,
        include_expired: bool = False,
        min_strike_weight: app_commands.Range[int, 1, 1000] | None = None,
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
            min_strike_weight=min_strike_weight,
        )
        chunks = chunk_lines([flag_line(r) for r in rows] or ["No flags."])
        embeds = []
        for i, c in enumerate(chunks[:10]):
            e = await make_embed(self.bot, interaction.guild_id, title="Flags" if i == 0 else None)
            e.description = c
            embeds.append(e)
        await interaction.response.send_message(embeds=embeds, ephemeral=True)

    @flag.command(name="summary", description="Players' active strike totals, highest first")
    @app_commands.describe(
        clans="Only players currently in these clans (tags/aliases)",
        min_strike_weight="Only players whose strike weight total is at least this",
    )
    async def summary(
        self,
        interaction: discord.Interaction,
        clans: str | None = None,
        min_strike_weight: app_commands.Range[int, 1, 1000] = 1,
    ) -> None:
        clan_tags = await resolve_clans(interaction, clans) if clans else None
        rows = await repo.strike_totals(
            self.db,
            interaction.guild_id,
            datetime.now(UTC),
            clan_tags=clan_tags,
            min_weight=min_strike_weight,
        )
        lines = [summary_line(i, r) for i, r in enumerate(rows, 1)] or ["No active strikes."]
        chunks = chunk_lines(lines)
        embeds = []
        for i, c in enumerate(chunks[:10]):
            e = await make_embed(self.bot, interaction.guild_id, title="Strike totals" if i == 0 else None)
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
            total = sum(r["weight"] for r in active if r["flag_type"] == "strike")
            if total:
                embed.add_field(name="Active strike total", value=str(total))
            await send_server_log(self.bot, guild_id, "flag_alert", embed)


async def setup(bot) -> None:
    await bot.add_cog(Flags(bot))
