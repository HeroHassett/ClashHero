"""Clan War League views and season stats.

Live views (round, lineup, group standings, TH spread) query the league group on demand;
season stats and history come from wars the collector stored.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import coc
import discord
from discord import app_commands

from clashhero.cogs._base import BaseCog
from clashhero.cogs._war_embeds import (
    add_fields,
    cwl_attacks_summary,
    cwl_group_fields,
    cwl_member_table,
    cwl_stars_summary,
    escape,
    fill_cwl_history,
    lineup_embed,
    resolve_player_tags,
    set_clan,
    side_badge,
    standings_description,
    state_value,
    war_overview_embed,
)
from clashhero.repos import war as war_repo
from clashhero.services import cards
from clashhero.utils.fmt import guild_color, make_embed, player_link
from clashhero.utils.resolve import ResolveError, clan_autocomplete, player_autocomplete, resolve_clan
from clashhero.utils.seasons import cwl_season_id
from clashhero.utils.style import ICONS

log = logging.getLogger(__name__)

SEASON_RE = re.compile(r"^\d{4}-\d{2}$")
NOT_STARTED = ("preparation", "notInWar", "None")


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


def season_title(season: str | None) -> str:
    """``2026-11`` -> ``November 2026``."""
    try:
        return datetime.strptime(season or "", "%Y-%m").strftime("%B %Y")
    except ValueError:
        return season or "?"


def current_round(rounds: Sequence[Sequence[dict]]) -> int:
    """Latest round (1-based) with a war that has started, 0 before the first battle day."""
    return max(
        (i + 1 for i, wars in enumerate(rounds) if any(w.get("state") not in NOT_STARTED for w in wars)),
        default=0,
    )


def cwl_movement(rounds: Sequence[Sequence[dict]]) -> dict[str, str]:
    """'up' / 'down' / 'same' per clan tag: its rank after the latest finished round against its
    rank one round earlier (cwl_standings order). Clans missing from either are left out; with
    fewer than two finished rounds there is nothing to compare."""
    finished = [
        i for i, wars in enumerate(rounds) if wars and all(w.get("state") == "warEnded" for w in wars)
    ]
    if not finished or finished[-1] < 1:
        return {}
    last = finished[-1]

    def ranks(upto: int) -> dict[str, int]:
        wars = [w for rnd in rounds[:upto] for w in rnd]
        return {s.tag: i for i, s in enumerate(war_repo.cwl_standings(wars))}

    now, before = ranks(last + 1), ranks(last)
    out = {}
    for tag, rank in now.items():
        if tag in before:
            out[tag] = "up" if rank < before[tag] else "down" if rank > before[tag] else "same"
    return out


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

    async def _with_clan(self, e: discord.Embed, clan_tag: str) -> discord.Embed:
        """The clan (badge and name, from the collector's copy) on ``e``."""
        row = await self.db.get_clan_row(clan_tag)
        return set_clan(e, row["name"] if row else clan_tag, clan_tag, row["badge_url"] if row else None)

    @staticmethod
    def _group_badge(group: Any, clan_tag: str) -> tuple[str | None, str | None]:
        """(name, badge URL) of a clan in the league group."""
        clan = next((c for c in group.clans if c.tag == clan_tag), None)
        return getattr(clan, "name", None), side_badge(clan)

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
        color = await guild_color(self.bot, interaction.guild_id)
        await interaction.followup.send(embed=war_overview_embed(w, tag, color, label=f"CWL round {r}"))

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
        color = await guild_color(self.bot, interaction.guild_id)
        await interaction.followup.send(embed=lineup_embed(w, tag, color, label=f"CWL round {r}"))

    @cwl.command(name="stats", description="Group standings (stars incl. +10 per win, destruction)")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def stats(self, interaction: discord.Interaction, clan: str | None = None) -> None:
        tag = await resolve_clan(interaction, clan, must_be_tracked=False)
        await interaction.response.defer()
        group = await self._group(tag)
        round_tags = self._round_tags(group)
        round_of = {t: i for i, rnd in enumerate(round_tags) for t in rnd}
        by_round: list[list[dict]] = [[] for _ in round_tags]
        wars = []
        for w in await self._round_wars(tag, [t for rnd in round_tags for t in rnd]):
            row = war_to_standing_row(w)
            wars.append(row)
            if (i := round_of.get(getattr(w, "war_tag", None))) is not None:
                by_round[i].append(row)
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
        round_no, rounds = max(current_round(by_round), 1), group.number_of_rounds or 7
        round_label = f"{round_no}/{rounds}"
        movement = cwl_movement(by_round)
        try:
            league = await self._war_league(tag)
        except Exception:  # the standings still go out without the league
            log.exception("Couldn't look up the war league of %s", tag)
            league = None
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title=f"CWL standings · {season_title(group.season)}",
            description=standings_description(
                rows, tag, round_no=round_no, rounds=rounds, league=league, movement=movement
            ),
        )
        name, badge = self._group_badge(group, tag)
        set_clan(e, name, tag, badge)
        card = await self._standings_card(group, tag, rows, movement, round_label, league)
        if card is None:
            await interaction.followup.send(embed=e)
            return
        e.set_image(url="attachment://cwl.png")
        await interaction.followup.send(embed=e, file=discord.File(io.BytesIO(card), filename="cwl.png"))

    async def _war_league(self, clan_tag: str) -> str | None:
        """The clan's war league name, from the collector's copy or else the API."""
        row = await self.db.get_clan_row(clan_tag)
        name = row["war_league"] if row else None
        if not name:
            try:
                clan = await self.coc.get_clan(clan_tag)
            except (coc.ClashOfClansException, TimeoutError):
                return None
            name = getattr(getattr(clan, "war_league", None), "name", None)
        return None if not name or name == "Unranked" else name

    async def _standings_card(
        self,
        group: Any,
        clan_tag: str,
        rows: Sequence[Any],
        movement: dict[str, str],
        round_label: str,
        league: str | None,
    ) -> bytes | None:
        """The standings as a PNG image, or None if it can't be drawn (the text embed still goes out)."""
        try:
            badge_urls = {}
            for c in group.clans:
                badge = getattr(c, "badge", None)
                badge_urls[c.tag] = getattr(badge, "medium", None) or getattr(badge, "url", None)
            badges = await cards.fetch_badges(badge_urls.values())
            card_rows = [
                cards.CardRow(
                    rank=i + 1,
                    movement=movement.get(s.tag, "same"),
                    name=s.name,
                    badge=badges.get(badge_urls.get(s.tag) or ""),
                    stars=s.total_stars,
                    destruction=s.destruction,
                    tag=s.tag,
                )
                for i, s in enumerate(rows)
            ]
            return await asyncio.to_thread(
                cards.render_cwl_standings,
                season=season_title(group.season),
                round_label=round_label,
                league=league,
                rows=card_rows,
                highlight=clan_tag,
            )
        except Exception:
            log.exception("Couldn't draw the CWL standings card for %s", clan_tag)
            return None

    @cwl.command(name="members", description="Town Hall spread of every clan in the CWL group")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def members(self, interaction: discord.Interaction, clan: str | None = None) -> None:
        tag = await resolve_clan(interaction, clan, must_be_tracked=False)
        await interaction.response.defer()
        group = await self._group(tag)
        e = await make_embed(
            self.bot, interaction.guild_id, title=f"CWL group · {season_title(group.season)}"
        )
        name, badge = self._group_badge(group, tag)
        set_clan(e, name, tag, badge)
        add_fields(e, cwl_group_fields(group.clans, tag))
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
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title=f"CWL attacks · {season_title(s)}",
            description=(
                cwl_attacks_summary(rows) + "\n" + cwl_member_table(rows, missed=True)
                if rows
                else "No CWL data recorded for this season."
            ),
        )
        await interaction.response.send_message(embed=await self._with_clan(e, tag))

    @cwl.command(name="stars", description="Season star leaderboard for the clan's CWL")
    @app_commands.describe(season="CWL season like 2026-11 (default: current)")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def stars_(
        self, interaction: discord.Interaction, clan: str | None = None, season: str | None = None
    ) -> None:
        tag = await resolve_clan(interaction, clan)
        s = _season(season)
        rows = await war_repo.cwl_member_stats(self.db, tag, s)
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title=f"CWL stars · {season_title(s)}",
            description=(
                cwl_stars_summary(rows) + "\n" + cwl_member_table(rows)
                if rows
                else "No CWL data recorded for this season."
            ),
        )
        await interaction.response.send_message(embed=await self._with_clan(e, tag))

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
        if len(tags) == 1:
            latest = {r["player_tag"]: r["name"] for r in reversed(rows)}  # rows are newest first
            names = latest or await war_repo.player_names(self.db, tags)
            title, url = f"{escape(names.get(tags[0], tags[0]))} ({tags[0]})", player_link(tags[0])
        else:
            title, url = f"{escape((user or interaction.user).display_name)} ({len(tags)} accounts)", None
        which = season_title(_season(season)) if season else "every recorded season"
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title=title[:256],
            url=url,
            description=f"{ICONS['cwl']} **CWL history** · {which}",
        )
        await interaction.response.send_message(embed=fill_cwl_history(e, rows))


async def setup(bot) -> None:
    await bot.add_cog(CWL(bot))
