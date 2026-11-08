"""Legend / ranked tracking: battle alerts, day & season logs, leaderboards, global tops.

Per-battle alerts, a live leaderboard, day/week/season logs, history, clan boards and
global/local rankings. The collector reads ranked battles from each player's battle log
(opponent, stars, destruction, army share code; see services/battlelog.py) and falls back to
inferring them from trophy changes (services/snapshots.infer_battles), in which case only the
trophies are known. Everything here handles both.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from clashhero.cogs import _legend_embeds as E
from clashhero.cogs._base import BaseCog, send_error
from clashhero.cogs._info_embeds import summarize_army
from clashhero.cogs.army import army_embed_fields
from clashhero.events import LegendBattle
from clashhero.repos import legends as repo
from clashhero.services.battlelog import army_link
from clashhero.services.outbox import send_clan_log, send_to_channel, update_live_message
from clashhero.utils.checks import has_permission, manager
from clashhero.utils.fmt import guild_color, signed, table
from clashhero.utils.ranked import is_tracked_ranked, league_name, weekly_attack_cap
from clashhero.utils.resolve import (
    ResolveError,
    clan_autocomplete,
    parse_tag,
    player_autocomplete,
    resolve_clan,
    resolve_clans,
    resolve_player,
)
from clashhero.utils.seasons import (
    game_season,
    legend_day_bounds,
    legend_day_id,
    ranked_week,
    season_by_id,
    season_for_date,
    season_label,
)
from clashhero.utils.style import SEP, chip, fail_embed, header, ok_embed

log = logging.getLogger(__name__)

SUBS_PER_GUILD = 100
BOARD_KIND = "legend_board"
PAGE_SIZE = 25
MAX_BUTTON_URL = 512  # Discord's limit for a link button URL


def _now() -> datetime:
    return datetime.now(UTC)


async def _player_name(bot, tag: str) -> str:
    row = await bot.db.get_player_row(tag)
    return row["name"] if row else tag


def alert_line(sub, where: str) -> str:
    """One /track list row: ``` `#12` **Name** `#TAG` → DM · ⚔️ on · 🛡️ off```."""
    name = sub["name"] or "?"
    return SEP.join(
        (
            f"`#{sub['id']}` **{name}** `{sub['player_tag']}` → {where}",
            f"⚔️ {'on' if sub['attacks'] else 'off'}",
            f"🛡️ {'on' if sub['defenses'] else 'off'}",
        )
    )


async def _can_manage_sub(interaction: discord.Interaction, sub) -> bool:
    if interaction.user.id in (sub["user_id"], sub["created_by"]):
        return True
    if interaction.guild_id and interaction.guild_id == sub["guild_id"]:
        return await has_permission(interaction, "bot")
    return False


async def _day_card(bot, tag: str, day: str, guild_id: int | None) -> discord.Embed:
    stats = await repo.day_stats(bot.db, tag, day)
    row = await repo.legend_day_row(bot.db, tag, day)
    return E.day_embed(
        name=await _player_name(bot, tag),
        tag=tag,
        day=day,
        stats=stats,
        start=row["start_trophies"] if row else None,
        end=row["end_trophies"] if row else None,
        color=await guild_color(bot, guild_id),
    )


# ------------------------------------------------------------------ persistent components


