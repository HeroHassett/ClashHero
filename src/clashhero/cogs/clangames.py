"""Clan Games leaderboard (points tracked from the "Games Champion" achievement delta)."""

from __future__ import annotations

import re

import discord
from discord import app_commands

from clashhero.cogs._base import BaseCog
from clashhero.repos import capital as cap_repo
from clashhero.utils.fmt import make_embed, table, ts
from clashhero.utils.resolve import ResolveError, clan_autocomplete, resolve_clan
from clashhero.utils.seasons import clan_games, utcnow


def games_embed_text(summary: cap_repo.GamesSummary, limit: int = 50) -> str:
    data = [
        (i + 1, r["name"][:16], f"{r['points']:,}" + (" ✅" if r["maxed"] else ""))
        for i, r in enumerate(summary.rows[:limit])
    ]
    head = f"Clan points: **{summary.total:,}** · Tier **{summary.tier}/6**"
    if summary.next_tier_at:
        head += f" · {summary.next_tier_at - summary.total:,} to next tier"
    return head + "\n" + (table(data, ["#", "Player", "Points"]) if data else "No points recorded yet.")


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
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title=f"Clan Games {games_id} · {clan_row['name'] if clan_row else tag}",
            description=games_embed_text(summary)[:4096],
        )
        if games_id == window.id:
            now = utcnow()
            if now < window.start:
                e.set_footer(text="Clan Games haven't started yet.")
                e.description = f"Starts {ts(window.start)}\n" + e.description
            elif now < window.end:
                e.description = f"Ends {ts(window.end)}\n" + e.description
        if show_missing:
            missing = await cap_repo.clan_games_missing(self.db, tag, games_id)
            names = ", ".join(discord.utils.escape_markdown(m["name"]) for m in missing) or "Nobody 🎉"
            e.add_field(name=f"No points yet ({len(missing)})", value=names[:1024], inline=False)
        await interaction.response.send_message(embed=e)


async def setup(bot) -> None:
    await bot.add_cog(ClanGames(bot))
