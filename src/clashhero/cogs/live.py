"""Live, auto-edited embeds: current war, CWL round, Clan Games, last seen, clan showcase."""

from __future__ import annotations

import json
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from clashhero import events as ev
from clashhero.cogs import _live_embeds as L
from clashhero.cogs._base import BaseCog
from clashhero.constants import LIVE_KINDS
from clashhero.services.outbox import update_live_message
from clashhero.utils.checks import manager
from clashhero.utils.fmt import make_embed
from clashhero.utils.resolve import clan_autocomplete, resolve_clan
from clashhero.utils.seasons import clan_games
from clashhero.utils.style import SEP, ok_embed

log = logging.getLogger(__name__)

OUR_KINDS = ("war_embed", "cwl_embed", "clan_games", "last_seen", "clan_embed")
KIND_CHOICES = [app_commands.Choice(name=LIVE_KINDS[k], value=k) for k in OUR_KINDS]
WAR_DEBOUNCE_SECONDS = 60


def live_line(r) -> str:
    """One /live list row: ``📡 **Current war** · `#TAG` → #channel``."""
    kind = LIVE_KINDS.get(r["kind"], r["kind"])
    return f"📡 **{kind}**{SEP}`{r['scope'] or 'server'}` → <#{r['channel_id']}>"


