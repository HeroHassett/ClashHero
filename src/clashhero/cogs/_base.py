"""Common base for feature cogs, plus the friendly error replies used by the bot-wide handler
(bot.tree.on_error = send_error). Cogs don't add their own handler: Discord.py calls both, which
would answer an error twice."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import coc
import discord
from discord import app_commands
from discord.ext import commands

from clashhero.utils.resolve import parse_tag
from clashhero.utils.style import fail_embed

if TYPE_CHECKING:
    from clashhero.bot import ClashHeroBot
    from clashhero.db import Database
    from clashhero.services.collector import Collector

log = logging.getLogger(__name__)


class BaseCog(commands.Cog):
    def __init__(self, bot: ClashHeroBot) -> None:
        self.bot = bot

    @property
    def db(self) -> Database:
        return self.bot.db

    @property
    def coc(self) -> coc.Client:
        return self.bot.coc

    @property
    def collector(self) -> Collector:
        return self.bot.collector

    parse_tag = staticmethod(parse_tag)


def error_message(error: BaseException) -> str | None:
    """User-facing text for an error, or None if it's unexpected (a bug)."""
    original = getattr(error, "original", error)
    if isinstance(original, coc.NotFound):
        return "Couldn't find that tag in Clash of Clans."
    if isinstance(original, coc.PrivateWarLog):
        return "That clan's war log is private."
    if isinstance(original, coc.Maintenance):
        return (
            "🛠️ The Clash of Clans API is down for maintenance (usually during a game update). "
            "ClashHero picks up automatically when it's back; check `/status`."
        )
    if isinstance(original, coc.GatewayError):
        return "The Clash of Clans API isn't responding right now. Try again in a minute."
    if isinstance(original, discord.Forbidden):
        return "I'm missing Discord permissions to do that here."
    if isinstance(error, app_commands.CheckFailure):
        return str(error) or "You don't have permission to use this command."
    if isinstance(original, app_commands.AppCommandError) and str(original):
        return str(original)
    return None


async def send_error(interaction: discord.Interaction, error: BaseException) -> None:
    msg = error_message(error)
    if msg is None:
        name = getattr(interaction.command, "qualified_name", "?")
        log.error("Unhandled error in /%s", name, exc_info=error)
        msg = "Something went wrong running that command. It's been logged; try again in a moment."
    embed = fail_embed(msg)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.HTTPException:
        pass
