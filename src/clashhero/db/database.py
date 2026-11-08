"""Async SQLite access.

Core queries live here. Feature-specific queries belong in ``clashhero/repos/<feature>.py``
as plain async functions taking a :class:`Database`; feature tables go in
``db/schema_<feature>.sql`` (loaded automatically after ``schema.sql``).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from importlib import resources
from pathlib import Path
from typing import Any

import aiosqlite

Row = aiosqlite.Row


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = path if str(path) == ":memory:" else Path(path)
        self._conn: aiosqlite.Connection | None = None
        self._batch_depth = 0

    # ------------------------------------------------------------------ lifecycle

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected; call connect() first")
        return self._conn

    async def connect(self) -> None:
        if isinstance(self.path, Path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.execute("PRAGMA journal_mode = WAL")
        for script in self._schema_scripts():
            await self._conn.executescript(script)
        await self._add_missing_columns()
        await self._conn.commit()

    @staticmethod
    def _schema_scripts() -> list[str]:
        pkg = resources.files("clashhero.db")
        names = sorted(
            p.name for p in pkg.iterdir() if p.name.startswith("schema_") and p.name.endswith(".sql")
        )
        return [pkg.joinpath("schema.sql").read_text()] + [pkg.joinpath(n).read_text() for n in names]

    @staticmethod
    def _declared_columns() -> list[tuple[str, str, str]]:
        """(table, column, declaration) from every ``db/columns_*.py`` module's ``COLUMNS``."""
        import importlib
        import pkgutil

        import clashhero.db as pkg

        out: list[tuple[str, str, str]] = []
        for m in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
            if m.name.startswith("columns_"):
                out.extend(importlib.import_module(f"{pkg.__name__}.{m.name}").COLUMNS)
        return out

    async def _add_missing_columns(self) -> None:
        """Columns added after a table first shipped. ``CREATE TABLE IF NOT EXISTS`` never alters an
        existing table, so new columns are declared in ``db/columns_<feature>.py`` instead of being
        edited into the CREATE statement, and added here if missing (fresh or existing databases)."""
        for table, column, decl in self._declared_columns():
            async with self.conn.execute(f"PRAGMA table_info({table})") as cur:
                existing = {r[1] for r in await cur.fetchall()}
            if existing and column not in existing:
                await self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------ generic helpers

    @asynccontextmanager
    async def batch(self) -> AsyncIterator[None]:
        """Group many writes into one commit (rolls back on error)."""
        self._batch_depth += 1
        try:
            yield
        except BaseException:
            self._batch_depth -= 1
            if self._batch_depth == 0:
                await self.conn.rollback()
            raise
        else:
            self._batch_depth -= 1
            if self._batch_depth == 0:
                await self.conn.commit()

    async def _maybe_commit(self) -> None:
        if self._batch_depth == 0:
            await self.conn.commit()

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        """Run a write statement. Returns lastrowid. Commits unless inside ``batch()``."""
        cur = await self.conn.execute(sql, tuple(params))
        await self._maybe_commit()
        return cur.lastrowid or 0

    async def execute_rowcount(self, sql: str, params: Iterable[Any] = ()) -> int:
        cur = await self.conn.execute(sql, tuple(params))
        await self._maybe_commit()
        return cur.rowcount

    async def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        await self.conn.executemany(sql, [tuple(r) for r in rows])
        await self._maybe_commit()

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> Row | None:
        async with self.conn.execute(sql, tuple(params)) as cur:
            return await cur.fetchone()

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[Row]:
        async with self.conn.execute(sql, tuple(params)) as cur:
            return list(await cur.fetchall())

    async def fetchval(self, sql: str, params: Iterable[Any] = (), default: Any = None) -> Any:
        row = await self.fetchone(sql, params)
        return default if row is None else row[0]

    # ------------------------------------------------------------------ guild settings

    async def get_guild_settings(self, guild_id: int) -> Row:
        await self.execute("INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)", (guild_id,))
        row = await self.fetchone("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,))
        assert row is not None
        return row

    _SETTINGS_COLUMNS = frozenset(
        {
            "bot_manager_role",
            "roster_manager_role",
            "links_manager_role",
            "flags_manager_role",
            "embed_color",
            "timezone_offset_min",
        }
    )

    async def set_guild_setting(self, guild_id: int, column: str, value: Any) -> None:
        if column not in self._SETTINGS_COLUMNS:
            raise ValueError(f"Unknown guild setting {column!r}")
        await self.get_guild_settings(guild_id)
        await self.execute(f"UPDATE guild_settings SET {column} = ? WHERE guild_id = ?", (value, guild_id))

    # ------------------------------------------------------------------ guild clans

    async def add_guild_clan(self, guild_id: int, clan_tag: str) -> None:
        await self.execute(
            "INSERT OR IGNORE INTO guild_clans (guild_id, clan_tag) VALUES (?, ?)",
            (guild_id, clan_tag),
        )

    async def remove_guild_clan(self, guild_id: int, clan_tag: str) -> None:
        """Remove a clan and every per-clan feature configured for it in this guild."""
        async with self.batch():
            for table in ("guild_clans", "clan_logs"):
                await self.execute(
                    f"DELETE FROM {table} WHERE guild_id = ? AND clan_tag = ?",
                    (guild_id, clan_tag),
                )
            await self.execute(
                "DELETE FROM channel_clans WHERE guild_id = ? AND clan_tag = ?",
                (guild_id, clan_tag),
            )
            await self.execute(
                "DELETE FROM live_messages WHERE guild_id = ? AND scope = ?", (guild_id, clan_tag)
            )

    async def get_guild_clan_rows(self, guild_id: int) -> list[Row]:
        """Guild clans joined with cached clan info, in display order."""
        return await self.fetchall(
            "SELECT g.*, c.name, c.level, c.member_count, c.war_league, c.badge_url "
            "FROM guild_clans g LEFT JOIN clans c ON c.clan_tag = g.clan_tag "
            "WHERE g.guild_id = ? ORDER BY g.category IS NULL, g.category, g.sort_order, "
            "g.added_at",
            (guild_id,),
        )

    async def get_guild_clans(self, guild_id: int) -> list[str]:
        return [r["clan_tag"] for r in await self.get_guild_clan_rows(guild_id)]

    async def all_clan_tags(self) -> list[str]:
        rows = await self.fetchall("SELECT DISTINCT clan_tag FROM guild_clans")
        return [r["clan_tag"] for r in rows]

    async def guilds_for_clan(self, clan_tag: str) -> list[int]:
        rows = await self.fetchall("SELECT guild_id FROM guild_clans WHERE clan_tag = ?", (clan_tag,))
        return [r["guild_id"] for r in rows]

    async def set_clan_alias(self, guild_id: int, clan_tag: str, alias: str | None) -> None:
        await self.execute(
            "UPDATE guild_clans SET alias = ? WHERE guild_id = ? AND clan_tag = ?",
            (alias, guild_id, clan_tag),
        )

    async def set_clan_category(
        self, guild_id: int, clan_tag: str, category: str | None, sort_order: int = 0
    ) -> None:
        await self.execute(
            "UPDATE guild_clans SET category = ?, sort_order = ? WHERE guild_id = ? AND clan_tag = ?",
            (category, sort_order, guild_id, clan_tag),
        )

    async def clan_by_alias(self, guild_id: int, alias: str) -> str | None:
        return await self.fetchval(
            "SELECT clan_tag FROM guild_clans WHERE guild_id = ? AND alias = ? COLLATE NOCASE",
            (guild_id, alias),
        )

    async def set_channel_clan(self, channel_id: int, guild_id: int, clan_tag: str | None) -> None:
        if clan_tag is None:
            await self.execute("DELETE FROM channel_clans WHERE channel_id = ?", (channel_id,))
        else:
            await self.execute(
                "INSERT INTO channel_clans (channel_id, guild_id, clan_tag) VALUES (?, ?, ?) "
                "ON CONFLICT (channel_id) DO UPDATE SET clan_tag = excluded.clan_tag",
                (channel_id, guild_id, clan_tag),
            )

    async def get_channel_clan(self, channel_id: int) -> str | None:
        return await self.fetchval("SELECT clan_tag FROM channel_clans WHERE channel_id = ?", (channel_id,))

    # ------------------------------------------------------------------ player links

    async def link_player(self, player_tag: str, discord_user_id: int, *, verified: bool = False) -> None:
        """Link (or re-link) an account. A user's first account becomes their default."""
        has_default = await self.fetchval(
            "SELECT 1 FROM player_links WHERE discord_user_id = ? AND is_default = 1 AND player_tag != ?",
            (discord_user_id, player_tag),
        )
        await self.execute(
            "INSERT INTO player_links (player_tag, discord_user_id, is_default, verified) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (player_tag) DO UPDATE SET "
            "discord_user_id = excluded.discord_user_id, is_default = excluded.is_default, "
            "verified = MAX(excluded.verified, CASE WHEN player_links.discord_user_id = "
            "excluded.discord_user_id THEN player_links.verified ELSE 0 END), "
            "linked_at = CASE WHEN player_links.discord_user_id = excluded.discord_user_id "
            "THEN player_links.linked_at ELSE datetime('now') END",
            (player_tag, discord_user_id, 0 if has_default else 1, int(verified)),
        )

    async def set_default_account(self, discord_user_id: int, player_tag: str) -> None:
        async with self.batch():
            await self.execute(
                "UPDATE player_links SET is_default = (player_tag = ?) WHERE discord_user_id = ?",
                (player_tag, discord_user_id),
            )

    async def unlink_player(self, player_tag: str) -> None:
        await self.execute("DELETE FROM player_links WHERE player_tag = ?", (player_tag,))

    async def get_links(self, discord_user_id: int) -> list[Row]:
        """A user's links, default first, joined with cached player info."""
        return await self.fetchall(
            "SELECT l.*, p.name, p.town_hall, p.trophies, p.clan_tag, p.clan_role "
            "FROM player_links l LEFT JOIN players p ON p.player_tag = l.player_tag "
            "WHERE l.discord_user_id = ? ORDER BY l.is_default DESC, l.linked_at",
            (discord_user_id,),
        )

    async def get_linked_tags(self, discord_user_id: int) -> list[str]:
        return [r["player_tag"] for r in await self.get_links(discord_user_id)]

    async def get_link(self, player_tag: str) -> Row | None:
        return await self.fetchone("SELECT * FROM player_links WHERE player_tag = ?", (player_tag,))

    async def links_for_tags(self, tags: Iterable[str]) -> dict[str, int]:
        tags = list(tags)
        if not tags:
            return {}
        q = ",".join("?" * len(tags))
        rows = await self.fetchall(
            f"SELECT player_tag, discord_user_id FROM player_links WHERE player_tag IN ({q})",
            tags,
        )
        return {r["player_tag"]: r["discord_user_id"] for r in rows}

    async def all_linked_tags(self) -> list[str]:
        return [r["player_tag"] for r in await self.fetchall("SELECT player_tag FROM player_links")]

    # ------------------------------------------------------------------ log routing

    async def set_clan_log(
        self,
        guild_id: int,
        clan_tag: str,
        log_type: str,
        channel_id: int | None,
        ping_role_id: int | None = None,
        color: int | None = None,
    ) -> None:
        if channel_id is None:
            await self.execute(
                "DELETE FROM clan_logs WHERE guild_id = ? AND clan_tag = ? AND log_type = ?",
                (guild_id, clan_tag, log_type),
            )
            return
        await self.execute(
            "INSERT INTO clan_logs (guild_id, clan_tag, log_type, channel_id, ping_role_id, color) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (guild_id, clan_tag, log_type) DO UPDATE SET "
            "channel_id = excluded.channel_id, ping_role_id = excluded.ping_role_id, "
            "color = excluded.color",
            (guild_id, clan_tag, log_type, channel_id, ping_role_id, color),
        )

    async def clan_log_targets(self, clan_tag: str, log_type: str) -> list[Row]:
        """Every (guild, channel, ping role, color) subscribed to a clan's log type."""
        return await self.fetchall(
            "SELECT * FROM clan_logs WHERE clan_tag = ? AND log_type = ?", (clan_tag, log_type)
        )

    async def get_clan_logs(self, guild_id: int) -> list[Row]:
        return await self.fetchall(
            "SELECT * FROM clan_logs WHERE guild_id = ? ORDER BY clan_tag, log_type", (guild_id,)
        )

    async def set_server_log(self, guild_id: int, log_type: str, channel_id: int | None) -> None:
        if channel_id is None:
            await self.execute(
                "DELETE FROM server_logs WHERE guild_id = ? AND log_type = ?", (guild_id, log_type)
            )
            return
        await self.execute(
            "INSERT INTO server_logs (guild_id, log_type, channel_id) VALUES (?, ?, ?) "
            "ON CONFLICT (guild_id, log_type) DO UPDATE SET channel_id = excluded.channel_id",
            (guild_id, log_type, channel_id),
        )

    async def server_log_channel(self, guild_id: int, log_type: str) -> int | None:
        return await self.fetchval(
            "SELECT channel_id FROM server_logs WHERE guild_id = ? AND log_type = ?",
            (guild_id, log_type),
        )

    async def server_log_targets(self, log_type: str) -> list[Row]:
        return await self.fetchall("SELECT * FROM server_logs WHERE log_type = ?", (log_type,))

    # ------------------------------------------------------------------ live messages

    async def upsert_live_message(
        self,
        guild_id: int,
        kind: str,
        scope: str,
        channel_id: int,
        message_id: int | None = None,
        options: dict | None = None,
    ) -> None:
        await self.execute(
            "INSERT INTO live_messages (guild_id, kind, scope, channel_id, message_id, options) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (guild_id, kind, scope) DO UPDATE SET "
            "channel_id = excluded.channel_id, message_id = excluded.message_id, "
            "options = COALESCE(excluded.options, live_messages.options)",
            (
                guild_id,
                kind,
                scope,
                channel_id,
                message_id,
                json.dumps(options) if options else None,
            ),
        )

    async def set_live_message_id(self, guild_id: int, kind: str, scope: str, message_id: int | None) -> None:
        await self.execute(
            "UPDATE live_messages SET message_id = ? WHERE guild_id = ? AND kind = ? AND scope = ?",
            (message_id, guild_id, kind, scope),
        )

    async def delete_live_message(self, guild_id: int, kind: str, scope: str) -> None:
        await self.execute(
            "DELETE FROM live_messages WHERE guild_id = ? AND kind = ? AND scope = ?",
            (guild_id, kind, scope),
        )

    async def live_messages(self, kind: str) -> list[Row]:
        return await self.fetchall("SELECT * FROM live_messages WHERE kind = ?", (kind,))

    # ------------------------------------------------------------------ collected data reads

    async def get_player_row(self, player_tag: str) -> Row | None:
        return await self.fetchone("SELECT * FROM players WHERE player_tag = ?", (player_tag,))

    async def get_clan_row(self, clan_tag: str) -> Row | None:
        return await self.fetchone("SELECT * FROM clans WHERE clan_tag = ?", (clan_tag,))

    async def clan_member_rows(self, clan_tag: str) -> list[Row]:
        return await self.fetchall(
            "SELECT * FROM players WHERE clan_tag = ? ORDER BY trophies DESC", (clan_tag,)
        )

    async def name_history(self, player_tag: str) -> list[Row]:
        return await self.fetchall(
            "SELECT * FROM player_names WHERE player_tag = ? ORDER BY first_seen", (player_tag,)
        )

    # ------------------------------------------------------------------ snapshot cache

    async def load_snapshots(self, prefix: str) -> dict[str, dict]:
        rows = await self.fetchall("SELECT key, data FROM snapshot_cache WHERE key LIKE ?", (prefix + "%",))
        return {r["key"]: json.loads(r["data"]) for r in rows}

    async def load_snapshot_times(self, prefix: str) -> dict[str, str]:
        rows = await self.fetchall(
            "SELECT key, updated_at FROM snapshot_cache WHERE key LIKE ?", (prefix + "%",)
        )
        return {r["key"]: r["updated_at"] for r in rows}

    async def save_snapshot(self, key: str, data: dict, now_iso: str) -> None:
        await self.execute(
            "INSERT INTO snapshot_cache (key, data, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT (key) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
            (key, json.dumps(data), now_iso),
        )
