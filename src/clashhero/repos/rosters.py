"""Roster queries and pure roster logic (eligibility, tag parsing, ping targets, sorting,
auto-ping windows)."""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from clashhero.db.database import Database, Row
from clashhero.utils.tags import is_valid_tag, normalize_tag

CATEGORIES = ("cwl", "war", "esports", "trophy")
DEFAULT_GROUPS = ("Confirmed", "Substitute")
SORT_OPTIONS = {
    "th_heroes": "Town Hall + heroes",
    "th": "Town Hall",
    "heroes": "Heroes",
    "trophies": "Trophies",
    "name": "Player name",
    "signup_time": "Signup time",
}
PING_OPTIONS = ("unregistered", "missing", "everyone")

_EDITABLE = frozenset(
    {
        "clan_tag",
        "name",
        "category",
        "max_members",
        "min_th",
        "max_th",
        "min_heroes",
        "max_accounts_per_user",
        "allow_unlinked",
        "allow_multi_signup",
        "allow_group_selection",
        "role_id",
        "sort_by",
        "start_time",
        "end_time",
        "color",
        "closed",
        "channel_id",
        "message_id",
        "autoping_start_hours",
        "autoping_end_hours",
        "autoping_message",
        "autoping_channel_id",
        "autoping_start_sent",
        "autoping_end_sent",
    }
)
_CLONE_COLUMNS = sorted(
    _EDITABLE - {"name", "channel_id", "message_id", "closed", "autoping_start_sent", "autoping_end_sent"}
)
AUTOPING_KINDS = ("start", "end")


# ------------------------------------------------------------------ pure helpers

_TOKEN = re.compile(r"[#A-Za-z0-9]+")


def parse_tags(text: str) -> list[str]:
    """Extract valid player tags from free text ("#ABC, def | #GHI", newline lists, etc.).
    Keeps first-seen order and drops duplicates/invalid tokens."""
    out: list[str] = []
    for token in _TOKEN.findall(text or ""):
        tag = normalize_tag(token)
        if is_valid_tag(tag) and len(tag) >= 4 and tag not in out:
            out.append(tag)
    return out


def check_eligibility(roster: Mapping[str, Any], town_hall: int | None, heroes: int | None) -> str | None:
    """Reason the account can't join, or None if it can."""
    th = town_hall or 0
    if roster["min_th"] and th < roster["min_th"]:
        return f"needs TH{roster['min_th']}+ (is TH{th})"
    if roster["max_th"] and th > roster["max_th"]:
        return f"max TH{roster['max_th']} (is TH{th})"
    if roster["min_heroes"] and (heroes or 0) < roster["min_heroes"]:
        return f"needs {roster['min_heroes']}+ combined hero levels (has {heroes or 0})"
    return None


def compute_ping_targets(
    option: str,
    members: Sequence[Mapping[str, Any]],
    clan_member_tags: Iterable[str],
    links: Mapping[str, int],
    group_id: int | None = None,
) -> set[int]:
    """Discord user ids to ping.

    - ``group_id`` given: everyone signed up in that group (overrides ``option``)
    - unregistered: linked members of the clan who aren't signed up
    - missing: signed-up accounts that aren't currently in the clan
    - everyone: everyone signed up
    """
    clan_tags = set(clan_member_tags)
    signed = {m["player_tag"] for m in members}

    def uid(m: Mapping[str, Any]) -> int | None:
        return m["user_id"] or links.get(m["player_tag"])

    if group_id is not None:
        return {u for m in members if m["group_id"] == group_id and (u := uid(m))}
    if option == "unregistered":
        return {links[t] for t in clan_tags - signed if t in links}
    if option == "missing":
        return {u for m in members if m["player_tag"] not in clan_tags and (u := uid(m))}
    if option == "everyone":
        return {u for m in members if (u := uid(m))}
    raise ValueError(f"Unknown ping option {option!r}")


def _get(m: Mapping[str, Any], key: str) -> Any:
    return m[key] if key in m.keys() else None


