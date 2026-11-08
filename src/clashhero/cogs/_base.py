"""Common base for feature cogs: shortcuts plus uniform, friendly error handling."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import coc
import discord
from discord import app_commands
from discord.ext import commands

from clashhero.utils.resolve import parse_tag

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

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        await send_error(interaction, error)


def error_message(error: BaseException) -> str | None:
    """User-facing text for an error, or None if it's unexpected (a bug)."""
    original = getattr(error, "original", error)
    if isinstance(original, coc.NotFound):
        return "Couldn't find that tag in Clash of Clans."
    if isinstance(original, coc.PrivateWarLog):
        return "That clan's war log is private."
    if isinstance(original, coc.Maintenance):
        return "Clash of Clans is in maintenance right now. Try again later."
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
        log.error("Unhandled error in %s", interaction.command, exc_info=error)
        msg = "Something went wrong running that command."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException:
        pass
