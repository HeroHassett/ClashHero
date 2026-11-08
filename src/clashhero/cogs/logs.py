"""Log feeds: configuration commands plus listeners that render collector events into the
channels subscribed via ``clan_logs`` / ``server_logs``.

Donations, Clan Games progress, unit upgrades and super troop boosts are batched per clan and
flushed every minute. Legend feeds (legend_battles, legend_daily) are rendered by the legends cog.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

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
from clashhero.utils.style import header, ok_embed

log = logging.getLogger(__name__)

# How long after a leave we keep polling the player (through the collector) to see where they went.
MOVE_WINDOW = timedelta(minutes=15)

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


POST_PERMISSIONS = (
    ("view_channel", "View Channel"),
    ("send_messages", "Send Messages"),
    ("embed_links", "Embed Links"),
)


def missing_post_permissions(perms: discord.Permissions) -> list[str]:
    """The permissions a log channel needs that the bot doesn't have there."""
    return [label for attr, label in POST_PERMISSIONS if not getattr(perms, attr)]


def check_can_post(channel, me) -> None:
    """Refuse a log channel the bot couldn't post in (otherwise every post would silently fail)."""
    if me is None or not hasattr(channel, "permissions_for"):
        return
    missing = missing_post_permissions(channel.permissions_for(me))
    if missing:
        raise ResolveError(
            f"I can't post in {channel.mention}: I'm missing **{', '.join(missing)}** there. "
            "Give the ClashHero role those permissions in the channel settings, then try again."
        )


def log_list_lines(clan_rows, server_rows, clan_names: dict[str, str]) -> list[str]:
    """/logs list body: one block per clan (header with name and tag), then server logs."""
    lines: list[str] = []
    current = None
    for r in clan_rows:
        tag = r["clan_tag"]
        if tag != current:
            if lines:
                lines.append("")
            lines.append(header(clan_names.get(tag) or tag, "clan", f"`{tag}`"))
            current = tag
        ping = f" · ping <@&{r['ping_role_id']}>" if r["ping_role_id"] else ""
        lines.append(f"{CLAN_LOG_TYPES.get(r['log_type'], r['log_type'])} → <#{r['channel_id']}>{ping}")
    if server_rows:
        if lines:
            lines.append("")
        lines.append(header("Server", "🖥️"))
        lines += [
            f"{SERVER_LOG_TYPES.get(r['log_type'], r['log_type'])} → <#{r['channel_id']}>"
            for r in server_rows
        ]
    return lines


def merge_unit_changes(
    acc: dict[tuple[str, str], list], changes: Iterable[tuple[str, str, int | None, int]]
) -> None:
    """Fold unit changes into ``acc`` ((kind, unit) -> [first old level, latest new level]) so
    several polls inside one batch window show up as a single "90 → 92"."""
    for kind, unit, old, new in changes:
        prev = acc.get((kind, unit))
        acc[(kind, unit)] = [prev[0] if prev else old, new]


