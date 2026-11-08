"""Clan War League views and season stats.

Live views (round, lineup, group standings, TH spread) query the league group on demand;
season stats and history come from wars the collector stored.
"""

from __future__ import annotations

import re
from typing import Any

import discord
from discord import app_commands

from clashhero.cogs._base import BaseCog
from clashhero.cogs._war_embeds import (
    lineup_embed,
    resolve_player_tags,
    standings_table,
    state_value,
    war_overview_embed,
)
from clashhero.repos import war as war_repo
from clashhero.utils.fmt import guild_color, make_embed, stars, table
from clashhero.utils.resolve import ResolveError, clan_autocomplete, player_autocomplete, resolve_clan
from clashhero.utils.seasons import cwl_season_id

SEASON_RE = re.compile(r"^\d{4}-\d{2}$")


def _season(raw: str | None) -> str:
    if not raw:
        return cwl_season_id()
    if not SEASON_RE.match(raw.strip()):
        raise ResolveError("Season must look like `2026-11`.")
    return raw.strip()


def war_to_standing_row(war: Any) -> dict:
    """Flatten a coc ClanWar for repos.war.cwl_standings."""
    return {
        "clan_tag": war.clan.tag,
        "clan_name": war.clan.name,
        "clan_stars": war.clan.stars,
        "clan_destruction": war.clan.destruction,
        "clan_attacks": war.clan.attacks_used,
        "opp_tag": war.opponent.tag,
        "opp_name": war.opponent.name,
        "opp_stars": war.opponent.stars,
        "opp_destruction": war.opponent.destruction,
        "opp_attacks": war.opponent.attacks_used,
        "team_size": war.team_size,
        "state": state_value(war),
    }


def spread_text(town_halls: list[int]) -> str:
    return " ".join(f"TH{th}×{n}" for th, n in war_repo.th_spread(town_halls).items()) or "—"


