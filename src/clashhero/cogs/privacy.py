"""/privacy (see or delete your data), /about, and automatic data cleanup.

- Leaving a server deletes that server's data immediately. If the bot was removed while it was
  offline, the server is noticed at startup and purged after a grace period.
- A daily job applies the retention windows in services/privacy.py.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from clashhero import __version__
from clashhero.cogs._base import BaseCog
from clashhero.constants import SUPERCELL_DISCLAIMER
from clashhero.services import privacy
from clashhero.utils.fmt import make_embed

log = logging.getLogger(__name__)


class ConfirmDelete(discord.ui.View):
    def __init__(self, cog: Privacy, user_id: int) -> None:
        super().__init__(timeout=120)
        self.cog = cog
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    @discord.ui.button(label="Delete my data", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        report = await privacy.purge_user(self.cog.db, self.user_id)
        self.stop()
        await interaction.response.edit_message(
            content=f"Done. Removed or anonymised {report.total} record(s) linked to your Discord "
            "account across all servers. Public Clash of Clans game data is no longer associated "
            "with you.",
            view=None,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(content="Cancelled. Nothing was deleted.", view=None)


class Privacy(BaseCog):
    privacy_group = app_commands.Group(name="privacy", description="See or delete the data stored about you")

    async def cog_load(self) -> None:
        self.retention_loop.start()

    async def cog_unload(self) -> None:
        self.retention_loop.cancel()

    # ------------------------------------------------------------------ commands

    @privacy_group.command(name="mydata", description="See what ClashHero stores that is linked to you")
    async def mydata(self, interaction: discord.Interaction) -> None:
        summary = await privacy.user_data_summary(self.db, interaction.user.id)
        links = await self.db.get_links(interaction.user.id)
        e = await make_embed(self.bot, interaction.guild_id, title="Your data in ClashHero")
        if links:
            e.add_field(
                name="Linked Clash accounts",
                value="\n".join(
                    f"`{r['player_tag']}` {r['name'] or ''}{' (verified)' if r['verified'] else ''}"
                    for r in links
                ),
                inline=False,
            )
        e.add_field(
            name="Records linked to your Discord ID",
            value="\n".join(f"{t}: {n}" for t, n in sorted(summary.items())) or "None",
            inline=False,
        )
        e.set_footer(text="Use /privacy delete to remove all of it.")
        await interaction.response.send_message(embed=e, ephemeral=True)

    @privacy_group.command(name="delete", description="Delete all data linked to your Discord account")
    async def delete(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "This removes your linked accounts, alerts, roster signups and target calls in **every** "
            "server, and anonymises things you configured for servers (such as reminders). "
            "It can't be undone.",
            view=ConfirmDelete(self, interaction.user.id),
            ephemeral=True,
        )

    @app_commands.command(name="about", description="About ClashHero, its terms and privacy policy")
    async def about(self, interaction: discord.Interaction) -> None:
        cfg = self.bot.config
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title="ClashHero",
            description="Clan management for Clash of Clans: legend tracking, war and CWL tools, "
            "rosters, logs, reminders and more. Use `/help` to see every command.",
        )
        e.add_field(name="Version", value=__version__)
        e.add_field(name="Servers", value=str(len(self.bot.guilds)))
        e.add_field(name="Your data", value="`/privacy mydata` · `/privacy delete`")
        view = discord.ui.View()
        for label, url in (
            ("Terms of Service", cfg.terms_url),
            ("Privacy Policy", cfg.privacy_url),
            ("Support", cfg.support_url),
        ):
            if url:
                view.add_item(discord.ui.Button(label=label, style=discord.ButtonStyle.link, url=url))
        e.set_footer(text=SUPERCELL_DISCLAIMER)
        await interaction.response.send_message(embed=e, view=view, ephemeral=True)

    # ------------------------------------------------------------------ server lifecycle

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        report = await privacy.purge_guild(self.db, guild.id)
        log.info("Removed from guild %s; purged %d rows", guild.id, report.total)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.db.execute("DELETE FROM guild_departures WHERE guild_id = ?", (guild.id,))

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        missing = await privacy.note_departures(self.db, {g.id for g in self.bot.guilds}, datetime.now(UTC))
        if missing:
            log.info(
                "No longer in %d server(s) the bot has data for; purging after %d days unless re-added",
                len(missing),
                privacy.DEPARTURE_GRACE_DAYS,
            )

    # ------------------------------------------------------------------ retention

    @tasks.loop(hours=24)
    async def retention_loop(self) -> None:
        now = datetime.now(UTC)
        report = await privacy.purge_old_data(self.db, now)
        purged = await privacy.purge_departed_guilds(self.db, now)
        log.info("Retention: removed %d old rows, purged %d departed server(s)", report.total, len(purged))

    @retention_loop.before_loop
    async def _wait(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot) -> None:
    await bot.add_cog(Privacy(bot))
