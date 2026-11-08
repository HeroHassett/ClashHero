"""/status and the bot's Discord presence.

The presence shows the Clash API state to everyone in every server: "Watching N clans · /help"
normally, and "🛠️ Clash API maintenance" (idle) while Supercell's API is down, which is usually
during a game update. /status gives the details: API state, when each kind of data last updated,
and what's being tracked.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from clashhero import __version__
from clashhero.cogs._base import BaseCog
from clashhero.events import MaintenanceChanged
from clashhero.utils.fmt import duration, make_embed, ts
from clashhero.utils.style import SEP, chip

log = logging.getLogger(__name__)

LOOP_LABELS = (
    ("clans", "Clans & donations"),
    ("players", "Players & ranked"),
    ("wars", "Wars & CWL"),
    ("raids", "Capital raids"),
)


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def presence_for(
    in_maintenance: bool, clans: int, servers: int = 1, phase: int = 0
) -> tuple[discord.Status, discord.BaseActivity]:
    """What the member list shows under the bot's name. Rotates between a help hint and what's
    being watched; maintenance always wins."""
    if in_maintenance:
        return discord.Status.idle, discord.CustomActivity(name="🛠️ Clash API maintenance")
    if phase % 2 == 0:
        return discord.Status.online, discord.CustomActivity(name="/help for commands")
    label = f"{_plural(clans, 'clan')} in {_plural(servers, 'server')}"
    return discord.Status.online, discord.Activity(type=discord.ActivityType.watching, name=label)


def status_fields(
    *,
    in_maintenance: bool,
    maintenance_since: datetime | None,
    last_success: dict[str, datetime],
    now: datetime,
) -> list[tuple[str, str]]:
    """(name, value) embed fields describing API state and data freshness."""
    if in_maintenance:
        since = (
            f" since {ts(maintenance_since)} ({duration((now - maintenance_since).total_seconds())})"
            if maintenance_since
            else ""
        )
        api = f"🛠️ **Down for maintenance**{since}. ClashHero resumes automatically when it's back."
    else:
        api = "✅ Online"
    fresh = "\n".join(
        f"{label}: {ts(last_success[key]) if key in last_success else 'waiting for first update'}"
        for key, label in LOOP_LABELS
    )
    return [("Clash of Clans API", api), ("Last updated", fresh)]


class Status(BaseCog):
    def __init__(self, bot) -> None:
        super().__init__(bot)
        self.started_at = datetime.now(UTC)
        self._shown: tuple | None = None
        self._phase = 0

    async def cog_load(self) -> None:
        self.presence_loop.start()

    async def cog_unload(self) -> None:
        self.presence_loop.cancel()

    async def _clan_count(self) -> int:
        return await self.db.fetchval("SELECT COUNT(DISTINCT clan_tag) FROM guild_clans", (), 0)

    async def refresh_presence(self, force: bool = False, rotate: bool = False) -> None:
        if rotate:
            self._phase += 1
        state = (
            self.collector.in_maintenance,
            await self._clan_count(),
            len(self.bot.guilds),
            self._phase % 2,
        )
        if state == self._shown and not force:
            return
        status, activity = presence_for(*state)
        try:
            await self.bot.change_presence(status=status, activity=activity)
            self._shown = state
        except Exception:  # not connected yet, or rate limited; the loop retries
            log.debug("Couldn't update presence", exc_info=True)

    @tasks.loop(minutes=5)
    async def presence_loop(self) -> None:
        await self.refresh_presence(rotate=True)

    @presence_loop.before_loop
    async def _wait(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self.refresh_presence(force=True)

    @commands.Cog.listener()
    async def on_clash_maintenance(self, _: MaintenanceChanged) -> None:
        await self.refresh_presence(force=True)

    @app_commands.command(name="status", description="Clash API status, data freshness and what's tracked")
    async def status(self, interaction: discord.Interaction) -> None:
        now = datetime.now(UTC)
        c = self.collector
        e = await make_embed(self.bot, interaction.guild_id, title="ClashHero status")
        for name, value in status_fields(
            in_maintenance=c.in_maintenance,
            maintenance_since=c.maintenance_since,
            last_success=c.last_success,
            now=now,
        ):
            e.add_field(name=name, value=value, inline=False)
        players = await self.db.fetchval("SELECT COUNT(*) FROM players", (), 0)
        clans = await self._clan_count()
        e.add_field(name="Tracking", value=f"{chip(f'{clans:,}')} clans{SEP}{chip(f'{players:,}')} players")
        e.add_field(name="Servers", value=chip(f"{len(self.bot.guilds):,}"))
        latency = self.bot.latency
        e.add_field(
            name="Discord latency", value=chip(f"{latency * 1000:.0f} ms") if latency == latency else "—"
        )
        e.set_footer(text=f"ClashHero {__version__} · up {duration((now - self.started_at).total_seconds())}")
        await interaction.response.send_message(embed=e, ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Status(bot))
