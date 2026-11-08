"""CSV exports of collected data. Manager-only."""

from __future__ import annotations

import io
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta

import discord
from discord import app_commands

from clashhero.cogs._base import BaseCog
from clashhero.cogs._info_embeds import rows_from, to_csv
from clashhero.cogs.info import SEASON_CHOICES, season_window
from clashhero.db.database import Database
from clashhero.repos import stats
from clashhero.utils.checks import manager
from clashhero.utils.resolve import resolve_clans
from clashhero.utils.seasons import Window, clan_games

Export = tuple[str, bytes]


async def export_members(db: Database, tags: Sequence[str], window: Window) -> Export:
    cols = [
        "clan_tag",
        "player_tag",
        "name",
        "town_hall",
        "exp_level",
        "trophies",
        "best_trophies",
        "league",
        "clan_role",
        "war_stars",
        "heroes_total",
        "war_opted_in",
        "builder_hall",
        "builder_trophies",
        "last_seen",
    ]
    rows = []
    for t in tags:
        rows += rows_from(await db.clan_member_rows(t), cols)
    return "members.csv", to_csv(cols, rows)


async def export_season(db: Database, tags: Sequence[str], window: Window) -> Export:
    cols = [
        "clan_tag",
        "player_tag",
        "name",
        "town_hall",
        "donations",
        "received",
        "capital_gold",
        "attack_wins",
        "activity",
    ]
    rows = rows_from(await stats.season_stats_rows(db, tags, window.id), cols)
    return f"season-{window.id}.csv", to_csv(["season", *cols], [[window.id, *r] for r in rows])


async def export_wars(db: Database, tags: Sequence[str], window: Window) -> Export:
    cols = [
        "clan_tag",
        "opponent_tag",
        "opponent_name",
        "war_type",
        "cwl_season",
        "cwl_round",
        "state",
        "team_size",
        "preparation_start",
        "end_time",
        "clan_stars",
        "clan_destruction",
        "clan_attacks",
        "opponent_stars",
        "opponent_destruction",
        "opponent_attacks",
        "result",
    ]
    return "wars.csv", to_csv(
        cols, rows_from(await stats.wars_rows(db, tags, window.start, window.end), cols)
    )


_ATTACK_COLS = [
    "clan_tag",
    "war_type",
    "opponent_name",
    "preparation_start",
    "cwl_round",
    "attack_order",
    "attacker_tag",
    "attacker_name",
    "attacker_th",
    "attacker_pos",
    "defender_tag",
    "defender_name",
    "defender_th",
    "defender_pos",
    "stars",
    "new_stars",
    "destruction",
    "duration",
    "is_fresh",
]


async def export_attacks(db: Database, tags: Sequence[str], window: Window) -> Export:
    rows = await stats.attack_rows(db, tags, window.start, window.end)
    return "attacks.csv", to_csv(_ATTACK_COLS, rows_from(rows, _ATTACK_COLS))


async def export_cwl(db: Database, tags: Sequence[str], window: Window) -> Export:
    rows = await stats.attack_rows(db, tags, window.start, window.end, cwl_only=True)
    cols = ["cwl_season", *_ATTACK_COLS]
    return "cwl-attacks.csv", to_csv(cols, rows_from(rows, cols))


async def export_missed(db: Database, tags: Sequence[str], window: Window) -> Export:
    cols = ["clan_tag", "war_type", "opponent_name", "end_time", "player_tag", "name", "town_hall", "missed"]
    rows = await stats.missed_attacks(db, tags, window.start, window.end)
    return "missed-attacks.csv", to_csv(cols, rows_from(rows, cols))


async def export_capital(db: Database, tags: Sequence[str], window: Window) -> Export:
    cols = ["clan_tag", "start_time", "player_tag", "name", "attacks", "attack_limit", "loot"]
    rows = await stats.raid_member_rows(db, tags, window.start - timedelta(days=3))
    return "capital-raids.csv", to_csv(cols, rows_from(rows, cols))


async def export_clangames(db: Database, tags: Sequence[str], window: Window) -> Export:
    games = clan_games(datetime.now(UTC))
    cols = ["games_id", "clan_tag", "player_tag", "name", "points", "updated_at"]
    rows = await stats.clan_games_players(db, tags, games.id)
    return f"clan-games-{games.id}.csv", to_csv(cols, rows_from(rows, cols))


def user_namer(guild: discord.Guild | None) -> Callable[[int], str]:
    def name(uid: int) -> str:
        m = guild.get_member(uid) if guild else None
        return str(m) if m else ""

    return name


async def export_users(db: Database, tags: Sequence[str], window: Window, namer=None) -> Export:
    namer = namer or (lambda uid: "")
    rows = await stats.guild_user_links(db, tags)
    cols = [
        "discord_user_id",
        "discord_name",
        "player_tag",
        "name",
        "town_hall",
        "clan_tag",
        "is_default",
        "verified",
        "linked_at",
    ]
    out = [
        [
            r["discord_user_id"],
            namer(r["discord_user_id"]),
            r["player_tag"],
            r["name"],
            r["town_hall"],
            r["clan_tag"],
            r["is_default"],
            r["verified"],
            r["linked_at"],
        ]
        for r in rows
    ]
    return "users.csv", to_csv(cols, out)


EXPORTERS: dict[str, Callable[..., Awaitable[Export]]] = {
    "members": export_members,
    "season": export_season,
    "wars": export_wars,
    "attacks": export_attacks,
    "cwl": export_cwl,
    "missed": export_missed,
    "capital": export_capital,
    "clangames": export_clangames,
    "users": export_users,
}