class CWL(BaseCog):
    cwl = app_commands.Group(name="cwl", description="Clan War League rounds, standings and stats")

    # ------------------------------------------------------------------ API helpers

    async def _group(self, clan_tag: str):
        group = await self.coc.get_league_group(clan_tag)
        if group is None:
            raise ResolveError("That clan isn't in a CWL group right now.")
        return group

    @staticmethod
    def _round_tags(group: Any) -> list[list[str]]:
        return [[t for t in rnd if t and t != "#0"] for rnd in (group.rounds or [])]

    async def _round_wars(self, clan_tag: str, tags: list[str]) -> list[Any]:
        if not tags:
            return []
        return [w async for w in self.coc.get_league_wars(tags)]

    async def _our_war(self, clan_tag: str, group: Any, round_no: int | None) -> tuple[int, Any]:
        """(round number, our war). Default round: latest one that has started."""
        rounds = self._round_tags(group)
        candidates = [round_no] if round_no else list(range(len(rounds), 0, -1))
        fallback = None
        for r in candidates:
            if r < 1 or r > len(rounds):
                raise ResolveError(f"Round must be between 1 and {len(rounds)}.")
            for w in await self._round_wars(clan_tag, rounds[r - 1]):
                if clan_tag in (w.clan.tag, w.opponent.tag):
                    if round_no or state_value(w) != "preparation":
                        return r, w
                    fallback = fallback or (r, w)
        if fallback:
            return fallback
        raise ResolveError("No CWL war found for that round yet.")

    # ------------------------------------------------------------------ live views

    @cwl.command(name="round", description="Score and timers for a CWL round")
    @app_commands.describe(round="Round 1-7 (default: the latest started round)")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def round_(
        self,
        interaction: discord.Interaction,
        clan: str | None = None,
        round: app_commands.Range[int, 1, 7] | None = None,
    ) -> None:
        tag = await resolve_clan(interaction, clan, must_be_tracked=False)
        await interaction.response.defer()
        group = await self._group(tag)
        r, w = await self._our_war(tag, group, round)
        e = war_overview_embed(w, tag, await guild_color(self.bot, interaction.guild_id))
        e.title = f"CWL round {r} · {e.title}"
        await interaction.followup.send(embed=e)

    @cwl.command(name="lineup", description="Both lineups for a CWL round")
    @app_commands.describe(round="Round 1-7 (default: the latest started round)")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def lineup(
        self,
        interaction: discord.Interaction,
        clan: str | None = None,
        round: app_commands.Range[int, 1, 7] | None = None,
    ) -> None:
        tag = await resolve_clan(interaction, clan, must_be_tracked=False)
        await interaction.response.defer()
        group = await self._group(tag)
        r, w = await self._our_war(tag, group, round)
        e = lineup_embed(w, tag, await guild_color(self.bot, interaction.guild_id))
        e.title = f"CWL round {r} · {e.title}"
        await interaction.followup.send(embed=e)

    @cwl.command(name="stats", description="Group standings (stars incl. +10 per win, destruction)")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def stats(self, interaction: discord.Interaction, clan: str | None = None) -> None:
        tag = await resolve_clan(interaction, clan, must_be_tracked=False)
        await interaction.response.defer()
        group = await self._group(tag)
        all_tags = [t for rnd in self._round_tags(group) for t in rnd]
        wars = [war_to_standing_row(w) for w in await self._round_wars(tag, all_tags)]
        for c in group.clans:  # make sure every clan shows even before its first war
            wars.append(
                {
                    "clan_tag": c.tag,
                    "clan_name": c.name,
                    "opp_tag": c.tag,
                    "opp_name": c.name,
                    "state": "preparation",
                }
            )
        rows = war_repo.cwl_standings(wars)
        played = sum(1 for r in self._round_tags(group) if r)
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title=f"CWL standings · {group.season}",
            description=standings_table(rows) + f"\nRounds played: {played}/{group.number_of_rounds or 7}",
        )
        await interaction.followup.send(embed=e)

    @cwl.command(name="members", description="Town Hall spread of every clan in the CWL group")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def members(self, interaction: discord.Interaction, clan: str | None = None) -> None:
        tag = await resolve_clan(interaction, clan, must_be_tracked=False)
        await interaction.response.defer()
        group = await self._group(tag)
        e = await make_embed(self.bot, interaction.guild_id, title=f"CWL group · {group.season}")
        for c in sorted(group.clans, key=lambda c: c.tag != tag):
            ths = [m.town_hall for m in c.members]
            name = ("⭐ " if c.tag == tag else "") + f"{c.name} ({len(ths)} in roster)"
            e.add_field(name=name[:256], value=spread_text(ths)[:1024], inline=False)
        await interaction.followup.send(embed=e)

    # ------------------------------------------------------------------ season stats (DB)

    @cwl.command(name="attacks", description="Season participation: attacks used and missed per member")
    @app_commands.describe(season="CWL season like 2026-11 (default: current)")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def attacks(
        self, interaction: discord.Interaction, clan: str | None = None, season: str | None = None
    ) -> None:
        tag = await resolve_clan(interaction, clan)
        s = _season(season)
        rows = await war_repo.cwl_member_stats(self.db, tag, s)
        data = [
            (m.name[:14], f"{m.attacks}/{m.rounds}", m.missed, m.stars, f"{m.avg_destruction:.0f}")
            for m in sorted(rows, key=lambda m: (-m.missed, m.name))
        ]
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title=f"CWL attacks · {s}",
            description=(
                table(data, ["Player", "Hits", "Miss", "★", "Dst"])
                if data
                else "No CWL data recorded for this season."
            )[:4096],
        )
        await interaction.response.send_message(embed=e)

    @cwl.command(name="stars", description="Season star leaderboard for the clan's CWL")
    @app_commands.describe(season="CWL season like 2026-11 (default: current)")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def stars_(
        self, interaction: discord.Interaction, clan: str | None = None, season: str | None = None
    ) -> None:
        tag = await resolve_clan(interaction, clan)
        s = _season(season)
        rows = await war_repo.cwl_member_stats(self.db, tag, s)
        data = [
            (i + 1, m.name[:14], m.stars, m.new_stars, m.triples, f"{m.avg_destruction:.0f}", m.stars_allowed)
            for i, m in enumerate(rows)
        ]
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title=f"CWL stars · {s}",
            description=(
                table(data, ["#", "Player", "★", "New★", "3★", "Dst", "★Def"])
                if data
                else "No CWL data recorded for this season."
            )[:4096],
        )
        await interaction.response.send_message(embed=e)

    @cwl.command(name="history", description="A player's round-by-round CWL attacks and defenses")
    @app_commands.describe(season="CWL season like 2026-11 (default: all recorded)")
    @app_commands.autocomplete(player=player_autocomplete)
    async def history(
        self,
        interaction: discord.Interaction,
        player: str | None = None,
        user: discord.User | None = None,
        season: str | None = None,
    ) -> None:
        tags = await resolve_player_tags(interaction, player, user)
        rows = await war_repo.player_cwl_history(self.db, tags, _season(season) if season else None)
        lines = []
        current = None
        for r in rows[:40]:
            if r["cwl_season"] != current:
                current = r["cwl_season"]
                lines.append(f"**{current}**")
            a, d = r["attack"], r["defense"]
            att = (
                f"{stars(a['stars'])} {a['destruction']:.0f}% on TH{a['defender_th']}"
                if a
                else ("missed" if r["state"] == "warEnded" else "—")
            )
            dfn = f"gave {d['stars']}★ {d['destruction']:.0f}%" if d else "not attacked"
            lines.append(
                f"R{r['cwl_round'] or '?'} vs {discord.utils.escape_markdown(r['opponent_name'] or '?')}"
                f" · {r['name']} (TH{r['town_hall']}): {att} · def {dfn}"
            )
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title="CWL history",
            description="\n".join(lines)[:4096] or "No CWL wars recorded for these accounts.",
        )
        await interaction.response.send_message(embed=e)


async def setup(bot) -> None:
    await bot.add_cog(CWL(bot))
