"""Scheduled boards: leaderboards and summaries posted to a channel on a schedule, or kept live.

Everything is built from the database (no API calls); a once-a-minute loop posts boards that are
due. See repos/autoboards.py for the schedule maths and storage.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import tasks

from clashhero.cogs._base import BaseCog
from clashhero.db.database import Database
from clashhero.repos import autoboards as repo
from clashhero.repos import capital, legends, stats, war
from clashhero.utils.checks import manager
from clashhero.utils.fmt import make_embed, signed, table, ts
from clashhero.utils.resolve import ResolveError, resolve_clans
from clashhero.utils.seasons import Window, clan_games, game_season, legend_day_id

log = logging.getLogger(__name__)

TOP = 20

BOARD_CHOICES = [app_commands.Choice(name=label, value=key) for key, label in repo.BOARD_TYPES.items()]
SCHEDULE_CHOICES = [app_commands.Choice(name=label, value=key) for key, label in repo.SCHEDULES.items()]
WEEKDAY_CHOICES = [app_commands.Choice(name=name, value=i) for i, name in enumerate(repo.WEEKDAYS)]


# ------------------------------------------------------------------ board bodies (DB only)


def _short(name: str | None, width: int = 15) -> str:
    name = name or "?"
    return name if len(name) <= width else name[: width - 1] + "…"


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _season_line(w: Window) -> str:
    return f"Season {ts(w.start, 'D')} → {ts(w.end, 'D')}\n"


async def _donations(db: Database, tags: Sequence[str], names: dict[str, str], ref: datetime) -> str:
    w = game_season(ref)
    body = _season_line(w)
    if len(tags) > 1:
        per_clan = await stats.season_totals_by_clan(db, tags, w.id)
        body += table(
            [
                (
                    _short(names.get(r["clan_tag"], r["clan_tag"]), 16),
                    f"{r['donations']:,}",
                    f"{r['received']:,}",
                )
                for r in per_clan
            ],
            ["CLAN", "DONATED", "RECEIVED"],
        )
    top = await stats.top_season_players(db, tags, w.id, "donations", TOP)
    return (
        body
        + "**Top donors**\n"
        + table(
            [(i, f"{r['value']:,}", _short(r["name"])) for i, r in enumerate(top, 1)], ["#", "DON", "NAME"]
        )
    )


async def _trophies(db: Database, tags: Sequence[str], names: dict[str, str], ref: datetime) -> str:
    body = ""
    if len(tags) > 1:
        per_clan = await stats.clan_trophy_summary(db, tags)
        body += table(
            [
                (
                    _short(r["name"] or r["clan_tag"], 16),
                    r["members"],
                    f"{r['total'] or 0:,}",
                    round(r["average"] or 0),
                )
                for r in per_clan
            ],
            ["CLAN", "MEM", "TOTAL", "AVG"],
        )
    top = await stats.top_trophy_players(db, tags, TOP)
    return (
        body
        + "**Top players**\n"
        + table(
            [(i, r["trophies"] or 0, r["town_hall"] or "?", _short(r["name"])) for i, r in enumerate(top, 1)],
            ["#", "CUPS", "TH", "NAME"],
        )
    )


async def _legends(db: Database, tags: Sequence[str], names: dict[str, str], ref: datetime) -> str:
    day = legend_day_id(ref)
    entries = await legends.board_entries(db, await legends.legend_members(db, tags), day)
    if not entries:
        return f"Legend day {day}\nNo ranked players in these clans yet."
    rows = [
        (i, e.trophies, signed(e.stats.offense), signed(e.stats.defense), signed(e.stats.net), _short(e.name))
        for i, e in enumerate(entries[:TOP], 1)
    ]
    return f"Legend day {day}\n" + table(rows, ["#", "CUPS", "OFF", "DEF", "NET", "NAME"])


async def _war_stats(db: Database, tags: Sequence[str], names: dict[str, str], ref: datetime) -> str:
    w = game_season(ref)
    board = await war.attack_leaderboard(db, tags, since=w.start, until=w.end)
    attackers = [(t, s) for t, s in board if s.attacks]
    attacks = sum(s.attacks for _, s in board)
    triples = sum(s.triples for _, s in board)
    missed = sum(s.missed for _, s in board)
    body = _season_line(w)
    if not attacks and not missed:
        return body + "No war attacks recorded this season."
    body += (
        f"Hit rate **{_pct(triples / attacks if attacks else 0)}** "
        f"({triples}/{attacks} triples) · {missed} missed\n"
    )
    player_names = await war.player_names(db, [t for t, _ in attackers[:TOP]])
    rows = [
        (
            i,
            _pct(s.triple_rate),
            f"{s.triples}/{s.attacks}",
            f"{s.avg_stars:.2f}",
            s.missed,
            _short(player_names.get(t, t)),
        )
        for i, (t, s) in enumerate(attackers[:TOP], 1)
    ]
    return body + table(rows, ["#", "HIT%", "3★", "AVG★", "MISS", "NAME"])


RESULT_ICONS = {"win": "✅", "lose": "❌", "tie": "➖"}


async def _war_results(db: Database, tags: Sequence[str], names: dict[str, str], ref: datetime) -> str:
    w = game_season(ref)
    rows = await stats.war_results(db, tags, w.start, w.end)
    body = _season_line(w) + table(
        [
            (
                _short(r["clan_name"] or r["clan_tag"], 16),
                r["wins"],
                r["losses"],
                r["ties"],
                f"{r['wins'] * 100 / r['total']:.0f}%" if r["total"] else "—",
            )
            for r in rows
        ],
        ["CLAN", "W", "L", "T", "WIN%"],
    )
    ended = [r for r in await stats.wars_rows(db, tags, w.start, w.end) if r["state"] == "warEnded"]
    recent = []
    for r in reversed(ended[-10:]):
        kind = "CWL" if r["war_type"] == "cwl" else r["war_type"].title()
        icon = RESULT_ICONS.get(r["result"] or "", "•")
        clan = _short(names.get(r["clan_tag"], r["clan_tag"]), 20)
        recent.append(
            f"{icon} **{clan}** vs {_short(r['opponent_name'], 20)} ({kind})"
            f" · ⭐ {r['clan_stars']}–{r['opponent_stars']}"
            f" · {r['clan_destruction'] or 0:.1f}%–{r['opponent_destruction'] or 0:.1f}%"
        )
    if recent:
        body += "**Recent wars**\n" + "\n".join(recent)
    return body


async def _clan_games(db: Database, tags: Sequence[str], names: dict[str, str], ref: datetime) -> str:
    games = clan_games(ref)
    body = f"Clan Games {ts(games.start, 'D')} → {ts(games.end, 'D')}\n"
    per_clan = []
    for t in tags:
        rows = await capital.clan_games_rows(db, t, games.id)
        if rows:
            s = capital.summarize_games(rows)
            maxed = sum(1 for r in s.rows if r["maxed"])
            per_clan.append((s.total, _short(names.get(t, t), 16), s.tier, len(rows), maxed))
    per_clan.sort(key=lambda x: -x[0])
    body += table(
        [(n, f"{total:,}", f"{tier}/6", players, maxed) for total, n, tier, players, maxed in per_clan],
        ["CLAN", "POINTS", "TIER", "PLAYERS", "MAXED"],
    )
    top = await stats.clan_games_players(db, tags, games.id, TOP)
    return (
        body
        + "**Top scorers**\n"
        + table(
            [(i, f"{r['points']:,}", _short(r["name"])) for i, r in enumerate(top, 1)], ["#", "PTS", "NAME"]
        )
    )


async def _capital(db: Database, tags: Sequence[str], names: dict[str, str], ref: datetime) -> str:
    w = game_season(ref)
    body = _season_line(w)
    if len(tags) > 1:
        per_clan = await stats.season_totals_by_clan(db, tags, w.id)
        body += table(
            [
                (_short(names.get(r["clan_tag"], r["clan_tag"]), 16), f"{r['capital_gold'] or 0:,}")
                for r in sorted(per_clan, key=lambda r: -(r["capital_gold"] or 0))
            ],
            ["CLAN", "CAPITAL GOLD"],
        )
    top = await stats.top_season_players(db, tags, w.id, "capital_gold", TOP)
    return (
        body
        + "**Top contributors**\n"
        + table(
            [(i, f"{r['value']:,}", _short(r["name"])) for i, r in enumerate(top, 1)], ["#", "GOLD", "NAME"]
        )
    )


async def _activity(db: Database, tags: Sequence[str], names: dict[str, str], ref: datetime) -> str:
    w = game_season(ref)
    body = _season_line(w)
    if len(tags) > 1:
        per_clan = await stats.season_totals_by_clan(db, tags, w.id)
        body += table(
            [
                (_short(names.get(r["clan_tag"], r["clan_tag"]), 16), r["activity"] or 0)
                for r in sorted(per_clan, key=lambda r: -(r["activity"] or 0))
            ],
            ["CLAN", "ACTIVITY"],
        )
    top = await stats.top_season_players(db, tags, w.id, "activity", TOP)
    return (
        body
        + "**Most active**\n"
        + table([(i, r["value"], _short(r["name"])) for i, r in enumerate(top, 1)], ["#", "SCORE", "NAME"])
    )


Renderer = Callable[[Database, Sequence[str], dict[str, str], datetime], Awaitable[str]]

RENDERERS: dict[str, Renderer] = {
    "donations": _donations,
    "trophies": _trophies,
    "legends": _legends,
    "war_stats": _war_stats,
    "war_results": _war_results,
    "clan_games": _clan_games,
    "capital": _capital,
    "activity": _activity,
}


async def render_body(
    db: Database, board_type: str, tags: Sequence[str], names: dict[str, str], ref: datetime
) -> str:
    """The embed description for a board (≤ 4096 chars)."""
    return (await RENDERERS[board_type](db, tags, names, ref))[:4096]


# ------------------------------------------------------------------ cog


async def board_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    cur = current.lower()
    out = []
    for r in await repo.guild_boards(interaction.client.db, interaction.guild_id):
        label = f"#{r['id']} {repo.BOARD_TYPES.get(r['board_type'], r['board_type'])} · "
        label += repo.Schedule.from_row(r).describe()
        if cur in label.lower():
            out.append(app_commands.Choice(name=label[:100], value=r["id"]))
    return out[:25]


class Autoboards(BaseCog):
    autoboard = app_commands.Group(
        name="autoboard", description="Leaderboards posted on a schedule or kept live", guild_only=True
    )

    async def cog_load(self) -> None:
        self.board_loop.start()

    async def cog_unload(self) -> None:
        self.board_loop.cancel()

    # ------------------------------------------------------------------ rendering & delivery

    async def _clan_tags(self, row) -> list[str]:
        """Stored clans still linked to the server, or every server clan if none were given."""
        linked = await self.db.get_guild_clans(row["guild_id"])
        stored = repo.board_clans(row)
        return [t for t in stored if t in linked] if stored else linked

    async def build_embed(self, row, ref: datetime, now: datetime) -> discord.Embed | None:
        tags = await self._clan_tags(row)
        if not tags:
            return None
        names = {
            r["clan_tag"]: r["name"] or r["clan_tag"]
            for r in await self.db.get_guild_clan_rows(row["guild_id"])
        }
        body = await render_body(self.db, row["board_type"], tags, names, ref)
        e = await make_embed(
            self.bot,
            row["guild_id"],
            title=repo.BOARD_TYPES.get(row["board_type"], row["board_type"]),
            description=body,
            timestamp=now,
        )
        clan_label = ", ".join(names.get(t, t) for t in tags)
        e.set_footer(text=f"{clan_label[:1900]} · {repo.Schedule.from_row(row).describe()}")
        return e

    async def _channel(self, channel_id: int) -> discord.abc.Messageable | None:
        """The channel, or None if it no longer exists. Raises discord.Forbidden if hidden."""
        ch = self.bot.get_channel(channel_id)
        if ch is not None:
            return ch  # type: ignore[return-value]
        try:
            return await self.bot.fetch_channel(channel_id)  # type: ignore[return-value]
        except discord.NotFound:
            return None

    async def deliver(self, row, embed: discord.Embed) -> str:
        """Post (fixed) or edit-or-repost (live). Returns "ok", "failed", or "gone" (the channel no
        longer exists, so the board was removed)."""
        try:
            ch = await self._channel(row["channel_id"])
        except (discord.Forbidden, discord.HTTPException) as e:
            log.warning("Autoboard %s: can't access channel %s: %s", row["id"], row["channel_id"], e)
            return "failed"
        if ch is None:
            log.info("Autoboard %s: channel %s is gone; removing board", row["id"], row["channel_id"])
            await repo.delete_board_by_id(self.db, row["id"])
            return "gone"
        if row["schedule"] == "live" and row["message_id"]:
            try:
                await ch.get_partial_message(row["message_id"]).edit(embed=embed)  # type: ignore[attr-defined]
                return "ok"
            except discord.NotFound:
                pass  # deleted: repost below
            except (discord.Forbidden, discord.HTTPException) as e:
                log.warning("Autoboard %s: edit failed: %s", row["id"], e)
                return "failed"
        try:
            msg = await ch.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException) as e:
            log.warning("Autoboard %s: send to %s failed: %s", row["id"], row["channel_id"], e)
            return "failed"
        if row["schedule"] == "live":
            await repo.set_message_id(self.db, row["id"], msg.id)
        return "ok"

    async def run_board(self, row, now: datetime) -> str | None:
        """Post a board if it's due, claiming the slot first so it's posted once. Returns the
        delivery status, or None if nothing was due."""
        schedule = repo.Schedule.from_row(row)
        slot = repo.due_slot(schedule, now, repo.from_iso(row["last_run"]))
        if slot is None or not await repo.claim_run(self.db, row["id"], row["last_run"], slot):
            return None
        embed = await self.build_embed(row, repo.reference_time(schedule, slot, now), now)
        if embed is None:
            log.info("Autoboard %s: no clans to show", row["id"])
            return "failed"
        return await self.deliver(row, embed)

    @tasks.loop(minutes=1)
    async def board_loop(self) -> None:
        now = datetime.now(UTC)
        for row in await repo.all_boards(self.db):
            try:
                await self.run_board(row, now)
            except Exception:
                log.exception("Autoboard %s failed", row["id"])

    @board_loop.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------ commands

    @autoboard.command(name="create", description="Post a board on a schedule or keep it live (Bot Manager)")
    @app_commands.choices(board=BOARD_CHOICES, schedule=SCHEDULE_CHOICES, weekday=WEEKDAY_CHOICES)
    @app_commands.rename(at="time")
    @app_commands.describe(
        board="What to show",
        schedule="When to post it",
        channel="Where to post (default: this channel)",
        clans="Tags/aliases separated by spaces (default: every server clan)",
        at="HH:MM in UTC (daily and weekly boards)",
        weekday="Day of the week (weekly boards)",
        interval="Minutes between edits for live boards (10-1440, default 30)",
    )
    @manager("bot")
    async def create(
        self,
        interaction: discord.Interaction,
        board: app_commands.Choice[str],
        schedule: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
        clans: str | None = None,
        at: str | None = None,
        weekday: app_commands.Choice[int] | None = None,
        interval: app_commands.Range[int, repo.MIN_LIVE_MINUTES, repo.MAX_LIVE_MINUTES] | None = None,
    ) -> None:
        gid = interaction.guild_id
        kind = schedule.value
        hour = minute = None
        if kind in ("daily", "weekly"):
            if not at:
                raise ResolveError("Daily and weekly boards need a `time` (HH:MM, UTC).")
            try:
                hour, minute = repo.parse_hhmm(at)
            except ValueError:
                raise ResolveError(f"`{at}` isn't a time. Use HH:MM in UTC, e.g. `18:00`.") from None
        if kind == "weekly" and weekday is None:
            raise ResolveError("Weekly boards need a `weekday`.")
        sched = repo.Schedule(
            kind,
            hour,
            minute,
            weekday.value if weekday is not None and kind == "weekly" else None,
            (interval or repo.DEFAULT_LIVE_MINUTES) if kind == "live" else None,
        )
        if await repo.count_boards(self.db, gid) >= repo.MAX_BOARDS_PER_GUILD:
            raise ResolveError(
                f"This server already has {repo.MAX_BOARDS_PER_GUILD} boards. Delete one first."
            )
        tags = await resolve_clans(interaction, clans)  # validates input; errors if no clans linked
        if channel is not None:
            target: discord.abc.Messageable | None = channel
            me = interaction.guild.me if interaction.guild else None
            perms = channel.permissions_for(me) if me is not None else None
        else:
            target, perms = interaction.channel, interaction.app_permissions  # type: ignore[assignment]
        if target is None or not isinstance(target, discord.abc.Messageable):
            raise ResolveError("Pick a text channel for the board.")
        mention = f"<#{target.id}>"  # type: ignore[attr-defined]
        if perms is not None and not (perms.view_channel and perms.send_messages and perms.embed_links):
            raise ResolveError(f"I need View Channel, Send Messages and Embed Links in {mention}.")
        await interaction.response.defer(ephemeral=True)
        now = datetime.now(UTC)
        board_id = await repo.create_board(
            self.db,
            gid,
            board.value,
            tags if clans else None,
            target.id,
            sched,
            interaction.user.id,
            now,
        )
        row = await repo.get_board(self.db, gid, board_id)
        note = ""
        if sched.is_live:
            status = await self.run_board(row, now)
            note = "Posted now; " if status == "ok" else "Couldn't post it yet (I'll keep trying); "
            row = await repo.get_board(self.db, gid, board_id)
        nxt = repo.next_post_time(sched, now, repo.from_iso(row["last_run"]) if row else None)
        await interaction.followup.send(
            f"Board `#{board_id}` **{board.name}** · {sched.describe()} → {mention}. "
            f"{note}{'next' if note else 'Next'} {'update' if sched.is_live else 'post'} {ts(nxt)}.",
            ephemeral=True,
        )

    @autoboard.command(name="list", description="Scheduled and live boards in this server")
    async def list_(self, interaction: discord.Interaction) -> None:
        now = datetime.now(UTC)
        rows = await repo.guild_boards(self.db, interaction.guild_id)
        names = {
            r["clan_tag"]: r["alias"] or r["name"] or r["clan_tag"]
            for r in await self.db.get_guild_clan_rows(interaction.guild_id)
        }
        lines = []
        for r in rows:
            s = repo.Schedule.from_row(r)
            clans = ", ".join(names.get(t, t) for t in repo.board_clans(r)) or "all clans"
            nxt = repo.next_post_time(s, now, repo.from_iso(r["last_run"]))
            lines.append(
                f"`#{r['id']}` **{repo.BOARD_TYPES.get(r['board_type'], r['board_type'])}** → "
                f"<#{r['channel_id']}>\n{s.describe()} · {clans} · next {ts(nxt)}"
            )
        embed = await make_embed(
            self.bot,
            interaction.guild_id,
            title="Boards",
            description="\n".join(lines)[:4000] or "No boards yet. Try `/autoboard create`.",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @autoboard.command(name="delete", description="Stop and remove a board (Bot Manager)")
    @app_commands.autocomplete(board_id=board_autocomplete)
    @manager("bot")
    async def delete(self, interaction: discord.Interaction, board_id: int) -> None:
        row = await repo.get_board(self.db, interaction.guild_id, board_id)
        if row is None or not await repo.delete_board(self.db, interaction.guild_id, board_id):
            raise ResolveError(f"No board `#{board_id}` in this server.")
        if row["schedule"] == "live" and row["message_id"]:
            try:  # best effort: remove the live message too
                ch = await self._channel(row["channel_id"])
                if ch is not None:
                    await ch.get_partial_message(row["message_id"]).delete()  # type: ignore[attr-defined]
            except discord.HTTPException:
                pass
        await interaction.response.send_message(f"Deleted board `#{board_id}`.", ephemeral=True)

    @autoboard.command(
        name="post-now", description="Post (or refresh, if live) a board right now (Bot Manager)"
    )
    @app_commands.autocomplete(board_id=board_autocomplete)
    @manager("bot")
    async def post_now(self, interaction: discord.Interaction, board_id: int) -> None:
        row = await repo.get_board(self.db, interaction.guild_id, board_id)
        if row is None:
            raise ResolveError(f"No board `#{board_id}` in this server.")
        await interaction.response.defer(ephemeral=True)
        now = datetime.now(UTC)
        if row["schedule"] == "live":
            # Refresh now and restart the interval from here (re-read: the loop may have just
            # posted it and stored a new message id).
            await repo.claim_run(self.db, row["id"], row["last_run"], now)
            row = await repo.get_board(self.db, interaction.guild_id, board_id) or row
        embed = await self.build_embed(row, now, now)
        if embed is None:
            raise ResolveError("None of this board's clans are linked to the server any more.")
        status = await self.deliver(row, embed)
        if status == "gone":
            raise ResolveError(f"Board `#{board_id}`'s channel no longer exists, so the board was removed.")
        if status != "ok":
            raise ResolveError(f"Couldn't post in <#{row['channel_id']}>. Check my permissions there.")
        await interaction.followup.send(
            f"Posted board `#{board_id}` in <#{row['channel_id']}>.", ephemeral=True
        )


async def setup(bot) -> None:
    await bot.add_cog(Autoboards(bot))
