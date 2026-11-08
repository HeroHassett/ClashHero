"""Log feeds: configuration commands plus listeners that render collector events into the
channels subscribed via ``clan_logs`` / ``server_logs``.

Legend feeds (legend_battles, legend_daily) are rendered by the legends cog.
"""

from __future__ import annotations

import logging
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands, tasks

from clashhero import events as ev
from clashhero.cogs import _log_embeds as E
from clashhero.cogs._base import BaseCog
from clashhero.constants import CLAN_LOG_TYPES, SERVER_LOG_TYPES
from clashhero.services.outbox import send_clan_log, send_to_channel
from clashhero.utils.checks import manager
from clashhero.utils.fmt import make_embed
from clashhero.utils.resolve import ResolveError, clan_autocomplete, resolve_clan
from clashhero.utils.seasons import game_season

log = logging.getLogger(__name__)

CLAN_LOG_CHOICES = [app_commands.Choice(name=label[:100], value=key) for key, label in CLAN_LOG_TYPES.items()]
SERVER_LOG_CHOICES = [
    app_commands.Choice(name=label[:100], value=key) for key, label in SERVER_LOG_TYPES.items()
]


def parse_color(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return int(raw.strip().lstrip("#").removeprefix("0x"), 16) & 0xFFFFFF
    except ValueError as e:
        raise ResolveError(f"`{raw}` isn't a hex color like `#00ff88`.") from e


class Logs(BaseCog):
    logs = app_commands.Group(name="logs", description="Configure log feeds", guild_only=True)

    def __init__(self, bot) -> None:
        super().__init__(bot)
        # clan_tag -> player_tag -> [name, donated, received]
        self._donations: dict[str, dict[str, list]] = defaultdict(dict)
        # clan_tag -> player_tag -> [name, gained, total]
        self._games: dict[str, dict[str, list]] = defaultdict(dict)
        self._clan_names: dict[str, str] = {}

    async def cog_load(self) -> None:
        self.flush_batches.start()

    async def cog_unload(self) -> None:
        self.flush_batches.cancel()

    # ------------------------------------------------------------------ commands

    @logs.command(name="clan", description="Enable or disable a clan log in a channel")
    @app_commands.describe(
        clan="Clan tag or alias",
        log_type="Which log",
        channel="Channel to post in (omit with disable=True to turn off)",
        ping_role="Role to ping with each post",
        color="Embed color override, e.g. #00ff88",
        disable="Turn this log off",
    )
    @app_commands.choices(log_type=CLAN_LOG_CHOICES)
    @app_commands.autocomplete(clan=clan_autocomplete)
    @manager("bot")
    async def logs_clan(
        self,
        interaction: discord.Interaction,
        clan: str,
        log_type: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
        ping_role: discord.Role | None = None,
        color: str | None = None,
        disable: bool = False,
    ) -> None:
        tag = await resolve_clan(interaction, clan)
        if disable:
            await self.db.set_clan_log(interaction.guild_id, tag, log_type.value, None)
            await interaction.response.send_message(
                f"Disabled **{log_type.name}** for `{tag}`.", ephemeral=True
            )
            return
        target = channel or interaction.channel
        await self.db.set_clan_log(
            interaction.guild_id,
            tag,
            log_type.value,
            target.id,
            ping_role.id if ping_role else None,
            parse_color(color),
        )
        await interaction.response.send_message(
            f"**{log_type.name}** for `{tag}` will post in {target.mention}.", ephemeral=True
        )

    @logs.command(name="server", description="Enable or disable a server-wide log")
    @app_commands.choices(log_type=SERVER_LOG_CHOICES)
    @manager("bot")
    async def logs_server(
        self,
        interaction: discord.Interaction,
        log_type: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
        disable: bool = False,
    ) -> None:
        if disable:
            await self.db.set_server_log(interaction.guild_id, log_type.value, None)
            await interaction.response.send_message(f"Disabled **{log_type.name}**.", ephemeral=True)
            return
        target = channel or interaction.channel
        await self.db.set_server_log(interaction.guild_id, log_type.value, target.id)
        await interaction.response.send_message(
            f"**{log_type.name}** will post in {target.mention}.", ephemeral=True
        )

    @logs.command(name="list", description="Show configured logs")
    async def logs_list(self, interaction: discord.Interaction) -> None:
        clan_rows = await self.db.get_clan_logs(interaction.guild_id)
        server_rows = await self.db.fetchall(
            "SELECT * FROM server_logs WHERE guild_id = ? ORDER BY log_type", (interaction.guild_id,)
        )
        lines = []
        for r in clan_rows:
            ping = f" ping <@&{r['ping_role_id']}>" if r["ping_role_id"] else ""
            lines.append(
                f"`{r['clan_tag']}` {CLAN_LOG_TYPES.get(r['log_type'], r['log_type'])} "
                f"→ <#{r['channel_id']}>{ping}"
            )
        for r in server_rows:
            lines.append(
                f"Server: {SERVER_LOG_TYPES.get(r['log_type'], r['log_type'])} → <#{r['channel_id']}>"
            )
        embed = await make_embed(
            self.bot,
            interaction.guild_id,
            title="Configured logs",
            description="\n".join(lines)[:4000] or "No logs configured. Use `/logs clan`.",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ batching

    @tasks.loop(seconds=60)
    async def flush_batches(self) -> None:
        donations, self._donations = self._donations, defaultdict(dict)
        games, self._games = self._games, defaultdict(dict)
        for clan_tag, by_player in donations.items():
            entries = [tuple(v) for v in by_player.values()]
            if entries:
                await send_clan_log(
                    self.bot,
                    clan_tag,
                    "donations",
                    E.donations_embed(self._clan_names.get(clan_tag, clan_tag), entries),
                )
        for clan_tag, by_player in games.items():
            entries = [tuple(v) for v in by_player.values()]
            if entries:
                await send_clan_log(
                    self.bot,
                    clan_tag,
                    "clan_games",
                    E.clan_games_progress_embed(self._clan_names.get(clan_tag, clan_tag), entries),
                )

    @flush_batches.error
    async def _flush_error(self, error: BaseException) -> None:
        log.exception("Log batch flush failed", exc_info=error)
        self.flush_batches.restart()

    async def _clan_name(self, clan_tag: str) -> str:
        if clan_tag not in self._clan_names:
            row = await self.db.get_clan_row(clan_tag)
            self._clan_names[clan_tag] = row["name"] if row else clan_tag
        return self._clan_names[clan_tag]

    # ------------------------------------------------------------------ clan listeners

    @commands.Cog.listener()
    async def on_clash_clan_change(self, e: ev.ClanAttributeChanged) -> None:
        await send_clan_log(
            self.bot,
            e.clan_tag,
            "clan_achievements",
            E.clan_attribute_embed(e.clan_name, e.clan_tag, e.attribute, e.old, e.new),
        )

    @commands.Cog.listener()
    async def on_clash_donations(self, e: ev.DonationsChanged) -> None:
        self._clan_names[e.clan_tag] = e.clan_name
        entry = self._donations[e.clan_tag].setdefault(e.player_tag, [e.name, 0, 0])
        entry[0] = e.name
        entry[1] += e.donated
        entry[2] += e.received

    @commands.Cog.listener()
    async def on_clash_clan_games(self, e: ev.ClanGamesProgress) -> None:
        if not e.clan_tag:
            return
        await self._clan_name(e.clan_tag)
        entry = self._games[e.clan_tag].setdefault(e.player_tag, [e.name, 0, 0])
        entry[0] = e.name
        entry[1] += e.points
        entry[2] = e.total

    @commands.Cog.listener()
    async def on_clash_member_join(self, e: ev.MemberJoined) -> None:
        row = await self.db.get_player_row(e.member.tag)
        await send_clan_log(
            self.bot,
            e.clan_tag,
            "join_leave",
            E.member_join_embed(e.clan_name, e.member, e.member_count, row),
        )

    @commands.Cog.listener()
    async def on_clash_member_leave(self, e: ev.MemberLeft) -> None:
        donated = await self.db.fetchval(
            "SELECT donations FROM season_stats WHERE season_id = ? AND player_tag = ? AND clan_tag = ?",
            (game_season().id, e.member.tag, e.clan_tag),
            0,
        )
        await send_clan_log(
            self.bot,
            e.clan_tag,
            "join_leave",
            E.member_leave_embed(e.clan_name, e.member, e.member_count, donated),
        )

    @commands.Cog.listener()
    async def on_clash_role_change(self, e: ev.RoleChanged) -> None:
        await send_clan_log(
            self.bot,
            e.clan_tag,
            "role_change",
            E.role_change_embed(e.clan_name, e.name, e.player_tag, e.old_role, e.new_role),
        )

    @commands.Cog.listener()
    async def on_clash_name_change(self, e: ev.NameChanged) -> None:
        if e.clan_tag:
            await send_clan_log(
                self.bot, e.clan_tag, "name_change", E.name_change_embed(e.player_tag, e.old_name, e.new_name)
            )

    @commands.Cog.listener()
    async def on_clash_town_hall_upgrade(self, e: ev.TownHallUpgraded) -> None:
        if e.clan_tag:
            await send_clan_log(
                self.bot,
                e.clan_tag,
                "town_hall",
                E.town_hall_embed(e.name, e.player_tag, e.old_level, e.new_level),
            )

    @commands.Cog.listener()
    async def on_clash_war_preference(self, e: ev.WarPreferenceChanged) -> None:
        if e.clan_tag:
            await send_clan_log(
                self.bot,
                e.clan_tag,
                "war_preference",
                E.war_preference_embed(e.name, e.player_tag, e.opted_in),
            )

    @commands.Cog.listener()
    async def on_clash_league_change(self, e: ev.LeagueChanged) -> None:
        if e.clan_tag:
            await send_clan_log(
                self.bot,
                e.clan_tag,
                "league_change",
                E.league_change_embed(e.name, e.player_tag, e.old_league, e.new_league, e.trophies),
            )

    # ------------------------------------------------------------------ war listeners

    @commands.Cog.listener()
    async def on_clash_war_attack(self, e: ev.WarAttackMade) -> None:
        log_type = "cwl_attacks" if e.war.get("war_type") == "cwl" else "war_attacks"
        await send_clan_log(
            self.bot,
            e.clan_tag,
            log_type,
            E.war_attack_embed(e.war, e.attack, e.attacker_name, e.defender_name),
        )

    @commands.Cog.listener()
    async def on_clash_war_state(self, e: ev.WarStateChanged) -> None:
        await send_clan_log(self.bot, e.clan_tag, "war_state", E.war_state_embed(e.war, e.new_state))
        if e.new_state != "warEnded" or e.old_state is None:
            return
        members = await self.db.fetchall("SELECT * FROM war_members WHERE war_id = ?", (e.war["id"],))
        attacks = await self.db.fetchall("SELECT * FROM war_attacks WHERE war_id = ?", (e.war["id"],))
        per = e.war.get("attacks_per_member") or (1 if e.war.get("war_type") == "cwl" else 2)
        missed = E.missed_attacks(members, attacks, per)
        log_type = "cwl_missed" if e.war.get("war_type") == "cwl" else "war_missed"
        await send_clan_log(self.bot, e.clan_tag, log_type, E.missed_attacks_embed(e.war, missed, per))

    @commands.Cog.listener()
    async def on_clash_cwl_lineup_change(self, e: ev.CWLLineupChanged) -> None:
        async def names(tags: list[str]) -> list[str]:
            out = []
            for t in tags:
                row = await self.db.get_player_row(t)
                out.append(f"{row['name']} (TH{row['town_hall']})" if row else t)
            return out

        await send_clan_log(
            self.bot,
            e.clan_tag,
            "cwl_lineup_change",
            E.cwl_lineup_embed(e.season, await names(e.added), await names(e.removed)),
        )

    # ------------------------------------------------------------------ capital listeners

    @commands.Cog.listener()
    async def on_clash_capital_contribution(self, e: ev.CapitalContribution) -> None:
        if e.clan_tag:
            await send_clan_log(
                self.bot,
                e.clan_tag,
                "capital_contributions",
                E.capital_contribution_embed(e.name, e.player_tag, e.amount),
            )

    @commands.Cog.listener()
    async def on_clash_raid_attack(self, e: ev.RaidAttacksMade) -> None:
        await send_clan_log(
            self.bot,
            e.clan_tag,
            "capital_raids",
            E.raid_attack_embed(e.name, e.attacks, e.loot, e.total_attacks, e.attack_limit),
        )

    @commands.Cog.listener()
    async def on_clash_raid_end(self, e: ev.RaidWeekendEnded) -> None:
        season = await self.db.fetchone(
            "SELECT * FROM raid_seasons WHERE clan_tag = ? AND start_time = ?", (e.clan_tag, e.start_time)
        )
        members = await self.db.fetchall(
            "SELECT * FROM raid_members WHERE clan_tag = ? AND start_time = ?", (e.clan_tag, e.start_time)
        )
        await send_clan_log(
            self.bot,
            e.clan_tag,
            "capital_summary",
            E.raid_summary_embed(await self._clan_name(e.clan_tag), season, members),
        )

    # ------------------------------------------------------------------ server listeners

    @commands.Cog.listener()
    async def on_clash_maintenance(self, e: ev.MaintenanceChanged) -> None:
        embed = E.maintenance_embed(e.started, e.at)
        for row in await self.db.server_log_targets("maintenance"):
            await send_to_channel(self.bot, row["channel_id"], embed=embed)


async def setup(bot) -> None:
    await bot.add_cog(Logs(bot))
