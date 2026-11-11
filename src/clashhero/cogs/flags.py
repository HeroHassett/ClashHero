"""Player flags (bans / weighted strikes) and the flag alert when a flagged player joins a family clan."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import escape_markdown

from clashhero.cogs._base import BaseCog
from clashhero.cogs._log_embeds import clan_author, player_embed, player_ref
from clashhero.events import MemberJoined
from clashhero.repos import flags as repo
from clashhero.services.emojis import EMOJIS
from clashhero.services.outbox import send_dm, send_server_log
from clashhero.utils.checks import manager
from clashhero.utils.fmt import chunk_lines, make_embed, player_link, ts
from clashhero.utils.resolve import ResolveError, parse_tag, player_autocomplete, resolve_clans
from clashhero.utils.style import SEP, chip, chip_width, ok_embed

log = logging.getLogger(__name__)

TYPE_CHOICES = [
    app_commands.Choice(name="Ban", value="ban"),
    app_commands.Choice(name="Strike", value="strike"),
]


FLAG_ICONS = {"ban": "⛔", "strike": "⚠️"}
# One message holds 6000 characters over all its embeds; lists stop a little short of that.
MESSAGE_BUDGET = 5600


def player_label(name: str | None, tag: str) -> str:
    """The player's name linked to their in-game profile, then their tag (just the linked tag
    when the bot doesn't know the name)."""
    return f"{player_ref(name, tag)} `{tag}`" if name and name != tag else f"[{tag}]({player_link(tag)})"


def flag_line(r, *, player: bool = True) -> str:
    """One flag: its id, ban or strike (with the strike's weight) and, with ``player``, who it's
    on; then the reason, and who added it, when, and when it expires."""
    ban = r["flag_type"] == "ban"
    kind = "Ban" if ban else "Strike" + (f" ×{r['weight']}" if r["weight"] != 1 else "")
    head = f"`#{r['id']}` {FLAG_ICONS['ban' if ban else 'strike']} **{kind}**"
    if player:
        head += f" · {player_label(r['name'], r['player_tag'])}"
    lines = [head]
    if r["reason"]:
        reason = r["reason"] if len(r["reason"]) <= 300 else r["reason"][:299].rstrip() + "…"
        lines.append(f"> {reason}")
    meta = f"by <@{r['created_by']}> {ts(r['created_at'], 'd')}"
    if r["expires_at"]:
        meta += f" · expires {ts(r['expires_at'], 'R')}"
    lines.append(f"-# {meta}")
    return "\n".join(lines)


def flags_summary(rows) -> str:
    """Count line above /flag list: ``⛔ **2** bans · ⚠️ **3** strikes``."""
    bans = sum(1 for r in rows if r["flag_type"] == "ban")
    strikes = len(rows) - bans
    return SEP.join(
        (
            f"{FLAG_ICONS['ban']} **{bans}** ban{'s' if bans != 1 else ''}",
            f"{FLAG_ICONS['strike']} **{strikes}** strike{'s' if strikes != 1 else ''}",
        )
    )


def summary_line(rank: int, r, width: int | None = None) -> str:
    """One /flag summary row: rank, strike total (``width`` pads it so a page lines up), the
    player linked to their profile, their strikes and the latest one."""
    plural = "s" if r["strikes"] != 1 else ""
    parts = [
        f"`{rank:>2}.` {chip(r['total'], width)} {player_label(r['name'], r['player_tag'])}",
        f"{r['strikes']} strike{plural}, latest {ts(r['last_strike'], 'd')}",
    ]
    if r["banned"]:
        parts.append(f"{FLAG_ICONS['ban']} banned")
    return SEP.join(parts)


def fit_blocks(
    blocks: Sequence[str],
    budget: int = MESSAGE_BUDGET,
    sep: str = "\n",
    hint: str = "Narrow it down with the filters.",
) -> list[str]:
    """The blocks that fit in ``budget`` characters (joined with ``sep``), plus a note saying how
    many were left out (and ``hint``)."""
    used = 0
    for i, block in enumerate(blocks):
        used += len(block) + len(sep)
        if used > budget - 80:
            return [*blocks[:i], f"-# … {len(blocks) - i} more not shown. {hint}".strip()]
    return list(blocks)


def _profile(member) -> str:
    """Town Hall and trophies with their game icons, as in the join and leave posts."""
    th = getattr(member, "town_hall", None)
    if not th:
        return ""
    th_emoji = EMOJIS.town_hall(th)
    parts = [f"{th_emoji}**{th}**" if th_emoji else f"TH **{th}**"]
    trophies = getattr(member, "trophies", None)
    if trophies is not None:
        icon = EMOJIS.league(getattr(member, "league", None)) or EMOJIS.get("res_trophy", "🏆")
        parts.append(f"{icon}**{trophies:,}**")
    return " ".join(parts)


