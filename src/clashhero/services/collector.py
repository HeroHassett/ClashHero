"""Background data collection.

The collector is the only thing that polls the Clash API on a schedule. It persists what it
sees (clans, players, wars, raids, legend battles, season stats, activity) and dispatches typed
events from :mod:`clashhero.events`. Feature cogs read the tables and listen to events; they
should not run their own polling loops against the API.

Loops:
- clans   (CLAN_POLL_SECONDS):   membership, roles, donations, clan level/leagues
- players (PLAYER_POLL_SECONDS): every member of a tracked clan, every linked account and every
          tag provided by registered player sources (e.g. legend subscriptions)
- wars    (WAR_POLL_SECONDS):    current war / CWL round per tracked clan
- raids   (RAID_POLL_SECONDS):   current capital raid weekend per tracked clan
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import coc
from discord.ext import tasks

from clashhero import events as ev
from clashhero.services.battlelog import (
    PENDING_TIMEOUT,
    Battle,
    Cursor,
    battle_trophies,
    note_unknown_types,
    parse_battles,
    take_new,
)
from clashhero.services.snapshots import (
    MAX_PLAUSIBLE_DELTA,
    ClanSnap,
    PlayerSnap,
    counter_delta,
    diff_clan,
    diff_settings,
    diff_units,
    has_activity,
    infer_battles,
)
from clashhero.utils.ranked import daily_attack_cap, defenses_gain, is_legend_one, is_tracked_ranked
from clashhero.utils.seasons import (
    clan_games,
    cwl_season_id,
    game_season,
    legend_day_bounds,
    legend_day_id,
    ranked_week,
)

if TYPE_CHECKING:
    from clashhero.bot import ClashHeroBot

log = logging.getLogger(__name__)

PlayerSource = Callable[[], Awaitable[Iterable[str]]]


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat() if dt else None


def _ts(t: Any) -> datetime | None:
    """coc.Timestamp -> aware datetime."""
    if t is None:
        return None
    raw = getattr(t, "time", t)
    return raw if raw.tzinfo else raw.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Crossing:
    """Which resets happened between a player's previous poll and this one."""

    day: bool = False  # a legend-day reset (05:00 UTC)
    week: bool = False  # a weekly tournament reset (Monday 05:00 UTC; also every season end)
    prev_day: str | None = None  # the legend day that just ended (only for a single-day step)


NO_CROSSING = Crossing()


def crossing_between(prev: datetime | None, now: datetime) -> Crossing:
    if prev is None:
        return NO_CROSSING
    prev_day, day = legend_day_id(prev), legend_day_id(now)
    if prev_day == day:
        return NO_CROSSING
    adjacent = legend_day_id(now - timedelta(days=1)) == prev_day
    return Crossing(True, ranked_week(prev).id != ranked_week(now).id, prev_day if adjacent else None)


def _sides(war: Any, clan_tag: str) -> tuple[Any, Any]:
    """(our side, their side); coc.py may orient a war from either clan."""
    if getattr(war.clan, "tag", None) == clan_tag:
        return war.clan, war.opponent
    return war.opponent, war.clan


