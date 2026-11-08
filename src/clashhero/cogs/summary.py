"""Family-wide summaries across every clan linked to the server."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime

import coc
import discord
from discord import app_commands

from clashhero.cogs._base import BaseCog
from clashhero.cogs._info_embeds import short
from clashhero.cogs.info import SEASON_CHOICES, season_window
from clashhero.repos import stats
from clashhero.utils.fmt import make_embed, table, ts
from clashhero.utils.resolve import resolve_clans
from clashhero.utils.seasons import clan_games, cwl_season_id

STATE_LABELS = {"preparation": "Preparation", "inWar": "Battle day", "warEnded": "Ended"}


def clan_names(rows: Sequence) -> dict[str, str]:
    return {r["clan_tag"]: r["name"] or r["clan_tag"] for r in rows}


def missed_by_clan(rows: Sequence) -> dict[str, dict[str, int]]:
    """{clan_tag: {"attacks": n, "players": n, "wars": n}} from stats.missed_attacks rows."""
    out: dict[str, dict] = defaultdict(lambda: {"attacks": 0, "players": set(), "wars": set()})
    for r in rows:
        d = out[r["clan_tag"]]
        d["attacks"] += r["missed"]
        d["players"].add(r["player_tag"])
        d["wars"].add(r["war_id"])
    return {
        k: {"attacks": v["attacks"], "players": len(v["players"]), "wars": len(v["wars"])}
        for k, v in out.items()
    }


def missed_by_player(rows: Sequence) -> list[tuple[str, str, int, int]]:
    """[(name, tag, missed attacks, wars)] sorted worst first."""
    acc: dict[str, list] = {}
    for r in rows:
        a = acc.setdefault(r["player_tag"], [r["name"], r["player_tag"], 0, 0])
        a[2] += r["missed"]
        a[3] += 1
    return sorted((tuple(v) for v in acc.values()), key=lambda x: (-x[2], x[0]))


def win_rate(wins: int, total: int) -> str:
    return f"{wins * 100 / total:.0f}%" if total else "—"


class Summary(BaseCog):
    """Family-wide summaries."""

    summary = app_commands.Group(
        name="summary", description="Summaries across all of this server's clans", guild_only=True
    )

    async def _ctx(self, interaction: discord.Interaction, clans: str | None):
        tags = await resolve_clans(interaction, clans)
        names = clan_names(await self.db.get_guild_clan_rows(interaction.guild_id))
        return tags, names

    async def _send(self, interaction: discord.Interaction, title: str, body: str, footer: str | None = None):
        e = await make_embed(self.bot, interaction.guild_id, title=title, description=body[:4096])
        if footer:
            e.set_footer(text=footer)
        if interaction.response.is_done():
            await interaction.followup.send(embed=e)
        else:
            await interaction.response.send_message(embed=e)

    @summary.command(name="donations", description="Season donations per clan and top donors")
    @app_commands.choices(season=SEASON_CHOICES)
    @app_commands.describe(clans="Tags/aliases separated by spaces (default: all)")
    async def donations(
        self,
        interaction: discord.Interaction,
        season: app_commands.Choice[str] | None = None,
        clans: str | None = None,
    ) -> None:
        tags, names = await self._ctx(interaction, clans)
        w = season_window(season)
        per_clan = await stats.season_totals_by_clan(self.db, tags, w.id)
        top = await stats.top_season_players(self.db, tags, w.id, "donations", 15)
        body = table(
            [
                (
                    short(names.get(r["clan_tag"], r["clan_tag"]), 16),
                    f"{r['donations']:,}",
                    f"{r['received']:,}",
                )
                for r in per_clan
            ],
            ["CLAN", "DONATED", "RECEIVED"],
        )
        body += "**Top donors**\n" + table(
            [(i + 1, f"{r['value']:,}", short(r["name"])) for i, r in enumerate(top)], ["#", "DON", "NAME"]
        )
        await self._send(interaction, "Donations summary", body, f"Season ending {w.id}")

    @summary.command(name="trophies", description="Trophy totals per clan and top players")
    async def trophies(self, interaction: discord.Interaction, clans: str | None = None) -> None:
        tags, names = await self._ctx(interaction, clans)
        per_clan = await stats.clan_trophy_summary(self.db, tags)
        top = await stats.top_trophy_players(self.db, tags, 15)
        body = table(
            [
                (
                    short(r["name"] or r["clan_tag"], 16),
                    r["members"],
                    f"{r['total']:,}",
                    round(r["average"] or 0),
                )
                for r in per_clan
            ],
            ["CLAN", "MEM", "TOTAL", "AVG"],
        )
        body += "**Top players**\n" + table(
            [(i + 1, r["trophies"], r["town_hall"] or "?", short(r["name"])) for i, r in enumerate(top)],
            ["#", "CUPS", "TH", "NAME"],
        )
        await self._send(interaction, "Trophy summary", body)

    @summary.command(name="wars", description="Current war status for every clan")
    async def wars(self, interaction: discord.Interaction, clans: str | None = None) -> None:
        tags, names = await self._ctx(interaction, clans)
        rows = {r["clan_tag"]: r for r in await stats.current_wars(self.db, tags)}
        lines = []
        for t in tags:
            w = rows.get(t)
            name = names.get(t, t)
            if not w:
                lines.append(f"**{name}** · not in war")
                continue
            kind = "CWL" if w["war_type"] == "cwl" else w["war_type"].title()
            when = ts(w["end_time"]) if w["state"] == "inWar" else ts(w["start_time"])
            label = "ends" if w["state"] == "inWar" else "starts"
            lines.append(
                f"**{name}** vs **{w['opponent_name']}** ({kind} {w['team_size']}v{w['team_size']})\n"
                f"{STATE_LABELS.get(w['state'], w['state'])} · ⭐ {w['clan_stars']}–{w['opponent_stars']} · "
                f"{w['clan_destruction']:.1f}%–{w['opponent_destruction']:.1f}% · {label} {when}"
            )
        await self._send(interaction, "Current wars", "\n\n".join(lines) or "No clans.")

    @summary.command(name="war-results", description="Season war wins/losses per clan")
    @app_commands.choices(season=SEASON_CHOICES)
    async def war_results(
        self,
        interaction: discord.Interaction,
        season: app_commands.Choice[str] | None = None,
        clans: str | None = None,
    ) -> None:
        tags, names = await self._ctx(interaction, clans)
        w = season_window(season)
        rows = await stats.war_results(self.db, tags, w.start, w.end)
        body = table(
            [
                (
                    short(r["clan_name"] or r["clan_tag"], 16),
                    r["wins"],
                    r["losses"],
                    r["ties"],
                    win_rate(r["wins"], r["total"]),
                )
                for r in rows
            ],
            ["CLAN", "W", "L", "T", "WIN%"],
        )
        await self._send(interaction, "War results", body, f"Wars started in season ending {w.id}")

    @summary.command(name="missed-wars", description="Missed war attacks this season, per clan and player")
    @app_commands.choices(season=SEASON_CHOICES)
    async def missed_wars(
        self,
        interaction: discord.Interaction,
        season: app_commands.Choice[str] | None = None,
        clans: str | None = None,
    ) -> None:
        tags, names = await self._ctx(interaction, clans)
        w = season_window(season)
        rows = await stats.missed_attacks(self.db, tags, w.start, w.end)
        per_clan = missed_by_clan(rows)
        body = table(
            [
                (short(names.get(t, t), 16), d["attacks"], d["players"], d["wars"])
                for t, d in sorted(per_clan.items(), key=lambda kv: -kv[1]["attacks"])
            ],
            ["CLAN", "MISSED", "PLAYERS", "WARS"],
        )
        body += "**Players**\n" + table(
            [(m, wars, short(n)) for n, _, m, wars in missed_by_player(rows)[:25]], ["MISSED", "WARS", "NAME"]
        )
        await self._send(interaction, "Missed attacks", body, f"Season ending {w.id}")

    @summary.command(name="cwl-status", description="CWL league and current status for every clan")
    async def cwl_status(self, interaction: discord.Interaction, clans: str | None = None) -> None:
        tags, names = await self._ctx(interaction, clans)
        await interaction.response.defer()
        lines = []
        for t in tags:
            row = await self.db.get_clan_row(t)
            league = (row["war_league"] if row else None) or "Unranked"
            try:
                group = await self.coc.get_league_group(t)
                state = str(getattr(group.state, "value", group.state))
                status = {"preparation": "Preparation", "inWar": "In progress", "ended": "Ended"}.get(
                    state, state
                )
                rounds = sum(1 for r in (group.rounds or []) if r and r[0] != "#0")
                status += f" · round {rounds}/{group.number_of_rounds}"
            except coc.NotFound:
                status = "Not spun / not in CWL"
            except coc.PrivateWarLog:
                status = "War log private"
            lines.append(f"**{names.get(t, t)}** · {league}\n{status}")
        await self._send(interaction, f"CWL status · {cwl_season_id()}", "\n\n".join(lines))

    @summary.command(name="clan-games", description="Clan Games points per clan and top scorers")
    async def clan_games_summary(self, interaction: discord.Interaction, clans: str | None = None) -> None:
        tags, names = await self._ctx(interaction, clans)
        games = clan_games(datetime.now(UTC))
        per_clan = await stats.clan_games_by_clan(self.db, tags, games.id)
        top = await stats.clan_games_players(self.db, tags, games.id, 15)
        body = table(
            [
                (
                    short(r["clan_name"] or r["clan_tag"], 16),
                    f"{r['points']:,}",
                    r["participants"],
                    r["maxed"],
                )
                for r in per_clan
            ],
            ["CLAN", "POINTS", "PLAYERS", "MAXED"],
        )
        body += "**Top scorers**\n" + table(
            [(i + 1, r["points"], short(r["name"])) for i, r in enumerate(top)], ["#", "PTS", "NAME"]
        )
        body += f"\n{ts(games.start, 'D')} → {ts(games.end, 'D')}"
        await self._send(interaction, f"Clan Games · {games.id}", body)

    @summary.command(name="capital", description="Last raid weekend results per clan")
    async def capital(self, interaction: discord.Interaction, clans: str | None = None) -> None:
        tags, names = await self._ctx(interaction, clans)
        rows = await stats.latest_raids(self.db, tags)
        body = table(
            [
                (
                    short(r["clan_name"] or r["clan_tag"], 16),
                    f"{r['total_loot'] or 0:,}",
                    r["member_attacks"] or 0,
                    r["participants"],
                    r["districts_destroyed"] or 0,
                )
                for r in rows
            ],
            ["CLAN", "LOOT", "ATK", "PLAYERS", "DIST"],
        )
        await self._send(interaction, "Capital raids (latest weekend)", body)

    @summary.command(name="activity", description="Most active players this season")
    @app_commands.choices(season=SEASON_CHOICES)
    async def activity(
        self,
        interaction: discord.Interaction,
        season: app_commands.Choice[str] | None = None,
        clans: str | None = None,
    ) -> None:
        tags, names = await self._ctx(interaction, clans)
        w = season_window(season)
        per_clan = await stats.season_totals_by_clan(self.db, tags, w.id)
        top = await stats.top_season_players(self.db, tags, w.id, "activity", 20)
        body = table(
            [
                (short(names.get(r["clan_tag"], r["clan_tag"]), 16), r["activity"])
                for r in sorted(per_clan, key=lambda r: -(r["activity"] or 0))
            ],
            ["CLAN", "ACTIVITY"],
        )
        body += "**Most active**\n" + table(
            [(i + 1, r["value"], short(r["name"])) for i, r in enumerate(top)], ["#", "SCORE", "NAME"]
        )
        await self._send(interaction, "Activity", body, f"Season ending {w.id}")

    @summary.command(name="best", description="Top performers this season across categories")
    @app_commands.choices(season=SEASON_CHOICES)
    async def best(
        self,
        interaction: discord.Interaction,
        season: app_commands.Choice[str] | None = None,
        clans: str | None = None,
    ) -> None:
        tags, _ = await self._ctx(interaction, clans)
        w = season_window(season)
        e = await make_embed(self.bot, interaction.guild_id, title="Best performers")
        sections = [
            ("Donations", await stats.top_season_players(self.db, tags, w.id, "donations", 5)),
            ("War stars (new)", await stats.war_star_leaders(self.db, tags, w.start, w.end, 5)),
            ("Capital gold", await stats.top_season_players(self.db, tags, w.id, "capital_gold", 5)),
            ("Attack wins", await stats.top_season_players(self.db, tags, w.id, "attack_wins", 5)),
            ("Activity", await stats.top_season_players(self.db, tags, w.id, "activity", 5)),
        ]
        games = clan_games(datetime.now(UTC))
        cg = await stats.clan_games_players(self.db, tags, games.id, 5)
        sections.append(("Clan Games", [{"name": r["name"], "value": r["points"]} for r in cg]))
        for title, rows in sections:
            value = "\n".join(f"`{r['value']:>7,}` {short(r['name'], 18)}" for r in rows) or "—"
            e.add_field(name=title, value=value[:1024])
        e.set_footer(text=f"Season ending {w.id}")
        await interaction.response.send_message(embed=e)


async def setup(bot) -> None:
    await bot.add_cog(Summary(bot))