def flag_alert_embed(
    member,
    clan_name: str,
    clan_tag: str,
    flags: Sequence,
    *,
    badge_url: str | None = None,
    at: datetime | None = None,
) -> discord.Embed:
    """A flagged player joined one of the server's clans: the player (linked to their profile) as
    the title, the clan with its badge as the author, their Town Hall and trophies, then the flags
    and their strike total."""
    n = len(flags)
    lines = [
        f"🚩 Joined **{escape_markdown(clan_name)}** with **{n}** active flag{'s' if n != 1 else ''}",
        _profile(member),
    ]
    e = player_embed(member.name, member.tag, lines, discord.Color.red())
    clan_author(e, clan_name, clan_tag, badge_url)
    blocks = fit_blocks([flag_line(r, player=False) for r in flags], 1024, hint="See `/flag list`.")
    e.add_field(name="Flags", value="\n".join(blocks), inline=False)
    total = sum(r["weight"] for r in flags if r["flag_type"] == "strike")
    if total:
        e.add_field(name="Active strike total", value=f"{FLAG_ICONS['strike']} **{total}**")
    e.timestamp = at
    return e


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
        description=f"Account: {player_label(player_name, player_tag)}",
        color=discord.Color.red() if flag_type == "ban" else discord.Color.orange(),
    )
    e.add_field(name="Reason", value=(reason or "No reason given.")[:1024], inline=False)
    if flag_type == "strike":
        e.add_field(name="Weight", value=f"**{weight}**")
        if total is not None:
            e.add_field(name="Active strike total", value=f"**{total}**")
    e.add_field(name="Expires", value=ts(expires_at, "R") if expires_at else "Never")
    return e


async def dm_users(bot, user_ids: Iterable[int], embed: discord.Embed) -> tuple[list[int], list[int]]:
    """DM each user. Returns (delivered, failed) user ids; failures are logged, never raised."""
    sent, failed = [], []
    for uid in user_ids:
        delivered = await send_dm(bot, uid, embed=embed, what="flag notice")
        (sent if delivered else failed).append(uid)
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
        player = await self.db.get_player_row(tag)
        label = player_label(player["name"] if player else None, tag)
        msg = f"Created {flag_type.name.lower()} flag `#{flag_id}` on {label}."
        total = None
        if flag_type.value == "strike":
            total = await repo.strike_total(self.db, interaction.guild_id, tag, now)
            msg += f"\nWeight **{weight}**{SEP}active strike total {FLAG_ICONS['strike']} **{total}**"
        embed = ok_embed(msg)
        if not dm:
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        embed.add_field(
            name="Direct message", value=await self._notify(interaction, tag, flag_id, total), inline=False
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

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
            parts.append("📨 Sent to " + ", ".join(f"<@{u}>" for u in sent) + ".")
        if failed:
            parts.append(
                "❌ Couldn't DM " + ", ".join(f"<@{u}>" for u in failed) + " (DMs closed or user not found)."
            )
        return "\n".join(parts)

    @flag.command(name="delete", description="Delete a flag by id (Flags Manager)")
    @manager("flags")
    async def delete(self, interaction: discord.Interaction, flag_id: int) -> None:
        if not await repo.delete_flag(self.db, interaction.guild_id, flag_id):
            raise ResolveError(f"No flag `#{flag_id}` in this server.")
        await interaction.response.send_message(embed=ok_embed(f"Deleted flag `#{flag_id}`."), ephemeral=True)

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
        blocks = fit_blocks([flags_summary(rows), *(flag_line(r) for r in rows)], sep="\n\n")
        embeds = await self._pages(interaction.guild_id, "Flags", blocks if rows else ["No flags."], "\n")
        await interaction.response.send_message(embeds=embeds, ephemeral=True)

    async def _pages(
        self, guild_id: int, title: str, lines: Sequence[str], gap: str = ""
    ) -> list[discord.Embed]:
        """``lines`` (already cut to fit one message) over as many embeds as they need, titled once.
        ``gap`` is an extra line break between lines."""
        embeds = []
        for i, chunk in enumerate(chunk_lines([line + gap for line in lines])):
            e = await make_embed(self.bot, guild_id, title=title if i == 0 else None)
            e.description = chunk.strip()
            embeds.append(e)
        return embeds

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
        width = chip_width(r["total"] for r in rows)
        lines = fit_blocks([summary_line(i, r, width) for i, r in enumerate(rows, 1)])
        embeds = await self._pages(interaction.guild_id, "Strike totals", lines or ["No active strikes."])
        await interaction.response.send_message(embeds=embeds, ephemeral=True)

    @commands.Cog.listener()
    async def on_clash_member_join(self, event: MemberJoined) -> None:
        now = datetime.now(UTC)
        for guild_id in await self.db.guilds_for_clan(event.clan_tag):
            active = await repo.active_flags(self.db, guild_id, event.member.tag, now)
            if not active:
                continue
            clan = await self.db.get_clan_row(event.clan_tag)
            embed = flag_alert_embed(
                event.member,
                event.clan_name,
                event.clan_tag,
                active,
                badge_url=clan["badge_url"] if clan else None,
                at=now,
            )
            await send_server_log(self.bot, guild_id, "flag_alert", embed)


async def setup(bot) -> None:
    await bot.add_cog(Flags(bot))