class Live(BaseCog):
    live = app_commands.Group(name="live", description="Auto-updating embeds", guild_only=True)

    def __init__(self, bot) -> None:
        super().__init__(bot)
        self._last_war_refresh: dict[str, float] = {}

    async def cog_load(self) -> None:
        self.games_loop.start()
        self.slow_loop.start()

    async def cog_unload(self) -> None:
        self.games_loop.cancel()
        self.slow_loop.cancel()

    # ------------------------------------------------------------------ rendering

    async def _clan_name(self, clan_tag: str) -> str:
        row = await self.db.get_clan_row(clan_tag)
        return row["name"] if row else clan_tag

    async def render(self, kind: str, clan_tag: str, options: dict | None = None) -> discord.Embed:
        if kind in ("war_embed", "cwl_embed"):
            sql = "SELECT * FROM wars WHERE clan_tag = ?"
            if kind == "cwl_embed":
                sql += " AND war_type = 'cwl'"
            war = await self.db.fetchone(sql + " ORDER BY preparation_start DESC LIMIT 1", (clan_tag,))
            members, attacks = [], []
            if war:
                members = await self.db.fetchall("SELECT * FROM war_members WHERE war_id = ?", (war["id"],))
                attacks = await self.db.fetchall("SELECT * FROM war_attacks WHERE war_id = ?", (war["id"],))
            names = {m["player_tag"]: m["name"] for m in members}
            return L.war_embed(war, await self._clan_name(clan_tag), members, attacks, names)
        if kind == "clan_games":
            window = clan_games()
            rows = await self.db.fetchall(
                "SELECT g.player_tag, g.points, p.name FROM clan_games_points g "
                "LEFT JOIN players p ON p.player_tag = g.player_tag "
                "WHERE g.games_id = ? AND g.clan_tag = ?",
                (window.id, clan_tag),
            )
            return L.clan_games_embed(await self._clan_name(clan_tag), rows, window.end)
        if kind == "last_seen":
            rows = await self.db.fetchall(
                "SELECT p.name, p.last_seen, "
                "(SELECT SUM(count) FROM activity a WHERE a.player_tag = p.player_tag "
                " AND a.hour >= strftime('%Y-%m-%dT%H', 'now', '-1 day')) AS day, "
                "(SELECT SUM(count) FROM activity a WHERE a.player_tag = p.player_tag "
                " AND a.hour >= strftime('%Y-%m-%dT%H', 'now', '-7 days')) AS week "
                "FROM players p WHERE p.clan_tag = ?",
                (clan_tag,),
            )
            return L.last_seen_embed(await self._clan_name(clan_tag), rows)
        if kind == "clan_embed":
            clan = await self.db.get_clan_row(clan_tag)
            members = await self.db.clan_member_rows(clan_tag)
            return L.clan_showcase_embed(clan, members, (options or {}).get("description"))
        raise ValueError(kind)

    async def refresh_row(self, row) -> None:
        try:
            options = json.loads(row["options"]) if row["options"] else None
            embed = await self.render(row["kind"], row["scope"], options)
            await update_live_message(self.bot, row, embeds=[embed])
        except Exception:
            log.exception("Live refresh failed for %s %s", row["kind"], row["scope"])

    async def refresh_kind(self, kind: str) -> None:
        for row in await self.db.live_messages(kind):
            await self.refresh_row(row)

    # ------------------------------------------------------------------ triggers

    @commands.Cog.listener()
    async def on_clash_war_update(self, e: ev.WarUpdated) -> None:
        now = time.monotonic()
        if now - self._last_war_refresh.get(e.clan_tag, 0) < WAR_DEBOUNCE_SECONDS:
            return
        self._last_war_refresh[e.clan_tag] = now
        kinds = ("war_embed", "cwl_embed") if e.war.get("war_type") == "cwl" else ("war_embed",)
        for kind in kinds:
            for row in await self.db.live_messages(kind):
                if row["scope"] == e.clan_tag:
                    await self.refresh_row(row)

    @tasks.loop(minutes=10)
    async def games_loop(self) -> None:
        await self.refresh_kind("clan_games")

    @tasks.loop(minutes=30)
    async def slow_loop(self) -> None:
        await self.refresh_kind("last_seen")
        await self.refresh_kind("clan_embed")

    @games_loop.before_loop
    @slow_loop.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------ commands

    async def _create(
        self,
        interaction: discord.Interaction,
        kind: str,
        clan: str | None,
        channel: discord.TextChannel | None,
        options: dict | None = None,
    ) -> None:
        tag = await resolve_clan(interaction, clan)
        target = channel or interaction.channel
        await interaction.response.defer(ephemeral=True)
        await self.db.upsert_live_message(interaction.guild_id, kind, tag, target.id, None, options)
        row = await self.db.fetchone(
            "SELECT * FROM live_messages WHERE guild_id = ? AND kind = ? AND scope = ?",
            (interaction.guild_id, kind, tag),
        )
        await self.refresh_row(row)
        name = await self._clan_name(tag)
        label = f"**{name}** `{tag}`" if name != tag else f"`{tag}`"
        await interaction.followup.send(
            embed=ok_embed(f"Live **{LIVE_KINDS[kind]}** for {label} posted in {target.mention}.")
        )

    @live.command(name="war", description="Live embed of the clan's current war")
    @app_commands.autocomplete(clan=clan_autocomplete)
    @manager("bot")
    async def live_war(
        self,
        interaction: discord.Interaction,
        clan: str | None = None,
        channel: discord.TextChannel | None = None,
    ) -> None:
        await self._create(interaction, "war_embed", clan, channel)

    @live.command(name="cwl", description="Live embed of the clan's current CWL round")
    @app_commands.autocomplete(clan=clan_autocomplete)
    @manager("bot")
    async def live_cwl(
        self,
        interaction: discord.Interaction,
        clan: str | None = None,
        channel: discord.TextChannel | None = None,
    ) -> None:
        await self._create(interaction, "cwl_embed", clan, channel)

    @live.command(name="clan-games", description="Live Clan Games leaderboard")
    @app_commands.autocomplete(clan=clan_autocomplete)
    @manager("bot")
    async def live_games(
        self,
        interaction: discord.Interaction,
        clan: str | None = None,
        channel: discord.TextChannel | None = None,
    ) -> None:
        await self._create(interaction, "clan_games", clan, channel)

    @live.command(name="last-seen", description="Live last-seen / activity board")
    @app_commands.autocomplete(clan=clan_autocomplete)
    @manager("bot")
    async def live_last_seen(
        self,
        interaction: discord.Interaction,
        clan: str | None = None,
        channel: discord.TextChannel | None = None,
    ) -> None:
        await self._create(interaction, "last_seen", clan, channel)

    @live.command(name="clan", description="Live clan showcase embed")
    @app_commands.autocomplete(clan=clan_autocomplete)
    @manager("bot")
    async def live_clan(
        self,
        interaction: discord.Interaction,
        clan: str | None = None,
        channel: discord.TextChannel | None = None,
    ) -> None:
        tag = await resolve_clan(interaction, clan)
        description = None
        try:
            description = (await self.coc.get_clan(tag)).description
        except Exception:
            log.debug("Could not fetch clan description", exc_info=True)
        await self._create(interaction, "clan_embed", tag, channel, {"description": description or ""})

    @live.command(name="remove", description="Stop a live embed")
    @app_commands.choices(kind=KIND_CHOICES)
    @app_commands.autocomplete(clan=clan_autocomplete)
    @manager("bot")
    async def live_remove(
        self, interaction: discord.Interaction, kind: app_commands.Choice[str], clan: str | None = None
    ) -> None:
        tag = await resolve_clan(interaction, clan)
        await self.db.delete_live_message(interaction.guild_id, kind.value, tag)
        await interaction.response.send_message(
            embed=ok_embed(f"Stopped **{kind.name}** for `{tag}`."), ephemeral=True
        )

    @live.command(name="list", description="List live embeds in this server")
    async def live_list(self, interaction: discord.Interaction) -> None:
        rows = await self.db.fetchall(
            "SELECT * FROM live_messages WHERE guild_id = ? ORDER BY kind, scope", (interaction.guild_id,)
        )
        lines = [live_line(r) for r in rows]
        embed = await make_embed(
            self.bot,
            interaction.guild_id,
            title="Live embeds",
            description="\n".join(lines)[:4000] or "None yet. Try `/live war`.",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Live(bot))
