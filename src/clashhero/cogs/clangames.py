"""Clan Games leaderboard (points tracked from the "Games Champion" achievement delta)."""

from __future__ import annotations

import re
from datetime import datetime

import discord
from discord import app_commands

from clashhero.cogs._base import BaseCog
from clashhero.cogs._live_embeds import (
    clan_row_header,
    fit_names,
    games_summary_lines,
    games_table,
    games_window_line,
    icon_text,
)
from clashhero.cogs._log_embeds import arrow
from clashhero.repos import capital as cap_repo
from clashhero.services.emojis import EMOJIS
from clashhero.utils.fmt import make_embed
from clashhero.utils.resolve import ResolveError, clan_autocomplete, resolve_clan
from clashhero.utils.seasons import clan_games, utcnow


def games_embed_text(summary: cap_repo.GamesSummary, when: str = "", limit: int = 50) -> str:
    """The clan's points and tier, when the games start or end (``when``), then the leaderboard."""
    head = "\n".join([*games_summary_lines(summary.total), *([when] if when else [])])
    rows = summary.rows[:limit]
    return f"{head}\n\n{games_table(rows, 4090 - len(head), cap=summary.cap)}"


def month_label(games_id: str) -> str:
    """'2026-11' -> 'November 2026' (unchanged if it isn't a month)."""
    try:
        return datetime.strptime(games_id, "%Y-%m").strftime("%B %Y")
    except ValueError:
        return games_id


class ClanGames(BaseCog):
    @app_commands.command(name="clangames", description="Clan Games points leaderboard")
    @app_commands.describe(
        month="Month like 2026-11 (default: current)",
        cap="Per-player points cap for this event (default 4000)",
        show_missing="List members who haven't scored yet",
    )
    @app_commands.autocomplete(clan=clan_autocomplete)
    @app_commands.guild_only()
    async def clangames(
        self,
        interaction: discord.Interaction,
        clan: str | None = None,
        month: str | None = None,
        cap: app_commands.Range[int, 1000, 10000] = cap_repo.GAMES_PLAYER_CAP,
        show_missing: bool = False,
    ) -> None:
        tag = await resolve_clan(interaction, clan)
        window = clan_games()
        games_id = window.id
        if month:
            if not re.match(r"^\d{4}-\d{2}$", month.strip()):
                raise ResolveError("Month must look like `2026-11`.")
            games_id = month.strip()
        summary = cap_repo.summarize_games(await cap_repo.clan_games_rows(self.db, tag, games_id), cap)
        clan_row = await self.db.get_clan_row(tag)
        current = games_id == window.id
        when = games_window_line(window.start, window.end) if current else ""
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title=icon_text(EMOJIS.get("ui_clan_games"), f"Clan Games {month_label(games_id)}"),
            description=games_embed_text(summary, when),
        )
        clan_row_header(e, clan_row, tag)
        if current and utcnow() < window.start:
            e.set_footer(text="Clan Games haven't started yet.")
        if show_missing:
            missing = await cap_repo.clan_games_missing(self.db, tag, games_id)
            names = [discord.utils.escape_markdown(m["name"]) for m in missing]
            e.add_field(
                name=icon_text(arrow("down") if missing else "", f"No points yet ({len(missing)})"),
                value=fit_names(names) if names else f"{arrow('up')} Everyone has scored",
                inline=False,
            )
        await interaction.response.send_message(embed=e)


async def setup(bot) -> None:
    await bot.add_cog(ClanGames(bot))