class MuteAttacksButton(discord.ui.DynamicItem[discord.ui.Button], template=r"lg:mute:(?P<sub>\d+)"):
    def __init__(self, sub_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Mute attacks",
                style=discord.ButtonStyle.secondary,
                emoji="🔕",
                custom_id=f"lg:mute:{sub_id}",
            )
        )
        self.sub_id = sub_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match[str]):
        return cls(int(match["sub"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        db = interaction.client.db
        sub = await repo.get_subscription(db, self.sub_id)
        if sub is None:
            await interaction.response.send_message(
                embed=fail_embed("This alert no longer exists."), ephemeral=True
            )
            return
        if not await _can_manage_sub(interaction, sub):
            await interaction.response.send_message(
                embed=fail_embed("Only the subscriber, whoever set this up, or a manager can do that."),
                ephemeral=True,
            )
            return
        new = 0 if sub["attacks"] else 1
        await repo.update_subscription(db, self.sub_id, attacks=new)
        await interaction.response.send_message(
            embed=ok_embed(f"Attack alerts {'unmuted' if new else 'muted'} for `{sub['player_tag']}`."),
            ephemeral=True,
        )


def _copy_army_url(share_code: str | None) -> str | None:
    link = army_link(share_code)
    return link if link and len(link) <= MAX_BUTTON_URL else None


def battle_army_embed(client, battle, color: discord.Color | None = None) -> discord.Embed | None:
    """Army breakdown for a legend_events row from the battle log, or None if it can't be read."""
    link = army_link(battle["army_share_code"])
    if not link:
        return None
    coc_client = client.coc
    if not getattr(coc_client, "_static_data", None):
        coc_client._load_static()  # static game data is normally loaded at login
    try:
        summary = summarize_army(coc_client.parse_army_link(link))
    except Exception:
        log.info("Could not parse army for legend event %s", battle["id"], exc_info=True)
        return None
    fields = army_embed_fields(summary)
    if not fields:
        return None
    return E.army_embed(battle=battle, fields=fields, color=color)


class ViewArmyButton(discord.ui.DynamicItem[discord.ui.Button], template=r"lg:army:(?P<event>\d+)"):
    """Shows the army used in a battle-log battle (the player's on attacks, the attacker's on
    defenses), looked up by legend_events id."""

    def __init__(self, event_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="View army",
                style=discord.ButtonStyle.secondary,
                emoji="🪖",
                custom_id=f"lg:army:{event_id}",
            )
        )
        self.event_id = event_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match[str]):
        return cls(int(match["event"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        client = interaction.client
        battle = await repo.get_battle(client.db, self.event_id)
        if battle is None or not battle["army_share_code"]:
            await interaction.response.send_message(
                embed=fail_embed("No army is recorded for this battle."), ephemeral=True
            )
            return
        embed = battle_army_embed(client, battle, await guild_color(client, interaction.guild_id))
        if embed is None:
            await interaction.response.send_message(
                embed=fail_embed("Couldn't read this battle's army."), ephemeral=True
            )
            return
        view = discord.ui.View()
        url = _copy_army_url(battle["army_share_code"])
        if url:
            view.add_item(discord.ui.Button(label="Copy army", style=discord.ButtonStyle.link, url=url))
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


def alert_view(event: LegendBattle, sub_id: int | None = None) -> discord.ui.View | None:
    """Buttons under a battle alert: View army and Copy army (battle-log battles with an army) and
    Mute attacks (subscription alerts)."""
    view = discord.ui.View(timeout=None)
    if event.event_id and event.army_share_code:
        view.add_item(ViewArmyButton(event.event_id))
    url = _copy_army_url(event.army_share_code)
    if url:
        view.add_item(discord.ui.Button(label="Copy army", style=discord.ButtonStyle.link, url=url))
    if sub_id is not None:
        view.add_item(MuteAttacksButton(sub_id))
    return view if view.children else None


class BoardSelect(discord.ui.DynamicItem[discord.ui.Select], template=r"lg:board:(?P<guild>\d+)"):
    def __init__(self, guild_id: int, options: list[discord.SelectOption] | None = None) -> None:
        super().__init__(
            discord.ui.Select(
                custom_id=f"lg:board:{guild_id}",
                placeholder="View a player's legend day",
                options=options or [discord.SelectOption(label="—", value="none")],
            )
        )
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match[str]):
        return cls(int(match["guild"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values") or []  # type: ignore[union-attr]
        if not values or values[0] == "none":
            await interaction.response.defer()
            return
        embed = await _day_card(interaction.client, values[0], legend_day_id(), interaction.guild_id)
        await interaction.response.send_message(embed=embed, ephemeral=True)


def board_view(guild_id: int, entries: list[repo.BoardEntry]) -> discord.ui.View | None:
    if not entries:
        return None
    options = [
        discord.SelectOption(label=f"{e.name} ({e.trophies})"[:100], value=e.player_tag) for e in entries[:25]
    ]
    view = discord.ui.View(timeout=None)
    view.add_item(BoardSelect(guild_id, options))
    return view


# ------------------------------------------------------------------ ephemeral views


class OwnerView(discord.ui.View):
    def __init__(self, owner_id: int, timeout: float = 180) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=fail_embed("This menu isn't yours."), ephemeral=True
            )
            return False
        return True

    async def on_error(self, interaction, error, item) -> None:
        await send_error(interaction, error)


class AltSelectView(OwnerView):
    def __init__(self, owner_id: int, accounts: list[tuple[str, str]], render) -> None:
        super().__init__(owner_id)
        self.render = render
        select = discord.ui.Select(
            placeholder="Switch account",
            options=[discord.SelectOption(label=f"{n} ({t})"[:100], value=t) for t, n in accounts[:25]],
        )
        select.callback = self._on_select
        self.select = select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        embed = await self.render(self.select.values[0])
        await interaction.response.edit_message(embed=embed, view=self)


class Pager(OwnerView):
    def __init__(self, owner_id: int, pages: int, render: Callable[[int], Awaitable[discord.Embed]]):
        super().__init__(owner_id)
        self.page, self.pages, self.render = 0, pages, render
        self._sync()

    def _sync(self) -> None:
        self.prev.disabled = self.page <= 0
        self.next.disabled = self.page >= self.pages - 1

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        self.page -= 1
        self._sync()
        await interaction.response.edit_message(embed=await self.render(self.page), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        self.page += 1
        self._sync()
        await interaction.response.edit_message(embed=await self.render(self.page), view=self)


# ------------------------------------------------------------------ cog


DESTINATIONS = [
    app_commands.Choice(name="Direct message", value="dm"),
    app_commands.Choice(name="Channel", value="channel"),
]


class Legends(BaseCog):
    track = app_commands.Group(name="track", description="Ranked battle alerts for a player")
    legends = app_commands.Group(name="legends", description="Legend day, season and clan stats")
    legendboard = app_commands.Group(
        name="legendboard", description="Live legend leaderboard for this server", guild_only=True
    )
    top = app_commands.Group(name="top", description="Global and local leaderboards")

    def __init__(self, bot) -> None:
        super().__init__(bot)
        self._locations: list[Any] | None = None

    async def cog_load(self) -> None:
        self.collector.register_player_source(self._tracked_tags)
        self.bot.add_dynamic_items(MuteAttacksButton, ViewArmyButton, BoardSelect)
        self.board_loop.start()
        self.daily_loop.start()

    async def cog_unload(self) -> None:
        self.board_loop.cancel()
        self.daily_loop.cancel()

    async def _tracked_tags(self) -> set[str]:
        return await repo.all_tracked_tags(self.db)

    # -------------------------------------------------------------- battle alerts

    @commands.Cog.listener()
    async def on_clash_legend_battle(self, event: LegendBattle) -> None:
        stats = await repo.day_stats(self.db, event.player_tag, event.legend_day)
        embed = E.alert_embed(
            name=event.name,
            tag=event.player_tag,
            kind=event.kind,
            trophies=event.trophies,
            trophies_after=event.trophies_after,
            stats=stats,
            occurred_at=event.occurred_at,
            opponent_tag=event.opponent_tag,
            opponent_name=event.opponent_name,
            opponent_th=event.opponent_th,
            stars=event.stars,
            destruction=event.destruction,
            battle_type=event.battle_type,
        )
        if event.clan_tag:
            await send_clan_log(self.bot, event.clan_tag, "legend_battles", embed, view=alert_view(event))
        for sub in await repo.subscriptions_for_player(self.db, event.player_tag):
            if not (sub["attacks"] if event.kind == "attack" else sub["defenses"]):
                continue
            await self._deliver(sub, embed, event)

    async def _deliver(self, sub, embed: discord.Embed, event: LegendBattle) -> None:
        kind = event.kind
        view = alert_view(event, sub["id"])
        if sub["channel_id"] == 0:
            try:
                user = self.bot.get_user(sub["user_id"]) or await self.bot.fetch_user(sub["user_id"])
                await user.send(embed=embed, view=view)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException) as e:
                log.info("Could not DM legend alert to %s: %s", sub["user_id"], e)
            return
        ping = f"<@&{sub['ping_role_id']}>" if kind == "defense" and sub["ping_role_id"] else None
        await send_to_channel(self.bot, sub["channel_id"], content=ping, embed=embed, view=view)

    # -------------------------------------------------------------- background loops

    @tasks.loop(minutes=2)
    async def board_loop(self) -> None:
        for row in await self.db.live_messages(BOARD_KIND):
            try:
                await self._refresh_board(row)
            except Exception:
                log.exception("Legend board refresh failed for guild %s", row["guild_id"])

    async def _refresh_board(self, row) -> None:
        gid = row["guild_id"]
        tags = await repo.board_tags(self.db, gid)
        entries = await repo.board_entries(self.db, tags, legend_day_id())
        embed = E.board_embed(
            title="🏆 Legend leaderboard",
            entries=entries,
            updated=_now(),
            color=await guild_color(self.bot, gid),
            latest=E.LATEST_BATTLES,
        )
        await update_live_message(self.bot, row, embeds=[embed], view=board_view(gid, entries))

    @tasks.loop(minutes=1)
    async def daily_loop(self) -> None:
        now = _now()
        start, _ = legend_day_bounds(now)
        since_reset = now - start
        # Wait for the final polls of the day to land; don't backfill old days after downtime.
        if since_reset < timedelta(minutes=3) or since_reset > timedelta(hours=3):
            return
        prev_day = (start - timedelta(days=1)).date().isoformat()
        for clan_tag in await repo.clans_with_daily_log(self.db):
            if await repo.daily_posted(self.db, clan_tag, prev_day):
                continue
            rows = await repo.clan_day_rows(self.db, clan_tag, prev_day)
            clan = await self.db.get_clan_row(clan_tag)
            embed = E.clan_day_embed(
                clan_name=clan["name"] if clan else clan_tag,
                clan_tag=clan_tag,
                day=prev_day,
                rows=rows,
                color=discord.Color.gold(),
            )
            await send_clan_log(self.bot, clan_tag, "legend_daily", embed)
            await repo.mark_daily_posted(self.db, clan_tag, prev_day)

    @board_loop.before_loop
    @daily_loop.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    # -------------------------------------------------------------- /track

    async def _sub_autocomplete(self, interaction: discord.Interaction, current: str):
        subs = await repo.list_subscriptions(
            self.db,
            user_id=interaction.user.id,
            guild_id=interaction.guild_id if await has_permission(interaction) else None,
        )
        seen, out = set(), []
        for s in subs:
            if s["player_tag"] in seen:
                continue
            seen.add(s["player_tag"])
            label = f"{s['name'] or '?'} ({s['player_tag']})"
            if current.lower() in label.lower():
                out.append(app_commands.Choice(name=label[:100], value=s["player_tag"]))
        return out[:25]

    async def _manageable_subs(self, interaction: discord.Interaction, tag: str) -> list:
        subs = await repo.subscriptions_for_player(self.db, tag)
        return [s for s in subs if await _can_manage_sub(interaction, s)]

    @track.command(name="add", description="Get an alert after every ranked attack/defense of a player")
    @app_commands.describe(
        player="Player tag",
        destination="DM (default) or a channel",
        channel="Channel for alerts (defaults to this one)",
        attacks="Alert on attacks",
        defenses="Alert on defenses",
        ping_role="Role pinged on defenses (channel alerts; managers)",
    )
    @app_commands.choices(destination=DESTINATIONS)
    @app_commands.autocomplete(player=player_autocomplete)
    async def track_add(
        self,
        interaction: discord.Interaction,
        player: str,
        destination: app_commands.Choice[str] | None = None,
        channel: discord.TextChannel | None = None,
        attacks: bool = True,
        defenses: bool = True,
        ping_role: discord.Role | None = None,
    ) -> None:
        tag = parse_tag(player)
        to_channel = (destination and destination.value == "channel") or channel is not None
        channel_id = 0
        if to_channel:
            if interaction.guild_id is None:
                raise ResolveError("Channel alerts can only be set up inside a server.")
            channel_id = (channel or interaction.channel).id  # type: ignore[union-attr]
        if ping_role and not to_channel:
            raise ResolveError("A ping role only applies to channel alerts.")
        if ping_role and not await has_permission(interaction):
            raise ResolveError("Only managers can set a ping role.")
        if not (attacks or defenses):
            raise ResolveError("Enable at least one of attacks or defenses.")
        if interaction.guild_id is not None:
            existing = await repo.list_subscriptions(self.db, guild_id=interaction.guild_id)
            if len(existing) >= SUBS_PER_GUILD:
                raise ResolveError(f"This server already has {SUBS_PER_GUILD} alerts.")
        await interaction.response.defer(ephemeral=True)
        p = await self.coc.get_player(tag)
        await repo.add_subscription(
            self.db,
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            player_tag=p.tag,
            channel_id=channel_id,
            attacks=attacks,
            defenses=defenses,
            ping_role_id=ping_role.id if ping_role else None,
            created_by=interaction.user.id,
        )
        where = f"<#{channel_id}>" if channel_id else "your DMs"
        what = " and ".join(x for x, on in (("attacks", attacks), ("defenses", defenses)) if on)
        note = ""
        if not is_tracked_ranked(league_name(p)):
            note = "\nℹ️ Alerts start once the player reaches Dragon League 28 or higher."
        await interaction.followup.send(
            embed=ok_embed(f"Tracking **{p.name}** `{p.tag}`: {what} → {where}.{note}"), ephemeral=True
        )

    @track.command(name="remove", description="Stop alerts for a player")
    @app_commands.autocomplete(player=_sub_autocomplete)
    async def track_remove(self, interaction: discord.Interaction, player: str) -> None:
        tag = parse_tag(player)
        subs = await self._manageable_subs(interaction, tag)
        if not subs:
            raise ResolveError(f"You have no alerts for `{tag}` that you can remove.")
        for s in subs:
            await repo.delete_subscription(self.db, s["id"])
        await interaction.response.send_message(
            embed=ok_embed(f"Removed {chip(len(subs))} alert(s) for `{tag}`."), ephemeral=True
        )

    @track.command(name="list", description="List your alerts (managers also see this server's)")
    async def track_list(self, interaction: discord.Interaction) -> None:
        gid = interaction.guild_id if await has_permission(interaction) else None
        subs = await repo.list_subscriptions(self.db, user_id=interaction.user.id, guild_id=gid)
        lines = [alert_line(s, "DM" if s["channel_id"] == 0 else f"<#{s['channel_id']}>") for s in subs]
        if lines:
            lines.insert(0, header("Alerts", "🔔", chip(len(subs))))
        embed = discord.Embed(
            title="Legend alerts",
            color=await guild_color(self.bot, interaction.guild_id),
            description="\n".join(lines)[:4000] or "No alerts set up. Use `/track add`.",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @track.command(name="edit", description="Change alerts for a player")
    @app_commands.autocomplete(player=_sub_autocomplete)
    @app_commands.describe(clear_ping="Remove the ping role")
    async def track_edit(
        self,
        interaction: discord.Interaction,
        player: str,
        attacks: bool | None = None,
        defenses: bool | None = None,
        channel: discord.TextChannel | None = None,
        ping_role: discord.Role | None = None,
        clear_ping: bool = False,
    ) -> None:
        tag = parse_tag(player)
        subs = await self._manageable_subs(interaction, tag)
        if not subs:
            raise ResolveError(f"You have no alerts for `{tag}` that you can edit.")
        if ping_role and not await has_permission(interaction):
            raise ResolveError("Only managers can set a ping role.")
        changes: dict[str, Any] = {}
        if attacks is not None:
            changes["attacks"] = int(attacks)
        if defenses is not None:
            changes["defenses"] = int(defenses)
        if ping_role is not None:
            changes["ping_role_id"] = ping_role.id
        if clear_ping:
            changes["ping_role_id"] = None
        if channel is not None:
            changes["channel_id"] = channel.id
        if not changes:
            raise ResolveError("Nothing to change.")
        for s in subs:
            try:
                await repo.update_subscription(self.db, s["id"], **changes)
            except sqlite3.IntegrityError as e:  # would duplicate an existing alert
                raise ResolveError("That would duplicate an existing alert.") from e
        await interaction.response.send_message(
            embed=ok_embed(f"Updated {chip(len(subs))} alert(s) for `{tag}`."), ephemeral=True
        )

    # -------------------------------------------------------------- /legends

    async def _resolve_player(self, interaction, player, user) -> str:
        return await resolve_player(interaction, player, user)

    @legends.command(name="day", description="A player's ranked attacks and defenses for a legend day")
    @app_commands.autocomplete(player=player_autocomplete)
    @app_commands.describe(day_offset="0 = today, 1 = yesterday, ...")
    async def legends_day(
        self,
        interaction: discord.Interaction,
        player: str | None = None,
        user: discord.User | None = None,
        day_offset: app_commands.Range[int, 0, 60] = 0,
    ) -> None:
        tag = await self._resolve_player(interaction, player, user)
        day = legend_day_id(_now() - timedelta(days=day_offset))
        gid = interaction.guild_id

        async def render(t: str) -> discord.Embed:
            return await _day_card(self.bot, t, day, gid)

        view = None
        if not player:
            links = await self.db.get_links((user or interaction.user).id)
            if len(links) > 1:
                view = AltSelectView(
                    interaction.user.id, [(r["player_tag"], r["name"] or "?") for r in links], render
                )
        await interaction.response.send_message(embed=await render(tag), view=view or discord.utils.MISSING)

    @legends.command(name="week", description="A player's ranked week (weekly tournaments)")
    @app_commands.autocomplete(player=player_autocomplete)
    @app_commands.describe(weeks_ago="0 = this week, 1 = last week, ...")
    async def legends_week(
        self,
        interaction: discord.Interaction,
        player: str | None = None,
        user: discord.User | None = None,
        weeks_ago: app_commands.Range[int, 0, 12] = 0,
    ) -> None:
        tag = await self._resolve_player(interaction, player, user)
        week = ranked_week(_now() - timedelta(weeks=weeks_ago))
        league = await repo.player_league(self.db, tag)
        embed = E.week_embed(
            name=await _player_name(self.bot, tag),
            tag=tag,
            league=league,
            week_id=week.id,
            days=await repo.week_days(self.db, tag, week.start),
            weekly_cap=weekly_attack_cap(league),
            color=await guild_color(self.bot, interaction.guild_id),
        )
        await interaction.response.send_message(embed=embed)

    async def _season_autocomplete(self, interaction: discord.Interaction, current: str):
        raw = getattr(interaction.namespace, "player", None)
        try:
            tag = parse_tag(raw) if raw else (await self.db.get_linked_tags(interaction.user.id) or [""])[0]
        except ResolveError:
            return []
        seasons = await repo.seasons_for_player(self.db, tag) if tag else []
        cur = game_season().id
        if cur not in seasons:
            seasons.insert(0, cur)
        out = []
        for s in seasons:
            label = season_label(season_by_id(s))
            if current.lower() in f"{label} {s}".lower():
                out.append(app_commands.Choice(name=label, value=s))
        return out[:25]

    @legends.command(name="logs", description="Day-by-day legend stats for a season")
    @app_commands.autocomplete(player=player_autocomplete, season=_season_autocomplete)
    @app_commands.describe(season="Pick a season, or type any date in it (YYYY-MM-DD); default: current")
    async def legends_logs(
        self,
        interaction: discord.Interaction,
        player: str | None = None,
        user: discord.User | None = None,
        season: str | None = None,
    ) -> None:
        tag = await self._resolve_player(interaction, player, user)
        if season:
            window = season_for_date(season)
            if window is None:
                raise ResolveError("Pick a season from the list, or type a date like 2026-11-02.")
            season_id = window.id
        else:
            season_id = game_season().id
        summary = await repo.season_summary(self.db, tag, season_id)
        embed = E.season_embed(
            name=await _player_name(self.bot, tag),
            tag=tag,
            season_id=season_id,
            summary=summary,
            color=await guild_color(self.bot, interaction.guild_id),
        )
        await interaction.response.send_message(embed=embed)

    @legends.command(name="history", description="A player's legend results across seasons")
    @app_commands.autocomplete(player=player_autocomplete)
    async def legends_history(
        self,
        interaction: discord.Interaction,
        player: str | None = None,
        user: discord.User | None = None,
    ) -> None:
        tag = await self._resolve_player(interaction, player, user)
        row = await self.db.get_player_row(tag)
        names = [r["name"] for r in await self.db.name_history(tag)]
        embed = E.history_embed(
            name=row["name"] if row else tag,
            tag=tag,
            seasons=await repo.player_seasons(self.db, tag),
            best_trophies=row["best_trophies"] if row else None,
            names=names,
            color=await guild_color(self.bot, interaction.guild_id),
        )
        await interaction.response.send_message(embed=embed)

    @legends.command(name="clan", description="Today's legend leaderboard for a clan's members")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def legends_clan(self, interaction: discord.Interaction, clan: str | None = None) -> None:
        if interaction.guild_id is None:
            raise ResolveError("Use this inside a server.")
        tag = await resolve_clan(interaction, clan)
        row = await self.db.get_clan_row(tag)
        tags = await repo.legend_members(self.db, [tag])
        entries = await repo.board_entries(self.db, tags, legend_day_id())
        embed = E.board_embed(
            title=f"{row['name'] if row else tag} · Legends today",
            entries=entries,
            color=await guild_color(self.bot, interaction.guild_id),
            latest=E.LATEST_BATTLES,
        )
        await interaction.response.send_message(embed=embed)

    @legends.command(name="leaderboard", description="Family-wide legend ranking across this server's clans")
    @app_commands.describe(clans="Clan tags/aliases separated by spaces (default: all)")
    async def legends_leaderboard(self, interaction: discord.Interaction, clans: str | None = None) -> None:
        if interaction.guild_id is None:
            raise ResolveError("Use this inside a server.")
        tags = await repo.legend_members(self.db, await resolve_clans(interaction, clans))
        entries = (await repo.board_entries(self.db, tags, legend_day_id()))[:50]
        embed = E.board_embed(
            title="Family legend leaderboard",
            entries=entries,
            color=await guild_color(self.bot, interaction.guild_id),
            latest=E.LATEST_BATTLES,
        )
        embed.set_footer(text=f"Season ends {game_season().end:%Y-%m-%d}")
        await interaction.response.send_message(embed=embed)

    # -------------------------------------------------------------- /legendboard

    async def _board_row(self, guild_id: int):
        rows = [r for r in await self.db.live_messages(BOARD_KIND) if r["guild_id"] == guild_id]
        return rows[0] if rows else None

    async def _board_autocomplete(self, interaction: discord.Interaction, current: str):
        out = []
        for tag in await repo.board_tags(self.db, interaction.guild_id):
            label = f"{await _player_name(self.bot, tag)} ({tag})"
            if current.lower() in label.lower():
                out.append(app_commands.Choice(name=label[:100], value=tag))
        return out[:25]

    @legendboard.command(name="setchannel", description="Post the live legend leaderboard in a channel")
    @manager()
    async def board_setchannel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        old = await self._board_row(interaction.guild_id)
        if old and old["message_id"] and old["channel_id"] != channel.id:
            ch = self.bot.get_channel(old["channel_id"])
            if ch is not None:
                try:
                    await ch.get_partial_message(old["message_id"]).delete()
                except discord.HTTPException:
                    pass
        await self.db.upsert_live_message(interaction.guild_id, BOARD_KIND, "", channel.id, None)
        await interaction.response.send_message(
            embed=ok_embed(f"The live legend leaderboard will post in {channel.mention}."), ephemeral=True
        )
        await self._refresh_board(await self._board_row(interaction.guild_id))

    @legendboard.command(name="add", description="Add a player to the leaderboard")
    @app_commands.autocomplete(player=player_autocomplete)
    @manager()
    async def board_add(self, interaction: discord.Interaction, player: str) -> None:
        tag = parse_tag(player)
        await interaction.response.defer(ephemeral=True)
        p = await self.coc.get_player(tag)
        added = await repo.board_add(self.db, interaction.guild_id, [p.tag])
        embed = (
            ok_embed(f"Added **{p.name}** `{p.tag}` to the leaderboard.")
            if added
            else fail_embed(
                f"Not added: **{p.name}** is already listed, or the board is full ({repo.BOARD_LIMIT})."
            )
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @legendboard.command(
        name="import", description="Add a clan's ranked members (Dragon 28+) to the leaderboard"
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Append", value="append"),
            app_commands.Choice(name="Replace", value="replace"),
        ]
    )
    @app_commands.autocomplete(clan=clan_autocomplete)
    @manager()
    async def board_import(
        self, interaction: discord.Interaction, clan: str, mode: app_commands.Choice[str] | None = None
    ) -> None:
        tag = await resolve_clan(interaction, clan, must_be_tracked=False)
        await interaction.response.defer(ephemeral=True)
        c = await self.coc.get_clan(tag)
        tags = [
            m.tag for m in sorted(c.members, key=lambda m: -m.trophies) if is_tracked_ranked(league_name(m))
        ]
        if mode and mode.value == "replace":
            await repo.board_clear(self.db, interaction.guild_id)
        added = await repo.board_add(self.db, interaction.guild_id, tags)
        await interaction.followup.send(
            embed=ok_embed(
                f"Imported {chip(f'{added}/{len(tags)}')} ranked members (Dragon 28+) from **{c.name}**."
            ),
            ephemeral=True,
        )

    @legendboard.command(name="remove", description="Remove a player from the leaderboard")
    @app_commands.autocomplete(player=_board_autocomplete)
    @manager()
    async def board_remove(self, interaction: discord.Interaction, player: str) -> None:
        tag = parse_tag(player)
        ok = await repo.board_remove(self.db, interaction.guild_id, tag)
        await interaction.response.send_message(
            embed=ok_embed(f"Removed `{tag}` from the leaderboard.")
            if ok
            else fail_embed(f"`{tag}` isn't on the leaderboard."),
            ephemeral=True,
        )

    @legendboard.command(name="list", description="Players on the leaderboard")
    async def board_list(self, interaction: discord.Interaction) -> None:
        tags = await repo.board_tags(self.db, interaction.guild_id)
        entries = await repo.board_entries(self.db, tags, legend_day_id())
        embed = E.board_embed(
            title=f"Legend leaderboard players ({len(tags)}/{repo.BOARD_LIMIT})",
            entries=entries,
            color=await guild_color(self.bot, interaction.guild_id),
        )
        missing = set(tags) - {e.player_tag for e in entries}
        if missing:
            embed.add_field(name="Waiting for data", value=", ".join(sorted(missing))[:1024], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @legendboard.command(name="post", description="Refresh (or re-post) the live leaderboard now")
    @manager()
    async def board_post(self, interaction: discord.Interaction) -> None:
        row = await self._board_row(interaction.guild_id)
        if row is None:
            raise ResolveError("Set a channel first with `/legendboard setchannel`.")
        await interaction.response.send_message(
            embed=ok_embed(f"Refreshing the leaderboard in <#{row['channel_id']}>."), ephemeral=True
        )
        await self._refresh_board(row)

    @legendboard.command(name="disable", description="Stop updating the live leaderboard")
    @manager()
    async def board_disable(self, interaction: discord.Interaction) -> None:
        await self.db.delete_live_message(interaction.guild_id, BOARD_KIND, "")
        await interaction.response.send_message(embed=ok_embed("Live leaderboard disabled."), ephemeral=True)

    # -------------------------------------------------------------- /top

    async def _location_autocomplete(self, interaction: discord.Interaction, current: str):
        if self._locations is None:
            try:
                self._locations = await self.coc.search_locations(limit=None)
            except Exception:
                self._locations = []
        out = [app_commands.Choice(name="Global", value="global")]
        cur = current.lower()
        for loc in self._locations:
            if loc.is_country and cur in loc.name.lower():
                out.append(app_commands.Choice(name=loc.name[:100], value=str(loc.id)))
        return out[:25]

    async def _location(self, raw: str | None) -> tuple[Any, str]:
        """(location id for the API, display name)."""
        if not raw or raw.lower() == "global":
            return "global", "Global"
        if raw.isdigit():
            loc = next((x for x in (self._locations or []) if str(x.id) == raw), None)
            return int(raw), loc.name if loc else raw
        loc = await self.coc.get_location_named(raw)
        if loc is None:
            raise ResolveError(f"Unknown location `{raw}`.")
        return loc.id, loc.name

    async def _family_tags(self, guild_id: int | None) -> tuple[set[str], set[str]]:
        """(player tags, clan tags) tracked by this server, for highlighting."""
        if guild_id is None:
            return set(), set()
        clans = set(await self.db.get_guild_clans(guild_id))
        rows = await self.db.fetchall(
            "SELECT player_tag FROM players WHERE clan_tag IN "
            "(SELECT clan_tag FROM guild_clans WHERE guild_id = ?)",
            (guild_id,),
        )
        tags = {r["player_tag"] for r in rows} | set(await repo.board_tags(self.db, guild_id))
        return tags, clans

    @top.command(name="players", description="Top players globally or in a country")
    @app_commands.autocomplete(location=_location_autocomplete)
    async def top_players(self, interaction: discord.Interaction, location: str | None = None) -> None:
        await interaction.response.defer()
        loc_id, loc_name = await self._location(location)
        players = await self.coc.get_location_players(loc_id, limit=200)
        highlight, _ = await self._family_tags(interaction.guild_id)
        color = await guild_color(self.bot, interaction.guild_id)
        pages = max(1, -(-len(players) // PAGE_SIZE))

        async def render(page: int) -> discord.Embed:
            chunk = players[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
            return E.top_players_embed(
                title=f"Top players · {loc_name}",
                players=chunk,
                page=page,
                pages=pages,
                highlight=highlight,
                color=color,
            )

        await interaction.followup.send(embed=await render(0), view=Pager(interaction.user.id, pages, render))

    @top.command(name="clans", description="Top clans globally or in a country")
    @app_commands.autocomplete(location=_location_autocomplete)
    async def top_clans(self, interaction: discord.Interaction, location: str | None = None) -> None:
        await interaction.response.defer()
        loc_id, loc_name = await self._location(location)
        clans = await self.coc.get_location_clans(loc_id, limit=200)
        _, highlight = await self._family_tags(interaction.guild_id)
        color = await guild_color(self.bot, interaction.guild_id)
        pages = max(1, -(-len(clans) // PAGE_SIZE))

        async def render(page: int) -> discord.Embed:
            chunk = clans[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
            return E.top_clans_embed(
                title=f"Top clans · {loc_name}",
                clans=chunk,
                page=page,
                pages=pages,
                highlight=highlight,
                color=color,
            )

        await interaction.followup.send(embed=await render(0), view=Pager(interaction.user.id, pages, render))

    @top.command(name="today", description="Best offense or defense today among tracked players")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Best offense", value="offense"),
            app_commands.Choice(name="Best defense", value="defense"),
        ]
    )
    @app_commands.describe(min_battles="Only players with at least this many battles of that kind")
    async def top_today(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str],
        min_battles: app_commands.Range[int, 0, 8] = 0,
    ) -> None:
        day = legend_day_id()
        rows = await repo.top_today(self.db, day, mode.value, min_battles)
        lines = [
            [i, (r["name"] or r["player_tag"])[:15], r["trophies"] or "?", signed(r["total"]), r["battles"]]
            for i, r in enumerate(rows, 1)
        ]
        embed = discord.Embed(
            title=f"{mode.name} · legend day {day}",
            color=await guild_color(self.bot, interaction.guild_id),
            description=table(lines, ["#", "Name", "Cups", "Total", "N"])
            if rows
            else "No battles yet today.",
        )
        embed.set_footer(text="Among players this bot tracks")
        await interaction.response.send_message(embed=embed)


async def setup(bot) -> None:
    await bot.add_cog(Legends(bot))
