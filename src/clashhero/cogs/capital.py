"""Clan Capital: raid weekend summaries, capital gold contributions and player raid history."""

from __future__ import annotations

import discord
from discord import app_commands

from clashhero.cogs._base import BaseCog
from clashhero.cogs._war_embeds import resolve_player_tags
from clashhero.repos import capital as cap_repo
from clashhero.utils.fmt import make_embed, table, ts
from clashhero.utils.resolve import clan_autocomplete, player_autocomplete, resolve_clan
from clashhero.utils.seasons import game_season, previous_game_season

SEASON_CHOICES = [
    app_commands.Choice(name="This season", value="current"),
    app_commands.Choice(name="Last season", value="last"),
]


class Capital(BaseCog):
    capital = app_commands.Group(name="capital", description="Clan Capital raids and contributions")

    @capital.command(name="raids", description="Raid weekend summary with per-member attacks and loot")
    @app_commands.describe(weeks_back="0 = latest raid weekend, 1 = the one before, ...")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def raids(
        self,
        interaction: discord.Interaction,
        clan: str | None = None,
        weeks_back: app_commands.Range[int, 0, 9] = 0,
    ) -> None:
        tag = await resolve_clan(interaction, clan, must_be_tracked=False)
        await interaction.response.defer()
        weekend = await cap_repo.raid_weekend(self.db, tag, weeks_back)
        if weekend is not None:
            members = await cap_repo.raid_members(self.db, tag, weekend["start_time"])
            missing = await cap_repo.raid_non_participants(self.db, tag, weekend["start_time"])
            summary = dict(weekend)
        else:  # not collected yet (clan just added): read the API directly
            log = await self.coc.get_raid_log(tag, limit=weeks_back + 1)
            entries = list(log)
            if len(entries) <= weeks_back:
                await interaction.followup.send("No raid weekend found.")
                return
            entry = entries[weeks_back]
            summary = {
                "start_time": entry.start_time.time.isoformat(),
                "end_time": entry.end_time.time.isoformat(),
                "state": str(getattr(entry.state, "value", entry.state)),
                "total_loot": entry.total_loot,
                "attack_count": entry.attack_count,
                "districts_destroyed": entry.destroyed_district_count,
                "offensive_reward": entry.offensive_reward,
                "defensive_reward": entry.defensive_reward,
            }
            members = sorted(
                (
                    {
                        "name": m.name,
                        "attacks": m.attack_count,
                        "attack_limit": m.attack_limit + (m.bonus_attack_limit or 0),
                        "loot": m.capital_resources_looted,
                    }
                    for m in entry.members or []
                ),
                key=lambda m: -m["loot"],
            )
            missing = []
        clan_row = await self.db.get_clan_row(tag)
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title=f"Raid weekend · {clan_row['name'] if clan_row else tag}",
        )
        state = "ongoing" if summary["state"] == "ongoing" else "ended"
        e.description = (
            f"{ts(summary['start_time'], 'd')} – {ts(summary['end_time'], 'd')} · **{state}**\n"
            f"Loot: **{summary['total_loot'] or 0:,}** · Attacks: {summary['attack_count'] or 0} · "
            f"Districts destroyed: {summary['districts_destroyed'] or 0}\n"
            f"Raid medals: offense {summary.get('offensive_reward') or 0}, "
            f"defense {summary.get('defensive_reward') or 0}\n"
        )
        data = [
            (i + 1, m["name"][:14], f"{m['attacks']}/{m['attack_limit']}", f"{m['loot']:,}")
            for i, m in enumerate(members)
        ]
        e.description += table(data, ["#", "Player", "Hits", "Loot"])
        e.description = e.description[:4096]
        if missing:
            names = ", ".join(discord.utils.escape_markdown(m["name"]) for m in missing)
            e.add_field(name=f"Didn't raid ({len(missing)})", value=names[:1024], inline=False)
        await interaction.followup.send(embed=e)

    @capital.command(name="contributions", description="Capital gold contributed per member this season")
    @app_commands.choices(season=SEASON_CHOICES)
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def contributions(
        self,
        interaction: discord.Interaction,
        clan: str | None = None,
        season: app_commands.Choice[str] | None = None,
    ) -> None:
        tag = await resolve_clan(interaction, clan)
        s = previous_game_season() if season and season.value == "last" else game_season()
        rows = await cap_repo.capital_contributions(self.db, tag, s.id)
        data = [(i + 1, r["name"][:16], f"{r['capital_gold']:,}") for i, r in enumerate(rows[:50])]
        total = sum(r["capital_gold"] for r in rows)
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title="Capital gold contributions",
            description=(
                f"Season ending {ts(s.end, 'd')} · total **{total:,}**\n"
                + (table(data, ["#", "Player", "Gold"]) if data else "No contributions recorded yet.")
            )[:4096],
        )
        await interaction.response.send_message(embed=e)

    @capital.command(name="history", description="A player's raid weekends (attacks and loot)")
    @app_commands.autocomplete(player=player_autocomplete)
    async def history(
        self, interaction: discord.Interaction, player: str | None = None, user: discord.User | None = None
    ) -> None:
        tags = await resolve_player_tags(interaction, player, user)
        rows = await cap_repo.player_raid_history(self.db, tags)
        data = [
            (r["start_time"][:10], r["name"][:12], f"{r['attacks']}/{r['attack_limit']}", f"{r['loot']:,}")
            for r in rows
        ]
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title="Raid history",
            description=table(data, ["Weekend", "Player", "Hits", "Loot"])
            if data
            else "No raids recorded yet.",
        )
        await interaction.response.send_message(embed=e)


async def setup(bot) -> None:
    await bot.add_cog(Capital(bot))