class Logs(BaseCog):
    logs = app_commands.Group(name="logs", description="Configure log feeds", guild_only=True)

    def __init__(self, bot) -> None:
        super().__init__(bot)
        # clan_tag -> player_tag -> [name, donated, received]
        self._donations: dict[str, dict[str, list]] = defaultdict(dict)
        # clan_tag -> player_tag -> [name, gained, total]
        self._games: dict[str, dict[str, list]] = defaultdict(dict)
        # clan_tag -> player_tag -> [name, town_hall, {(kind, unit): [old, new]}]
        self._upgrades: dict[str, dict[str, list]] = defaultdict(dict)
        # clan_tag -> player_tag -> [name, [super troops]]
        self._boosts: dict[str, dict[str, list]] = defaultdict(dict)
        # player_tag -> (clan_tag, left_at) for recent leavers of clans with a join/leave log
        self._left: dict[str, tuple[str, datetime]] = {}
        self._clan_names: dict[str, str] = {}

    async def cog_load(self) -> None:
        # Leavers drop out of the collector's player set; keep them polled for a while so a
        # PlayerClanChanged tells us where they went.
        self.collector.register_player_source(self._recent_leavers)
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
        row = await self.db.get_clan_row(tag)
        label = f"**{row['name']}** `{tag}`" if row and row["name"] else f"`{tag}`"
        if disable:
            await self.db.set_clan_log(interaction.guild_id, tag, log_type.value, None)
            await interaction.response.send_message(
                embed=ok_embed(f"Disabled **{log_type.name}** for {label}."), ephemeral=True
            )
            return
        target = channel or interaction.channel
        check_can_post(target, interaction.guild and interaction.guild.me)
        await self.db.set_clan_log(
            interaction.guild_id,
            tag,
            log_type.value,
            target.id,
            ping_role.id if ping_role else None,
            parse_color(color),
        )
        ping = f", pinging {ping_role.mention}" if ping_role else ""
        await interaction.response.send_message(
            embed=ok_embed(
                f"**{log_type.name}** for {label} will post in {target.mention}{ping}.\n"
                "Nothing is posted right away: posts start with the next matching event in the "
                "game (a join or leave shows up about a minute after it happens)."
            ),
            ephemeral=True,
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
            await interaction.response.send_message(
                embed=ok_embed(f"Disabled **{log_type.name}**."), ephemeral=True
            )
            return
        target = channel or interaction.channel
        check_can_post(target, interaction.guild and interaction.guild.me)
        await self.db.set_server_log(interaction.guild_id, log_type.value, target.id)
        await interaction.response.send_message(
            embed=ok_embed(f"**{log_type.name}** will post in {target.mention}."), ephemeral=True
        )

    @logs.command(name="list", description="Show configured logs")
    async def logs_list(self, interaction: discord.Interaction) -> None:
        clan_rows = await self.db.get_clan_logs(interaction.guild_id)
        server_rows = await self.db.fetchall(
            "SELECT * FROM server_logs WHERE guild_id = ? ORDER BY log_type", (interaction.guild_id,)
        )
        names = {r["clan_tag"]: r["name"] for r in await self.db.get_guild_clan_rows(interaction.guild_id)}
        lines = log_list_lines(clan_rows, server_rows, names)
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
        await self.flush_now()

    async def flush_now(self) -> None:
        donations, self._donations = self._donations, defaultdict(dict)
        games, self._games = self._games, defaultdict(dict)
        upgrades, self._upgrades = self._upgrades, defaultdict(dict)
        boosts, self._boosts = self._boosts, defaultdict(dict)
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
        for clan_tag, by_player in upgrades.items():
            entries = [
                (name, tag, th, [(kind, unit, old, new) for (kind, unit), (old, new) in changes.items()])
                for tag, (name, th, changes) in by_player.items()
            ]
            for embed in E.upgrades_embeds(self._clan_names.get(clan_tag, clan_tag), entries):
                await send_clan_log(self.bot, clan_tag, "upgrades", embed)
        for clan_tag, by_player in boosts.items():
            entries = [(name, tag, troops) for tag, (name, troops) in by_player.items()]
            for embed in E.super_troops_embeds(self._clan_names.get(clan_tag, clan_tag), entries):
                await send_clan_log(self.bot, clan_tag, "super_troops", embed)

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
    async def on_clash_clan_settings(self, e: ev.ClanSettingsChanged) -> None:
        self._clan_names[e.clan_tag] = e.clan_name
        if e.changes:
            await send_clan_log(
                self.bot,
                e.clan_tag,
                "clan_settings",
                E.clan_settings_embed(e.clan_name, e.clan_tag, e.changes),
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
            view=E.profile_view(e.member.tag),
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
            view=E.profile_view(e.member.tag),
        )
        if await self.db.clan_log_targets(e.clan_tag, "join_leave"):
            # If a player poll already stored their new clan, the move was reported already.
            row = await self.db.get_player_row(e.member.tag)
            if row is None or row["clan_tag"] in (None, e.clan_tag):
                self._left[e.member.tag] = (e.clan_tag, datetime.now(UTC))

    async def _recent_leavers(self) -> set[str]:
        """Player source for the collector: recent leavers of clans with a join/leave log."""
        cutoff = datetime.now(UTC) - MOVE_WINDOW
        self._left = {tag: v for tag, v in self._left.items() if v[1] >= cutoff}
        return set(self._left)

    @commands.Cog.listener()
    async def on_clash_player_clan_changed(self, e: ev.PlayerClanChanged) -> None:
        """Follow up a leave with where the player went. Posts to the old clan's join/leave log;
        if the player was clanless for a poll in between, the recorded leave is used instead."""
        if not e.new_clan_tag:
            return
        recent = self._left.pop(e.player_tag, None)
        from_clan = e.old_clan_tag
        if from_clan is None and recent and recent[1] >= datetime.now(UTC) - MOVE_WINDOW:
            from_clan = recent[0]
        if not from_clan or from_clan == e.new_clan_tag:
            return
        targets = await self.db.clan_log_targets(from_clan, "join_leave")
        if not targets:
            return
        old_name = await self._clan_name(from_clan)
        own_guilds = set(await self.db.guilds_for_clan(e.new_clan_tag))
        view = E.profile_view(e.player_tag)
        for target in targets:
            embed = E.clan_move_embed(
                e.name,
                e.player_tag,
                old_name,
                e.new_clan_name,
                e.new_clan_tag,
                own_clan=target["guild_id"] in own_guilds,
            )
            # Same color/ping handling as outbox.send_clan_log, but the embed differs per guild.
            if target["color"] is not None:
                embed.color = discord.Color(target["color"])
            ping = f"<@&{target['ping_role_id']}>" if target["ping_role_id"] else None
            await send_to_channel(self.bot, target["channel_id"], content=ping, embed=embed, view=view)

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
    async def on_clash_units_upgraded(self, e: ev.UnitsUpgraded) -> None:
        if not e.clan_tag or not e.changes:
            return
        await self._clan_name(e.clan_tag)
        entry = self._upgrades[e.clan_tag].setdefault(e.player_tag, [e.name, e.town_hall, {}])
        entry[0], entry[1] = e.name, e.town_hall
        merge_unit_changes(entry[2], e.changes)

    @commands.Cog.listener()
    async def on_clash_super_troops_boosted(self, e: ev.SuperTroopsBoosted) -> None:
        if not e.clan_tag or not e.troops:
            return
        await self._clan_name(e.clan_tag)
        entry = self._boosts[e.clan_tag].setdefault(e.player_tag, [e.name, []])
        entry[0] = e.name
        entry[1].extend(t for t in e.troops if t not in entry[1])

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
        embed = E.maintenance_embed(e.started, e.at, e.since)
        for row in await self.db.server_log_targets("maintenance"):
            await send_to_channel(self.bot, row["channel_id"], embed=embed)


async def setup(bot) -> None:
    await bot.add_cog(Logs(bot))