class Collector:
    def __init__(self, bot: ClashHeroBot) -> None:
        self.bot = bot
        self.db = bot.db
        self.coc = bot.coc
        cfg = bot.config
        self._clans: dict[str, ClanSnap] = {}
        self._players: dict[str, PlayerSnap] = {}
        self._cwl_lineups: dict[str, set[str]] = {}
        self._battle_cursors: dict[str, Cursor] = {}
        self._battlelog_fetched: dict[str, datetime] = {}
        self._player_seen: dict[str, datetime] = {}  # when each player's snapshot was taken
        self._player_sources: dict[str, PlayerSource] = {}
        self.in_maintenance = False
        self.maintenance_since: datetime | None = None
        self.last_success: dict[str, datetime] = {}  # loop name -> end of last successful cycle
        self._sem = asyncio.Semaphore(10)

        self.clan_loop = tasks.loop(seconds=cfg.clan_poll_seconds)(self._poll_clans)
        self.player_loop = tasks.loop(seconds=cfg.player_poll_seconds)(self._poll_players)
        self.war_loop = tasks.loop(seconds=cfg.war_poll_seconds)(self._poll_wars)
        self.raid_loop = tasks.loop(seconds=cfg.raid_poll_seconds)(self._poll_raids)
        self._loops = (self.clan_loop, self.player_loop, self.war_loop, self.raid_loop)
        for loop in self._loops:
            loop.error(self._make_error_handler(loop))

    # ------------------------------------------------------------------ public API

    def register_player_source(self, source: PlayerSource, key: str | None = None) -> None:
        """Ask the collector to also poll the tags returned by ``source`` each player cycle.

        Registering again under the same key (by default the owning class and function name)
        replaces the old source, so reloading a cog doesn't leave a stale copy behind.
        """
        owner = getattr(source, "__self__", None)
        key = key or f"{type(owner).__name__ if owner else ''}.{getattr(source, '__name__', repr(source))}"
        self._player_sources[key] = source

    async def start(self) -> None:
        for key, data in (await self.db.load_snapshots("clan:")).items():
            self._clans[key[5:]] = ClanSnap.from_json(data)
        for key, data in (await self.db.load_snapshots("player:")).items():
            self._players[key[7:]] = PlayerSnap.from_json(data)
        for key, seen in (await self.db.load_snapshot_times("player:")).items():
            self._player_seen[key[7:]] = datetime.fromisoformat(seen)
        for key, data in (await self.db.load_snapshots("battles:")).items():
            self._battle_cursors[key[8:]] = Cursor.from_json(data)
        log.info(
            "Collector restored %d clan and %d player snapshots",
            len(self._clans),
            len(self._players),
        )
        for loop in self._loops:
            loop.start()

    def stop(self) -> None:
        for loop in self._loops:
            loop.cancel()

    def note_maintenance(self, now: datetime, *, announce: bool = True) -> None:
        """Mark the Clash API as down for maintenance (idempotent)."""
        if self.in_maintenance:
            return
        self.in_maintenance, self.maintenance_since = True, now
        if announce:
            self.dispatch(ev.MaintenanceChanged(started=True, at=now))

    def _cycle_done(self, name: str, now: datetime) -> None:
        if not self.in_maintenance:
            self.last_success[name] = now

    def dispatch(self, event: ev.ClashEvent) -> None:
        self.bot.dispatch(event.event_name, event)

    async def refresh_clan_now(self, clan_tag: str) -> None:
        """Poll one clan immediately (e.g. right after it's added)."""
        await self._poll_clan(clan_tag, datetime.now(UTC))

    # ------------------------------------------------------------------ helpers

    def _make_error_handler(self, loop):
        async def handler(error: BaseException) -> None:
            log.exception("Collector loop %s crashed; restarting", loop.coro.__name__, exc_info=error)
            await asyncio.sleep(30)
            loop.restart()

        return handler

    async def _guarded(self, coro: Awaitable[Any]) -> Any:
        """Run one API-touching unit of work, tracking maintenance and swallowing per-item
        errors so one bad tag doesn't stop the cycle."""
        async with self._sem:
            try:
                result = await coro
            except coc.Maintenance:
                self.note_maintenance(datetime.now(UTC))
                return None
            except (coc.NotFound, coc.PrivateWarLog, coc.Forbidden):
                return None
            except coc.HTTPException as e:
                log.warning("API error: %s", e)
                return None
            except Exception:
                log.exception("Collector task failed")
                return None
        if self.in_maintenance:
            since, self.in_maintenance, self.maintenance_since = self.maintenance_since, False, None
            self.dispatch(ev.MaintenanceChanged(started=False, at=datetime.now(UTC), since=since))
        return result

    _failure_logged: set[tuple[str, str]] = set()

    async def _isolated(self, coro: Awaitable[Any], what: str, tag: str) -> None:
        """Run one clan/player/war/raid update; log and skip it on error instead of failing the
        whole cycle. Unexpected API data after a game update shows up here first."""
        try:
            await coro
        except Exception:
            key = (what, tag)
            if key not in self._failure_logged:  # log each failing item once, not every minute
                self._failure_logged.add(key)
                log.exception("Skipping %s %s: couldn't process its data", what, tag)
        else:
            self._failure_logged.discard((what, tag))

    async def _record_activity(
        self, player_tag: str, clan_tag: str | None, now: datetime, amount: int = 1
    ) -> None:
        hour = now.strftime("%Y-%m-%dT%H")
        await self.db.execute(
            "INSERT INTO activity (player_tag, hour, clan_tag, count) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (player_tag, hour) DO UPDATE SET count = count + excluded.count",
            (player_tag, hour, clan_tag or "", amount),
        )
        await self.db.execute(
            "UPDATE players SET last_seen = ? WHERE player_tag = ?", (_iso(now), player_tag)
        )
        await self._bump_season(player_tag, clan_tag, now, activity=amount)

    async def _bump_season(self, player_tag: str, clan_tag: str | None, now: datetime, **cols: int):
        cols = {k: v for k, v in cols.items() if v}
        if not cols:
            return
        names = ", ".join(cols)
        placeholders = ", ".join("?" * len(cols))
        updates = ", ".join(f"{k} = {k} + excluded.{k}" for k in cols)
        await self.db.execute(
            f"INSERT INTO season_stats (season_id, player_tag, clan_tag, {names}) "
            f"VALUES (?, ?, ?, {placeholders}) "
            f"ON CONFLICT (season_id, player_tag, clan_tag) DO UPDATE SET {updates}",
            (game_season(now).id, player_tag, clan_tag or "", *cols.values()),
        )

    # ------------------------------------------------------------------ clans

    async def _poll_clans(self) -> None:
        now = datetime.now(UTC)
        tags = await self.db.all_clan_tags()
        await asyncio.gather(*(self._isolated(self._poll_clan(t, now), "clan", t) for t in tags))
        self._cycle_done("clans", now)

    async def _poll_clan(self, tag: str, now: datetime) -> None:
        clan = await self._guarded(self.coc.get_clan(tag))
        if clan is None:
            return
        new = ClanSnap.from_coc(clan)
        old = self._clans.get(tag)
        async with self.db.batch():
            await self._store_clan(clan, new, now)
            if old is not None:
                await self._apply_clan_diff(old, new, now)
            self._clans[tag] = new
            await self.db.save_snapshot(f"clan:{tag}", new.to_json(), _iso(now))

    async def _store_clan(self, clan: Any, snap: ClanSnap, now: datetime) -> None:
        badge = getattr(getattr(clan, "badge", None), "url", None)
        await self.db.execute(
            "INSERT INTO clans (clan_tag, name, level, member_count, war_league, capital_league, "
            "badge_url, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (clan_tag) DO "
            "UPDATE SET name = excluded.name, level = excluded.level, "
            "member_count = excluded.member_count, war_league = excluded.war_league, "
            "capital_league = excluded.capital_league, badge_url = excluded.badge_url, "
            "updated_at = excluded.updated_at",
            (
                snap.tag,
                snap.name,
                snap.level,
                len(snap.members),
                snap.war_league,
                snap.capital_league,
                badge,
                _iso(now),
            ),
        )
        # Lightweight member info so clan views work before the player loop catches up.
        await self.db.executemany(
            "INSERT INTO players (player_tag, name, town_hall, exp_level, trophies, league, "
            "clan_tag, clan_role, builder_trophies, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (player_tag) DO UPDATE SET name = excluded.name, "
            "town_hall = excluded.town_hall, exp_level = excluded.exp_level, "
            "trophies = excluded.trophies, league = excluded.league, "
            "clan_tag = excluded.clan_tag, clan_role = excluded.clan_role, "
            "builder_trophies = excluded.builder_trophies, updated_at = excluded.updated_at",
            [
                (
                    m.tag,
                    m.name,
                    m.town_hall,
                    m.exp_level,
                    m.trophies,
                    m.league,
                    snap.tag,
                    m.role,
                    m.builder_trophies,
                    _iso(now),
                )
                for m in snap.members.values()
            ],
        )

    async def _apply_clan_diff(self, old: ClanSnap, new: ClanSnap, now: datetime) -> None:
        d = diff_clan(old, new)
        count = len(new.members)
        for m in d.joined:
            await self.db.execute(
                "INSERT INTO clan_membership_events (clan_tag, player_tag, kind, occurred_at) "
                "VALUES (?, ?, 'join', ?)",
                (new.tag, m.tag, _iso(now)),
            )
            self.dispatch(ev.MemberJoined(new.tag, new.name, m, count))
        for m in d.left:
            await self.db.execute(
                "INSERT INTO clan_membership_events (clan_tag, player_tag, kind, occurred_at) "
                "VALUES (?, ?, 'leave', ?)",
                (new.tag, m.tag, _iso(now)),
            )
            await self.db.execute(
                "UPDATE players SET clan_tag = NULL, clan_role = NULL WHERE player_tag = ? AND clan_tag = ?",
                (m.tag, new.tag),
            )
            self.dispatch(ev.MemberLeft(new.tag, new.name, m, count))
        for before, after in d.role_changes:
            self.dispatch(ev.RoleChanged(new.tag, new.name, after.tag, after.name, before.role, after.role))
        for m, donated, received in d.donations:
            await self._bump_season(m.tag, new.tag, now, donations=donated, received=received)
            if donated:
                await self._record_activity(m.tag, new.tag, now)
            self.dispatch(ev.DonationsChanged(new.tag, new.name, m.tag, m.name, donated, received))
        for attr, a, b in d.clan_changes:
            self.dispatch(ev.ClanAttributeChanged(new.tag, new.name, attr, a, b))
        settings = diff_settings(old.settings, new.settings)
        if settings:
            self.dispatch(ev.ClanSettingsChanged(new.tag, new.name, tuple(settings)))

    # ------------------------------------------------------------------ players

    async def player_tags_to_poll(self) -> set[str]:
        rows = await self.db.fetchall(
            "SELECT player_tag FROM players WHERE clan_tag IN (SELECT clan_tag FROM guild_clans)"
        )
        tags = {r["player_tag"] for r in rows}
        tags.update(await self.db.all_linked_tags())
        for source in self._player_sources.values():
            try:
                tags.update(await source())
            except Exception:
                log.exception("Player source failed")
        return tags

    async def _poll_players(self) -> None:
        now = datetime.now(UTC)
        tags = await self.player_tags_to_poll()
        if not tags:
            return
        await asyncio.gather(*(self._isolated(self._poll_player(t, now), "player", t) for t in tags))
        self._cycle_done("players", now)

    async def _poll_player(self, tag: str, now: datetime) -> None:
        player = await self._guarded(self.coc.get_player(tag))
        if player is None:
            return
        new = PlayerSnap.from_coc(player)
        old = self._players.get(tag)
        logged: tuple[list[Battle], set[str]] | None = (
            None  # (ranked battles in log, keys new since last read)
        )
        if self._wants_battlelog(tag, old, new, now):
            battles = await self._fetch_battlelog(tag)
            if battles is not None:
                self._battlelog_fetched[tag] = now
                fresh, cursor = take_new(battles, self._battle_cursors.get(tag))
                self._battle_cursors[tag] = cursor
                note_unknown_types(fresh)
                logged = ([b for b in battles if b.ranked], {b.key for b in fresh})
        crossing = crossing_between(self._player_seen.get(tag), now) if old is not None else NO_CROSSING
        async with self.db.batch():
            await self._store_player(new, now)
            if logged is not None:
                await self._sync_battles(old, new, *logged, now, crossing)
            if old is not None:
                await self._apply_player_diff(
                    old, new, now, battle_log_read=logged is not None, crossing=crossing
                )
            else:
                await self._ensure_name(new, now)
            await self._update_legend_day(new, now)
            self._players[tag] = new
            self._player_seen[tag] = now
            await self.db.save_snapshot(f"player:{tag}", new.to_json(), _iso(now))
            if tag in self._battle_cursors:
                await self.db.save_snapshot(f"battles:{tag}", self._battle_cursors[tag].to_json(), _iso(now))

    BATTLELOG_REFRESH = timedelta(minutes=10)

    def _wants_battlelog(self, tag: str, old: PlayerSnap | None, new: PlayerSnap, now: datetime) -> bool:
        """Read the battle log for ranked players when trophies moved, and periodically otherwise
        (a 0-trophy defense doesn't move trophies)."""
        if not (is_tracked_ranked(new.league) or (old is not None and is_tracked_ranked(old.league))):
            return False
        if tag not in self._battle_cursors or old is None or old.trophies != new.trophies:
            return True
        last = self._battlelog_fetched.get(tag)
        return last is None or now - last >= self.BATTLELOG_REFRESH

    async def _fetch_battlelog(self, tag: str) -> list[Battle] | None:
        http = getattr(self.coc, "http", None)
        if http is None:
            return None
        route = coc.http.Route("GET", http.base_url, f"/players/{quote(tag, safe='')}/battlelog")
        data = await self._guarded(http.request(route))
        if not isinstance(data, dict):
            return None
        return parse_battles(data.get("items") or [])

    async def _store_player(self, p: PlayerSnap, now: datetime) -> None:
        await self.db.execute(
            "INSERT INTO players (player_tag, name, town_hall, exp_level, trophies, best_trophies, "
            "league, war_stars, attack_wins, defense_wins, war_opted_in, clan_tag, clan_role, "
            "heroes_total, builder_hall, builder_trophies, builder_league, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (player_tag) DO UPDATE SET "
            "name = excluded.name, town_hall = excluded.town_hall, "
            "exp_level = excluded.exp_level, trophies = excluded.trophies, "
            "best_trophies = excluded.best_trophies, league = excluded.league, "
            "war_stars = excluded.war_stars, attack_wins = excluded.attack_wins, "
            "defense_wins = excluded.defense_wins, war_opted_in = excluded.war_opted_in, "
            "clan_tag = excluded.clan_tag, clan_role = excluded.clan_role, "
            "heroes_total = excluded.heroes_total, builder_hall = excluded.builder_hall, "
            "builder_trophies = excluded.builder_trophies, "
            "builder_league = excluded.builder_league, updated_at = excluded.updated_at",
            (
                p.tag,
                p.name,
                p.town_hall,
                p.exp_level,
                p.trophies,
                p.best_trophies,
                p.league,
                p.war_stars,
                p.attack_wins,
                p.defense_wins,
                None if p.war_opted_in is None else int(p.war_opted_in),
                p.clan_tag,
                p.clan_role,
                p.heroes_total,
                p.builder_hall,
                p.builder_trophies,
                p.builder_league,
                _iso(now),
            ),
        )

    async def _ensure_name(self, p: PlayerSnap, now: datetime) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO player_names (player_tag, name, first_seen) VALUES (?, ?, ?)",
            (p.tag, p.name, _iso(now)),
        )

    async def _apply_player_diff(
        self,
        old: PlayerSnap,
        new: PlayerSnap,
        now: datetime,
        *,
        battle_log_read: bool = False,
        crossing: Crossing = NO_CROSSING,
    ) -> None:
        clan = new.clan_tag
        if old.clan_tag != new.clan_tag:
            self.dispatch(ev.PlayerClanChanged(new.tag, new.name, old.clan_tag, new.clan_tag, new.clan_name))
        if old.units:  # need a baseline; snapshots from older versions have none
            changes = diff_units(old.units, new.units)
            if changes:
                self.dispatch(ev.UnitsUpgraded(clan, new.tag, new.name, new.town_hall, tuple(changes)))
            boosted = tuple(t for t in new.super_troops if t not in old.super_troops)
            if boosted:
                self.dispatch(ev.SuperTroopsBoosted(clan, new.tag, new.name, boosted))
        if old.name != new.name:
            await self._ensure_name(new, now)
            self.dispatch(ev.NameChanged(clan, new.tag, old.name, new.name))
        if new.town_hall > old.town_hall:
            self.dispatch(ev.TownHallUpgraded(clan, new.tag, new.name, old.town_hall, new.town_hall))
        if old.league != new.league:
            self.dispatch(ev.LeagueChanged(clan, new.tag, new.name, old.league, new.league, new.trophies))
            if is_legend_one(new.league) and not is_legend_one(old.league):
                await self.db.execute(
                    "INSERT OR IGNORE INTO legend_promotions (player_tag, legend_day) VALUES (?, ?)",
                    (new.tag, legend_day_id(now)),
                )
        if old.war_opted_in is not None and old.war_opted_in != new.war_opted_in:
            self.dispatch(ev.WarPreferenceChanged(clan, new.tag, new.name, bool(new.war_opted_in)))

        games_delta = new.games_points - old.games_points
        if games_delta > 0:
            games = clan_games(now)
            games_id = games.id if now >= games.start else None
            if games_id:
                await self.db.execute(
                    "INSERT INTO clan_games_points (games_id, player_tag, clan_tag, points, "
                    "updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT (games_id, player_tag) DO "
                    "UPDATE SET points = points + excluded.points, clan_tag = excluded.clan_tag, "
                    "updated_at = excluded.updated_at",
                    (games_id, new.tag, clan or "", games_delta, _iso(now)),
                )
                total = await self.db.fetchval(
                    "SELECT points FROM clan_games_points WHERE games_id = ? AND player_tag = ?",
                    (games_id, new.tag),
                    0,
                )
                self.dispatch(ev.ClanGamesProgress(clan, new.tag, new.name, games_delta, total))

        cap = counter_delta(old.capital_contributions, new.capital_contributions)
        if cap > 0 and new.capital_contributions >= old.capital_contributions:
            await self._bump_season(new.tag, clan, now, capital_gold=cap)
            self.dispatch(ev.CapitalContribution(clan, new.tag, new.name, cap))

        wins = new.attack_wins - old.attack_wins
        if wins > 0:
            await self._bump_season(new.tag, clan, now, attack_wins=wins)

        if has_activity(old, new):
            await self._record_activity(new.tag, clan, now)

        if not battle_log_read and not crossing.day:
            # Battle log unavailable this poll: infer from the trophy change. Changes across a
            # reset are autofill or reset adjustments, not battles, so they're skipped here.
            await self._record_inferred(old, new, now)

    async def _record_inferred(self, old: PlayerSnap, new: PlayerSnap, now: datetime) -> None:
        clan = new.clan_tag
        battles = infer_battles(old, new)
        if battles:
            day = legend_day_id(now)
            season = game_season(now).id
            running = old.trophies
            for b in battles:
                running += b.trophies
                await self.db.execute(
                    "INSERT INTO legend_events (player_tag, legend_day, season_id, kind, "
                    "trophies, trophies_after, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (new.tag, day, season, b.kind, b.trophies, running, _iso(now)),
                )
                self.dispatch(ev.LegendBattle(new.tag, new.name, clan, b.kind, b.trophies, running, now, day))

    async def _sync_battles(
        self,
        old: PlayerSnap | None,
        new: PlayerSnap,
        ranked_log: list[Battle],
        fresh_keys: set[str],
        now: datetime,
        crossing: Crossing = NO_CROSSING,
    ) -> None:
        """Make legend_events match the player's battle log for the current season.

        Records every ranked battle in the log that isn't stored yet (backfilling the last ~2 days
        on first sight and filling gaps after downtime). Alerts go out only for battles new since
        the previous read. Trophies come from the Legend I rule, or from the observed trophy
        change for other tiers. Trophy changes no battle explains are, depending on timing:
        carried until their battle shows up; the game's autofilled defenses (at a daily reset);
        or a reset adjustment such as the weekly floor or season reset to 5000 (ignored).
        """
        cursor = self._battle_cursors[new.tag]
        season_start = game_season(now).start
        ranked = sorted((b for b in ranked_log if b.occurred_at >= season_start), key=lambda b: b.timestamp)
        existing: dict[str, int] = {}
        if ranked:
            rows = await self.db.fetchall(
                "SELECT battle_time, kind, opponent_tag, trophies FROM legend_events "
                "WHERE player_tag = ? AND battle_time IS NOT NULL AND battle_time >= ?",
                (new.tag, ranked[0].timestamp),
            )
            existing = {
                f"{r['battle_time']}|{'A' if r['kind'] == 'attack' else 'D'}|{r['opponent_tag']}": r[
                    "trophies"
                ]
                for r in rows
            }
        missing = [b for b in ranked if b.key not in existing]

        delta = new.trophies - old.trophies if old is not None else 0
        plausible = old is not None and old.league == new.league and abs(delta) <= MAX_PLAUSIBLE_DELTA
        if crossing.week:  # weekly floor / season reset: the raw change isn't battles
            cursor.pending_delta, cursor.pending_since = 0, None
            total = 0
        else:
            total = cursor.pending_delta + (delta if plausible else 0)
        values = battle_trophies(missing, fresh_keys, total, defenses_gain=defenses_gain(new.league))
        explained = sum(v for b, v in zip(missing, values, strict=True) if b.key in fresh_keys)
        residual = total - explained

        if crossing.day and old is not None:
            await self._record_autofill(old, crossing, residual)
            cursor.pending_delta, cursor.pending_since = 0, None
        elif residual and not crossing.week:
            # Trophies moved but the battle isn't in the log yet: carry the change.
            cursor.pending_delta = residual
            cursor.pending_since = cursor.pending_since or _iso(now)
            if abs(residual) > MAX_PLAUSIBLE_DELTA:
                cursor.pending_delta, cursor.pending_since = 0, None
            elif old is not None and now - datetime.fromisoformat(cursor.pending_since) >= PENDING_TIMEOUT:
                # No matching battle ever appeared (e.g. a battle type this version doesn't know
                # yet): record the change the old, inferred way.
                cursor.pending_delta, cursor.pending_since = 0, None
                await self._record_inferred(replace(new, trophies=new.trophies - residual), new, now)
        else:
            cursor.pending_delta, cursor.pending_since = 0, None
        if not missing:
            return

        trophies = {**existing, **{b.key: v for b, v in zip(missing, values, strict=True)}}
        after: dict[str, int] = {}
        running = new.trophies
        for b in reversed(ranked):  # walk back from the current trophy count
            after[b.key] = running
            running -= trophies.get(b.key, 0)

        # Trophy-change guesses covering the same window are superseded by the real battles.
        await self.db.execute(
            "DELETE FROM legend_events WHERE player_tag = ? AND battle_time IS NULL "
            "AND (battle_type IS NULL OR battle_type != 'autofill') AND occurred_at >= ?",
            (new.tag, _iso(missing[0].occurred_at - timedelta(minutes=15))),
        )
        for b, t in zip(missing, values, strict=True):
            at = b.occurred_at
            day = legend_day_id(at)
            event_id = await self.db.execute(
                "INSERT INTO legend_events (player_tag, legend_day, season_id, kind, trophies, "
                "trophies_after, occurred_at, battle_type, opponent_tag, opponent_name, opponent_th, "
                "stars, destruction, army_share_code, battle_time) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new.tag,
                    day,
                    game_season(at).id,
                    b.kind,
                    t,
                    after[b.key],
                    _iso(at),
                    b.battle_type,
                    b.opponent_tag,
                    b.opponent_name,
                    b.opponent_th,
                    b.stars,
                    b.destruction,
                    b.army_share_code,
                    b.timestamp,
                ),
            )
            if b.key in fresh_keys:
                self.dispatch(
                    ev.LegendBattle(
                        new.tag,
                        new.name,
                        new.clan_tag,
                        b.kind,
                        t,
                        after[b.key],
                        at,
                        day,
                        event_id=event_id,
                        battle_type=b.battle_type,
                        opponent_tag=b.opponent_tag,
                        opponent_name=b.opponent_name,
                        opponent_th=b.opponent_th,
                        stars=b.stars,
                        destruction=b.destruction,
                        army_share_code=b.army_share_code,
                    )
                )
        for day in sorted({legend_day_id(b.occurred_at) for b in missing}):
            await self._rebuild_legend_day(new.tag, day)

    async def _record_autofill(self, old: PlayerSnap, crossing: Crossing, residual: int) -> None:
        """Record the defenses the game auto-filled for the legend day that just ended.

        Legend I: missing = daily cap (8, or 7 on a promotion day) minus real defenses; each is
        worth the season's average defense loss. On a normal day the unexplained trophy drop is the
        exact total; at a weekly/season reset that change is mixed with the reset itself, so the
        expected amount is used instead. Below Legend I defenses gain trophies and there's no daily
        cap, so an unexplained gain at a (non-weekly) daily reset is recorded as autofill.
        """
        day = crossing.prev_day
        if day is None or not is_tracked_ranked(old.league):
            return
        tag = old.tag
        rows = await self.db.fetchall(
            "SELECT kind, battle_type FROM legend_events WHERE player_tag = ? AND legend_day = ?", (tag, day)
        )
        if any(r["battle_type"] == "autofill" for r in rows):
            return
        real_defenses = sum(1 for r in rows if r["kind"] == "defense")
        day_start, day_end = legend_day_bounds(datetime.fromisoformat(f"{day}T12:00:00+00:00"))
        if is_legend_one(old.league):
            first_day = await self.db.fetchval(
                "SELECT 1 FROM legend_promotions WHERE player_tag = ? AND legend_day = ?", (tag, day)
            )
            count = (daily_attack_cap(old.league, first_day=bool(first_day)) or 0) - real_defenses
            if count <= 0:
                return
            if crossing.week:
                if not rows:  # no battles that day: shielded or not tracked; nothing to estimate
                    return
                avg = await self.db.fetchval(
                    "SELECT AVG(trophies) FROM legend_events WHERE player_tag = ? AND kind = 'defense' "
                    "AND season_id = ? AND (battle_type IS NULL OR battle_type != 'autofill')",
                    (tag, game_season(day_start).id),
                )
                if avg is None:
                    return
                total = round(avg * count)
            else:
                if residual >= 0:  # no unexplained loss: shielded day, or nothing was filled
                    return
                total = residual
        else:
            if crossing.week or residual <= 0:
                return
            avg = await self.db.fetchval(
                "SELECT AVG(trophies) FROM legend_events WHERE player_tag = ? AND kind = 'defense' "
                "AND occurred_at >= ? AND trophies > 0",
                (tag, _iso(ranked_week(day_start).start)),
            )
            count = max(1, round(residual / avg)) if avg else 1
            total = residual
        base, rem = divmod(abs(total), count)
        sign = 1 if total >= 0 else -1
        end_row = await self.db.fetchone(
            "SELECT trophies_after FROM legend_events WHERE player_tag = ? AND legend_day = ? "
            "ORDER BY occurred_at DESC, id DESC LIMIT 1",
            (tag, day),
        )
        running = end_row["trophies_after"] if end_row else old.trophies
        at = day_end - timedelta(seconds=1)
        for i in range(count):
            value = sign * (base + (1 if i < rem else 0))
            running += value
            await self.db.execute(
                "INSERT INTO legend_events (player_tag, legend_day, season_id, kind, trophies, "
                "trophies_after, occurred_at, battle_type) VALUES (?, ?, ?, 'defense', ?, ?, ?, 'autofill')",
                (tag, day, game_season(day_start).id, value, running, _iso(at)),
            )
        await self._rebuild_legend_day(tag, day)

    async def _rebuild_legend_day(self, player_tag: str, day: str) -> None:
        """Start/end trophies for a legend day from its recorded battles."""
        rows = await self.db.fetchall(
            "SELECT trophies, trophies_after, season_id FROM legend_events "
            "WHERE player_tag = ? AND legend_day = ? ORDER BY occurred_at, id",
            (player_tag, day),
        )
        if not rows:
            return
        start = rows[0]["trophies_after"] - rows[0]["trophies"]
        await self.db.execute(
            "INSERT INTO legend_days (player_tag, legend_day, season_id, start_trophies, end_trophies) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (player_tag, legend_day) DO UPDATE SET "
            "start_trophies = excluded.start_trophies, end_trophies = excluded.end_trophies",
            (player_tag, day, rows[0]["season_id"], start, rows[-1]["trophies_after"]),
        )

    async def _update_legend_day(self, p: PlayerSnap, now: datetime) -> None:
        if not is_tracked_ranked(p.league):
            return
        day = legend_day_id(now)
        net_today = await self.db.fetchval(
            "SELECT COALESCE(SUM(trophies), 0) FROM legend_events WHERE player_tag = ? AND legend_day = ?",
            (p.tag, day),
            0,
        )
        await self.db.execute(
            "INSERT INTO legend_days (player_tag, legend_day, season_id, start_trophies, "
            "end_trophies) VALUES (?, ?, ?, ?, ?) ON CONFLICT (player_tag, legend_day) DO "
            "UPDATE SET end_trophies = excluded.end_trophies",
            (p.tag, day, game_season(now).id, p.trophies - net_today, p.trophies),
        )

    # ------------------------------------------------------------------ wars

    async def _poll_wars(self) -> None:
        now = datetime.now(UTC)
        tags = await self.db.all_clan_tags()
        await asyncio.gather(*(self._isolated(self._poll_war(t, now), "war", t) for t in tags))
        self._cycle_done("wars", now)

    async def _poll_war(self, clan_tag: str, now: datetime) -> None:
        war = await self._guarded(self.coc.get_current_war(clan_tag))
        if war is None or getattr(war, "state", None) in (None, "notInWar"):
            return
        group = None
        if getattr(war, "is_cwl", False):
            group = await self._check_cwl_lineup(clan_tag)
        prep = _ts(war.preparation_start_time)
        if prep is None:
            return
        async with self.db.batch():
            war_row, old_state, is_new = await self._upsert_war(clan_tag, war, prep, group)
            await self._store_war_members(war_row["id"], war, clan_tag)
            new_attacks = await self._store_war_attacks(war_row, war, now, silent=is_new)
            war_row = dict(await self.db.fetchone("SELECT * FROM wars WHERE id = ?", (war_row["id"],)))
        for attack, att_name, def_name in new_attacks:
            self.dispatch(ev.WarAttackMade(clan_tag, war_row, attack, att_name, def_name))
        if old_state != war_row["state"]:
            self.dispatch(ev.WarStateChanged(clan_tag, war_row, old_state, war_row["state"]))
        self.dispatch(ev.WarUpdated(clan_tag, war_row))

    async def _upsert_war(self, clan_tag: str, war: Any, prep: datetime, group: Any = None):
        clan, opp = _sides(war, clan_tag)
        state = str(getattr(war.state, "value", war.state))
        war_type = "cwl" if war.is_cwl else str(getattr(war.type, "value", war.type) or "random")
        result = None
        if state == "warEnded":
            if clan.stars != opp.stars:
                result = "win" if clan.stars > opp.stars else "lose"
            elif clan.destruction != opp.destruction:
                result = "win" if clan.destruction > opp.destruction else "lose"
            else:
                result = "tie"
        existing = await self.db.fetchone(
            "SELECT * FROM wars WHERE clan_tag = ? AND preparation_start = ?",
            (clan_tag, _iso(prep)),
        )
        cwl_round = None
        group = group or getattr(war, "league_group", None)
        if war.is_cwl and group is not None:
            for i, rnd in enumerate(getattr(group, "rounds", None) or []):
                if war.war_tag in rnd:
                    cwl_round = i + 1
        values = dict(
            opponent_tag=opp.tag,
            opponent_name=opp.name,
            war_type=war_type,
            war_tag=getattr(war, "war_tag", None),
            cwl_season=cwl_season_id(prep) if war.is_cwl else None,
            cwl_round=cwl_round,
            state=state,
            team_size=war.team_size,
            attacks_per_member=getattr(war, "attacks_per_member", None) or (1 if war.is_cwl else 2),
            start_time=_iso(_ts(war.start_time)),
            end_time=_iso(_ts(war.end_time)),
            clan_stars=clan.stars,
            clan_destruction=clan.destruction,
            clan_attacks=getattr(clan, "attacks_used", 0) or 0,
            opponent_stars=opp.stars,
            opponent_destruction=opp.destruction,
            opponent_attacks=getattr(opp, "attacks_used", 0) or 0,
            result=result,
        )
        if existing is None:
            cols = ", ".join(values)
            qs = ", ".join("?" * len(values))
            await self.db.execute(
                f"INSERT INTO wars (clan_tag, preparation_start, {cols}) VALUES (?, ?, {qs})",
                (clan_tag, _iso(prep), *values.values()),
            )
            row = await self.db.fetchone(
                "SELECT * FROM wars WHERE clan_tag = ? AND preparation_start = ?",
                (clan_tag, _iso(prep)),
            )
            return row, None, True
        sets = ", ".join(f"{k} = ?" for k in values)
        await self.db.execute(f"UPDATE wars SET {sets} WHERE id = ?", (*values.values(), existing["id"]))
        return existing, existing["state"], False

    async def _store_war_members(self, war_id: int, war: Any, clan_tag: str) -> None:
        ours, theirs = _sides(war, clan_tag)
        rows = [
            (war_id, m.tag, m.name, m.town_hall, m.map_position, is_opp)
            for side, is_opp in ((ours, 0), (theirs, 1))
            for m in side.members or []
        ]
        await self.db.executemany(
            "INSERT INTO war_members (war_id, player_tag, name, town_hall, map_position, "
            "is_opponent) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (war_id, player_tag) DO UPDATE "
            "SET name = excluded.name, town_hall = excluded.town_hall, "
            "map_position = excluded.map_position",
            rows,
        )

    async def _store_war_attacks(self, war_row, war: Any, now: datetime, *, silent: bool):
        war_id = war_row["id"]
        clan_tag = war_row["clan_tag"]
        known = {
            r["attack_order"]
            for r in await self.db.fetchall(
                "SELECT attack_order FROM war_attacks WHERE war_id = ?", (war_id,)
            )
        }
        members = {}
        for side in (war.clan, war.opponent):
            for m in side.members or []:
                members[m.tag] = m
        our_tags = {m.tag for m in (_sides(war, clan_tag)[0].members or [])}
        best: dict[str, int] = {}
        new_attacks = []
        for a in sorted(war.attacks or [], key=lambda x: x.order):
            prev_best = best.get(a.defender_tag, 0)
            best[a.defender_tag] = max(prev_best, a.stars)
            if a.order in known:
                continue
            att, dfn = members.get(a.attacker_tag), members.get(a.defender_tag)
            is_opp = int(a.attacker_tag not in our_tags)
            row = dict(
                war_id=war_id,
                attack_order=a.order,
                attacker_tag=a.attacker_tag,
                defender_tag=a.defender_tag,
                stars=a.stars,
                destruction=a.destruction,
                duration=getattr(a, "duration", None),
                is_fresh=int(bool(a.is_fresh_attack)),
                new_stars=max(0, a.stars - prev_best),
                attacker_th=getattr(att, "town_hall", None),
                defender_th=getattr(dfn, "town_hall", None),
                attacker_pos=getattr(att, "map_position", None),
                defender_pos=getattr(dfn, "map_position", None),
                is_opponent=is_opp,
                seen_at=_iso(now),
            )
            cols = ", ".join(row)
            qs = ", ".join("?" * len(row))
            await self.db.execute(
                f"INSERT OR IGNORE INTO war_attacks ({cols}) VALUES ({qs})", tuple(row.values())
            )
            if not silent:
                new_attacks.append((row, getattr(att, "name", "?"), getattr(dfn, "name", "?")))
        return new_attacks

    async def _check_cwl_lineup(self, clan_tag: str) -> Any:
        """Fetch the CWL group, dispatch lineup changes, and return the group."""
        group = await self._guarded(self.coc.get_league_group(clan_tag))
        if group is None:
            return None
        ours = next((c for c in group.clans if c.tag == clan_tag), None)
        if ours is None:
            return group
        lineup = {m.tag for m in ours.members}
        prev = self._cwl_lineups.get(clan_tag)
        self._cwl_lineups[clan_tag] = lineup
        if prev is not None and prev != lineup:
            self.dispatch(
                ev.CWLLineupChanged(clan_tag, cwl_season_id(), sorted(lineup - prev), sorted(prev - lineup))
            )
        return group

    # ------------------------------------------------------------------ capital raids

    async def _poll_raids(self) -> None:
        tags = await self.db.all_clan_tags()
        await asyncio.gather(*(self._isolated(self._poll_raid(t), "raid", t) for t in tags))
        self._cycle_done("raids", datetime.now(UTC))

    async def _poll_raid(self, clan_tag: str) -> None:
        log_ = await self._guarded(self.coc.get_raid_log(clan_tag, limit=1))
        if not log_:
            return
        entry = log_[0]
        start = _iso(_ts(entry.start_time))
        state = str(getattr(entry.state, "value", entry.state))
        async with self.db.batch():
            prev_state = await self.db.fetchval(
                "SELECT state FROM raid_seasons WHERE clan_tag = ? AND start_time = ?",
                (clan_tag, start),
            )
            await self.db.execute(
                "INSERT INTO raid_seasons (clan_tag, start_time, end_time, state, total_loot, "
                "attack_count, districts_destroyed, offensive_reward, defensive_reward) "
                "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT (clan_tag, start_time) DO UPDATE SET "
                "state = excluded.state, total_loot = excluded.total_loot, "
                "attack_count = excluded.attack_count, "
                "districts_destroyed = excluded.districts_destroyed, "
                "offensive_reward = excluded.offensive_reward, "
                "defensive_reward = excluded.defensive_reward",
                (
                    clan_tag,
                    start,
                    _iso(_ts(entry.end_time)),
                    state,
                    entry.total_loot,
                    entry.attack_count,
                    entry.destroyed_district_count,
                    getattr(entry, "offensive_reward", None),
                    getattr(entry, "defensive_reward", None),
                ),
            )
            old = {
                r["player_tag"]: r
                for r in await self.db.fetchall(
                    "SELECT * FROM raid_members WHERE clan_tag = ? AND start_time = ?",
                    (clan_tag, start),
                )
            }
            events = []
            for m in entry.members or []:
                prev = old.get(m.tag)
                d_att = m.attack_count - (prev["attacks"] if prev else 0)
                d_loot = m.capital_resources_looted - (prev["loot"] if prev else 0)
                await self.db.execute(
                    "INSERT INTO raid_members (clan_tag, start_time, player_tag, name, attacks, "
                    "attack_limit, loot) VALUES (?,?,?,?,?,?,?) ON CONFLICT "
                    "(clan_tag, start_time, player_tag) DO UPDATE SET name = excluded.name, "
                    "attacks = excluded.attacks, attack_limit = excluded.attack_limit, "
                    "loot = excluded.loot",
                    (
                        clan_tag,
                        start,
                        m.tag,
                        m.name,
                        m.attack_count,
                        m.attack_limit + (getattr(m, "bonus_attack_limit", 0) or 0),
                        m.capital_resources_looted,
                    ),
                )
                if prev_state is not None and d_att > 0:
                    events.append(
                        ev.RaidAttacksMade(
                            clan_tag,
                            m.tag,
                            m.name,
                            d_att,
                            d_loot,
                            m.attack_count,
                            m.attack_limit + (getattr(m, "bonus_attack_limit", 0) or 0),
                        )
                    )
        for e in events:
            self.dispatch(e)
        if prev_state == "ongoing" and state != "ongoing":
            self.dispatch(ev.RaidWeekendEnded(clan_tag, start))