def sort_members(members: Sequence[Mapping[str, Any]], sort_by: str) -> list[Mapping[str, Any]]:
    def th(m):
        return m["town_hall"] or 0

    def heroes(m):
        return m["heroes_total"] or 0

    keys = {
        "th_heroes": lambda m: (-th(m), -heroes(m), (m["name"] or "").lower()),
        "th": lambda m: (-th(m), (m["name"] or "").lower()),
        "heroes": lambda m: (-heroes(m), (m["name"] or "").lower()),
        "trophies": lambda m: (-(_get(m, "trophies") or 0), (m["name"] or "").lower()),
        "name": lambda m: ((m["name"] or "").lower(),),
        "signup_time": lambda m: (m["signed_up_at"],),
    }
    return sorted(members, key=keys.get(sort_by, keys["th_heroes"]))


def members_csv(members: Sequence[Mapping[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        ["player_tag", "name", "town_hall", "heroes_total", "group", "discord_user_id", "signed_up_at"]
    )
    for m in members:
        w.writerow(
            [
                m["player_tag"],
                m["name"] or "",
                m["town_hall"] or "",
                m["heroes_total"] or "",
                m["group_name"] or "",
                m["user_id"] or "",
                m["signed_up_at"],
            ]
        )
    return buf.getvalue()


def _as_dt(value: str | datetime | None) -> datetime | None:
    if not value:
        return None
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def format_hours(hours: float) -> str:
    """1.5 -> '1h30m', 0.25 -> '15m', 24 -> '24h'."""
    minutes = round(hours * 60)
    h, m = divmod(minutes, 60)
    return (f"{h}h" if h else "") + (f"{m}m" if m else "") or "0m"


def autoping_due(roster: Mapping[str, Any], now: datetime) -> list[tuple[str, str]]:
    """(kind, time) pairs whose auto-ping window is open: ``now`` is within the configured hours
    before the roster's start/end time and that exact time hasn't been pinged for yet. Closed
    rosters never ping."""
    if _get(roster, "closed"):
        return []
    due = []
    for kind in AUTOPING_KINDS:
        hours, when = _get(roster, f"autoping_{kind}_hours"), _get(roster, f"{kind}_time")
        at = _as_dt(when)
        if not hours or at is None or _get(roster, f"autoping_{kind}_sent") == when:
            continue
        if at - timedelta(hours=hours) <= now < at:
            due.append((kind, when))
    return due


def describe_autoping(roster: Mapping[str, Any]) -> str | None:
    """Human summary of the auto-ping setting, or None when it's off."""
    parts = []
    if _get(roster, "autoping_end_hours"):
        parts.append(f"{format_hours(roster['autoping_end_hours'])} before signups close")
    if _get(roster, "autoping_start_hours"):
        parts.append(f"{format_hours(roster['autoping_start_hours'])} before start")
    if not parts:
        return None
    return "Auto-ping unregistered members " + " and ".join(parts)


# ------------------------------------------------------------------ rosters


async def create_roster(db: Database, guild_id: int, clan_tag: str, name: str, **opts: Any) -> int:
    bad = set(opts) - _EDITABLE
    if bad:
        raise ValueError(f"Unknown roster fields: {sorted(bad)}")
    cols = {"guild_id": guild_id, "clan_tag": clan_tag, "name": name, **opts}
    async with db.batch():
        rid = await db.execute(
            f"INSERT INTO rosters ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            cols.values(),
        )
        for i, g in enumerate(DEFAULT_GROUPS):
            await db.execute(
                "INSERT INTO roster_groups (roster_id, name, position) VALUES (?, ?, ?)", (rid, g, i)
            )
    return rid


async def get_roster(db: Database, roster_id: int) -> Row | None:
    return await db.fetchone("SELECT * FROM rosters WHERE id = ?", (roster_id,))


async def get_roster_by_name(db: Database, guild_id: int, name: str) -> Row | None:
    return await db.fetchone(
        "SELECT * FROM rosters WHERE guild_id = ? AND name = ? COLLATE NOCASE", (guild_id, name)
    )


async def list_rosters(
    db: Database, guild_id: int, clan_tag: str | None = None, category: str | None = None
) -> list[Row]:
    sql = (
        "SELECT r.*, (SELECT COUNT(*) FROM roster_members m WHERE m.roster_id = r.id) AS member_count, "
        "c.name AS clan_name FROM rosters r LEFT JOIN clans c ON c.clan_tag = r.clan_tag "
        "WHERE r.guild_id = ?"
    )
    params: list[Any] = [guild_id]
    if clan_tag:
        sql += " AND r.clan_tag = ?"
        params.append(clan_tag)
    if category:
        sql += " AND r.category = ?"
        params.append(category)
    return await db.fetchall(sql + " ORDER BY r.created_at", params)


async def update_roster(db: Database, roster_id: int, **fields: Any) -> None:
    bad = set(fields) - _EDITABLE
    if bad:
        raise ValueError(f"Unknown roster fields: {sorted(bad)}")
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    await db.execute(f"UPDATE rosters SET {sets} WHERE id = ?", (*fields.values(), roster_id))


async def clone_roster(db: Database, roster_id: int, new_name: str) -> int:
    """Copy settings and groups (not members) into a new roster."""
    src = await get_roster(db, roster_id)
    if src is None:
        raise ValueError("Roster not found")
    cols = ", ".join(_CLONE_COLUMNS)
    async with db.batch():
        new_id = await db.execute(
            f"INSERT INTO rosters (guild_id, name, {cols}) "
            f"SELECT guild_id, ?, {cols} FROM rosters WHERE id = ?",
            (new_name, roster_id),
        )
        await db.execute(
            "INSERT INTO roster_groups (roster_id, name, position) "
            "SELECT ?, name, position FROM roster_groups WHERE roster_id = ?",
            (new_id, roster_id),
        )
    return new_id


async def delete_roster(db: Database, roster_id: int) -> None:
    await db.execute("DELETE FROM rosters WHERE id = ?", (roster_id,))


# ------------------------------------------------------------------ groups


async def list_groups(db: Database, roster_id: int) -> list[Row]:
    return await db.fetchall(
        "SELECT * FROM roster_groups WHERE roster_id = ? ORDER BY position, id", (roster_id,)
    )


async def get_group_by_name(db: Database, roster_id: int, name: str) -> Row | None:
    return await db.fetchone(
        "SELECT * FROM roster_groups WHERE roster_id = ? AND name = ? COLLATE NOCASE", (roster_id, name)
    )


async def default_group_id(db: Database, roster_id: int) -> int | None:
    return await db.fetchval(
        "SELECT id FROM roster_groups WHERE roster_id = ? ORDER BY position, id LIMIT 1", (roster_id,)
    )


async def create_group(db: Database, roster_id: int, name: str) -> int:
    pos = await db.fetchval(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM roster_groups WHERE roster_id = ?", (roster_id,), 0
    )
    return await db.execute(
        "INSERT INTO roster_groups (roster_id, name, position) VALUES (?, ?, ?)", (roster_id, name, pos)
    )


async def rename_group(db: Database, group_id: int, name: str) -> None:
    await db.execute("UPDATE roster_groups SET name = ? WHERE id = ?", (name, group_id))


async def delete_group(db: Database, group_id: int) -> None:
    """Members of a deleted group fall back to no group (shown as ungrouped)."""
    await db.execute("DELETE FROM roster_groups WHERE id = ?", (group_id,))


# ------------------------------------------------------------------ members


async def add_member(
    db: Database,
    roster_id: int,
    player_tag: str,
    *,
    user_id: int | None,
    group_id: int | None,
    name: str | None,
    town_hall: int | None,
    heroes_total: int | None,
) -> bool:
    """Insert or update a signup. Returns True if newly added."""
    existed = await db.fetchval(
        "SELECT 1 FROM roster_members WHERE roster_id = ? AND player_tag = ?", (roster_id, player_tag)
    )
    await db.execute(
        "INSERT INTO roster_members (roster_id, player_tag, user_id, group_id, name, town_hall, "
        "heroes_total) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (roster_id, player_tag) DO UPDATE SET "
        "user_id = COALESCE(excluded.user_id, roster_members.user_id), group_id = excluded.group_id, "
        "name = COALESCE(excluded.name, roster_members.name), "
        "town_hall = COALESCE(excluded.town_hall, roster_members.town_hall), "
        "heroes_total = COALESCE(excluded.heroes_total, roster_members.heroes_total)",
        (roster_id, player_tag, user_id, group_id, name, town_hall, heroes_total),
    )
    return not existed


async def remove_member(db: Database, roster_id: int, player_tag: str) -> bool:
    return (
        await db.execute_rowcount(
            "DELETE FROM roster_members WHERE roster_id = ? AND player_tag = ?", (roster_id, player_tag)
        )
        > 0
    )


async def get_member(db: Database, roster_id: int, player_tag: str) -> Row | None:
    return await db.fetchone(
        "SELECT * FROM roster_members WHERE roster_id = ? AND player_tag = ?", (roster_id, player_tag)
    )


async def move_member(
    db: Database, roster_id: int, player_tag: str, *, to_roster_id: int, group_id: int | None
) -> bool:
    """Move a signup to another roster and/or group. Returns False if it isn't on ``roster_id``."""
    row = await get_member(db, roster_id, player_tag)
    if row is None:
        return False
    async with db.batch():
        if to_roster_id != roster_id:
            await remove_member(db, roster_id, player_tag)
        await add_member(
            db,
            to_roster_id,
            player_tag,
            user_id=row["user_id"],
            group_id=group_id,
            name=row["name"],
            town_hall=row["town_hall"],
            heroes_total=row["heroes_total"],
        )
    return True


async def list_members(db: Database, roster_id: int) -> list[Row]:
    """Signups joined with live player data (falls back to signup snapshot) and group names."""
    return await db.fetchall(
        "SELECT m.roster_id, m.player_tag, m.group_id, m.signed_up_at, "
        "COALESCE(m.user_id, l.discord_user_id) AS user_id, "
        "COALESCE(p.name, m.name) AS name, COALESCE(p.town_hall, m.town_hall) AS town_hall, "
        "COALESCE(p.heroes_total, m.heroes_total) AS heroes_total, p.trophies, p.clan_tag, "
        "g.name AS group_name, g.position AS group_position "
        "FROM roster_members m LEFT JOIN players p ON p.player_tag = m.player_tag "
        "LEFT JOIN player_links l ON l.player_tag = m.player_tag "
        "LEFT JOIN roster_groups g ON g.id = m.group_id WHERE m.roster_id = ?",
        (roster_id,),
    )


async def clear_members(db: Database, roster_id: int) -> int:
    return await db.execute_rowcount("DELETE FROM roster_members WHERE roster_id = ?", (roster_id,))


async def count_user_accounts(db: Database, roster_id: int, user_id: int) -> int:
    return await db.fetchval(
        "SELECT COUNT(*) FROM roster_members WHERE roster_id = ? AND user_id = ?", (roster_id, user_id), 0
    )


async def user_has_accounts(db: Database, roster_id: int, user_id: int) -> bool:
    return await count_user_accounts(db, roster_id, user_id) > 0


async def other_roster_in_category(
    db: Database, guild_id: int, category: str, player_tag: str, exclude_roster_id: int
) -> Row | None:
    """Another roster of the same category this player is already on (for the multi-signup rule)."""
    return await db.fetchone(
        "SELECT r.* FROM roster_members m JOIN rosters r ON r.id = m.roster_id "
        "WHERE r.guild_id = ? AND r.category = ? AND m.player_tag = ? AND r.id != ? LIMIT 1",
        (guild_id, category, player_tag, exclude_roster_id),
    )


async def member_count(db: Database, roster_id: int) -> int:
    return await db.fetchval("SELECT COUNT(*) FROM roster_members WHERE roster_id = ?", (roster_id,), 0)


# ------------------------------------------------------------------ suggestions


async def suggestion_candidate_rows(db: Database, roster: Mapping[str, Any]) -> list[Row]:
    """Everyone a roster suggestion considers: the roster clan's current members plus anyone
    already signed up, with live player data (falling back to the signup snapshot), last_seen,
    the Discord link and the current group."""
    return await db.fetchall(
        "SELECT t.player_tag, COALESCE(p.name, m.name, t.player_tag) AS name, "
        "COALESCE(p.town_hall, m.town_hall) AS town_hall, "
        "COALESCE(p.heroes_total, m.heroes_total) AS heroes_total, p.last_seen, p.clan_tag, "
        "m.group_id, g.name AS group_name, m.player_tag IS NOT NULL AS signed_up, "
        "COALESCE(m.user_id, l.discord_user_id) AS user_id "
        "FROM (SELECT player_tag FROM players WHERE clan_tag = ? "
        "      UNION SELECT player_tag FROM roster_members WHERE roster_id = ?) t "
        "LEFT JOIN players p ON p.player_tag = t.player_tag "
        "LEFT JOIN roster_members m ON m.roster_id = ? AND m.player_tag = t.player_tag "
        "LEFT JOIN roster_groups g ON g.id = m.group_id "
        "LEFT JOIN player_links l ON l.player_tag = t.player_tag",
        (roster["clan_tag"], roster["id"], roster["id"]),
    )


async def tags_on_other_rosters(
    db: Database, guild_id: int, category: str, exclude_roster_id: int
) -> dict[str, str]:
    """player_tag -> roster name for signups on other rosters of the same category."""
    rows = await db.fetchall(
        "SELECT m.player_tag, r.name FROM roster_members m JOIN rosters r ON r.id = m.roster_id "
        "WHERE r.guild_id = ? AND r.category = ? AND r.id != ? ORDER BY r.created_at",
        (guild_id, category, exclude_roster_id),
    )
    out: dict[str, str] = {}
    for r in rows:
        out.setdefault(r["player_tag"], r["name"])
    return out


async def suggestion_groups(db: Database, roster_id: int) -> tuple[int, int]:
    """(confirmed, substitute) group ids: the groups with those names, else the first and second
    groups by position; creates whichever is missing."""
    groups = await list_groups(db, roster_id)
    by_name = {g["name"].lower(): g["id"] for g in groups}
    confirmed = by_name.get("confirmed") or (groups[0]["id"] if groups else None)
    if confirmed is None:
        confirmed = await create_group(db, roster_id, DEFAULT_GROUPS[0])
    sub = by_name.get("substitute")
    if sub is None:
        rest = [g["id"] for g in groups if g["id"] != confirmed]
        sub = rest[0] if rest else await create_group(db, roster_id, DEFAULT_GROUPS[1])
    return confirmed, sub


# ------------------------------------------------------------------ auto-ping


async def set_autoping(
    db: Database,
    roster_id: int,
    *,
    start_hours: float | None,
    end_hours: float | None,
    message: str | None,
    channel_id: int | None,
) -> None:
    """Configure (or with all None, turn off) the auto-ping. Sent markers are kept, so turning it
    off and on again inside a window that already pinged doesn't ping twice."""
    await update_roster(
        db,
        roster_id,
        autoping_start_hours=start_hours,
        autoping_end_hours=end_hours,
        autoping_message=message,
        autoping_channel_id=channel_id,
    )


async def autoping_rosters(db: Database) -> list[Row]:
    """Open rosters with an auto-ping configured for a time they actually have."""
    return await db.fetchall(
        "SELECT * FROM rosters WHERE closed = 0 AND ("
        "(autoping_start_hours IS NOT NULL AND start_time IS NOT NULL) OR "
        "(autoping_end_hours IS NOT NULL AND end_time IS NOT NULL))"
    )


async def mark_autoping_sent(db: Database, roster_id: int, kind: str, when: str) -> None:
    if kind not in AUTOPING_KINDS:
        raise ValueError(f"Unknown auto-ping kind {kind!r}")
    await update_roster(db, roster_id, **{f"autoping_{kind}_sent": when})