DESCRIPTIONS = {
    "members": "Current members of your clans",
    "season": "Seasonal member stats (donations, capital gold, activity)",
    "wars": "Wars in the season with results",
    "attacks": "Every war attack made by your clans in the season",
    "cwl": "CWL attacks in the season",
    "missed": "Missed war attacks in the season",
    "capital": "Capital raid participation (season)",
    "clangames": "Clan Games points (this month)",
    "users": "Discord users and their linked accounts",
}


_CLANS_HELP = "Tags/aliases separated by spaces (default: all)"


class Exports(BaseCog, name="Export"):
    """Spreadsheet (CSV) exports."""

    export = app_commands.Group(name="export", description="Export data as CSV spreadsheets", guild_only=True)

    async def _run(
        self,
        interaction: discord.Interaction,
        kind: str,
        clans: str | None,
        season: app_commands.Choice[str] | None,
    ) -> None:
        tags = await resolve_clans(interaction, clans)
        await interaction.response.defer(ephemeral=True)
        window = season_window(season)
        fn = EXPORTERS[kind]
        if kind == "users":
            filename, data = await fn(self.db, tags, window, user_namer(interaction.guild))
        else:
            filename, data = await fn(self.db, tags, window)
        lines = data.count(b"\n") - 1
        await interaction.followup.send(
            f"{DESCRIPTIONS[kind]} · {max(lines, 0)} rows",
            file=discord.File(io.BytesIO(data), filename=filename),
            ephemeral=True,
        )

    @export.command(name="members", description=DESCRIPTIONS["members"])
    @manager("bot")
    @app_commands.choices(season=SEASON_CHOICES)
    @app_commands.describe(clans=_CLANS_HELP)
    async def cmd_members(
        self,
        interaction: discord.Interaction,
        clans: str | None = None,
        season: app_commands.Choice[str] | None = None,
    ) -> None:
        await self._run(interaction, "members", clans, season)

    @export.command(name="season", description=DESCRIPTIONS["season"])
    @manager("bot")
    @app_commands.choices(season=SEASON_CHOICES)
    @app_commands.describe(clans=_CLANS_HELP)
    async def cmd_season(
        self,
        interaction: discord.Interaction,
        clans: str | None = None,
        season: app_commands.Choice[str] | None = None,
    ) -> None:
        await self._run(interaction, "season", clans, season)

    @export.command(name="wars", description=DESCRIPTIONS["wars"])
    @manager("bot")
    @app_commands.choices(season=SEASON_CHOICES)
    @app_commands.describe(clans=_CLANS_HELP)
    async def cmd_wars(
        self,
        interaction: discord.Interaction,
        clans: str | None = None,
        season: app_commands.Choice[str] | None = None,
    ) -> None:
        await self._run(interaction, "wars", clans, season)

    @export.command(name="attacks", description=DESCRIPTIONS["attacks"])
    @manager("bot")
    @app_commands.choices(season=SEASON_CHOICES)
    @app_commands.describe(clans=_CLANS_HELP)
    async def cmd_attacks(
        self,
        interaction: discord.Interaction,
        clans: str | None = None,
        season: app_commands.Choice[str] | None = None,
    ) -> None:
        await self._run(interaction, "attacks", clans, season)

    @export.command(name="cwl", description=DESCRIPTIONS["cwl"])
    @manager("bot")
    @app_commands.choices(season=SEASON_CHOICES)
    @app_commands.describe(clans=_CLANS_HELP)
    async def cmd_cwl(
        self,
        interaction: discord.Interaction,
        clans: str | None = None,
        season: app_commands.Choice[str] | None = None,
    ) -> None:
        await self._run(interaction, "cwl", clans, season)

    @export.command(name="missed", description=DESCRIPTIONS["missed"])
    @manager("bot")
    @app_commands.choices(season=SEASON_CHOICES)
    @app_commands.describe(clans=_CLANS_HELP)
    async def cmd_missed(
        self,
        interaction: discord.Interaction,
        clans: str | None = None,
        season: app_commands.Choice[str] | None = None,
    ) -> None:
        await self._run(interaction, "missed", clans, season)

    @export.command(name="capital", description=DESCRIPTIONS["capital"])
    @manager("bot")
    @app_commands.choices(season=SEASON_CHOICES)
    @app_commands.describe(clans=_CLANS_HELP)
    async def cmd_capital(
        self,
        interaction: discord.Interaction,
        clans: str | None = None,
        season: app_commands.Choice[str] | None = None,
    ) -> None:
        await self._run(interaction, "capital", clans, season)

    @export.command(name="clangames", description=DESCRIPTIONS["clangames"])
    @manager("bot")
    @app_commands.choices(season=SEASON_CHOICES)
    @app_commands.describe(clans=_CLANS_HELP)
    async def cmd_clangames(
        self,
        interaction: discord.Interaction,
        clans: str | None = None,
        season: app_commands.Choice[str] | None = None,
    ) -> None:
        await self._run(interaction, "clangames", clans, season)

    @export.command(name="users", description=DESCRIPTIONS["users"])
    @manager("bot")
    @app_commands.choices(season=SEASON_CHOICES)
    @app_commands.describe(clans=_CLANS_HELP)
    async def cmd_users(
        self,
        interaction: discord.Interaction,
        clans: str | None = None,
        season: app_commands.Choice[str] | None = None,
    ) -> None:
        await self._run(interaction, "users", clans, season)


async def setup(bot) -> None:
    await bot.add_cog(Exports(bot))
