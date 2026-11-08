"""Autorole + nickname storage and the pure logic that decides roles and nicknames."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from clashhero.db.database import Database, Row
from clashhero.utils.ranked import is_legend_tier

KINDS = (
    "clan_role",
    "town_hall",
    "builder_hall",
    "league",
    "trophy_band",
    "builder_league",
    "family",
    "war",
)
CLAN_ROLE_KEYS = ("leader", "coLeader", "admin", "member", "everyone")
FAMILY_KEYS = ("family", "exclusive", "guest", "verified", "linked", "leaders")
NICK_ROLE_LABELS = {"leader": "Lead", "coLeader": "Co-Lead", "admin": "Eld", "member": "Mem"}
NICK_MAX = 32

_SETTING_COLUMNS = frozenset(
    {
        "auto_update",
        "removal_delay_hours",
        "allow_not_linked",
        "allow_non_family",
        "only_verified_clan_roles",
        "nick_enabled",
        "family_format",
        "non_family_format",
        "nick_preference",
    }
)


# ------------------------------------------------------------------ storage


async def set_mapping(
    db: Database, guild_id: int, kind: str, scope: str, key: str, role_id: int | None
) -> None:
    if kind not in KINDS:
        raise ValueError(kind)
    if role_id is None:
        await db.execute(
            "DELETE FROM autoroles WHERE guild_id = ? AND kind = ? AND scope = ? AND key = ?",
            (guild_id, kind, scope, key),
        )
        return
    await db.execute(
        "INSERT INTO autoroles (guild_id, kind, scope, key, role_id) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (guild_id, kind, scope, key) DO UPDATE SET role_id = excluded.role_id",
        (guild_id, kind, scope, key, role_id),
    )


async def get_mappings(db: Database, guild_id: int) -> list[Row]:
    return await db.fetchall(
        "SELECT * FROM autoroles WHERE guild_id = ? ORDER BY kind, scope, key", (guild_id,)
    )


async def delete_kind(db: Database, guild_id: int, kind: str, scope: str | None = None) -> int:
    sql = "DELETE FROM autoroles WHERE guild_id = ? AND kind = ?"
    params: list = [guild_id, kind]
    if scope is not None:
        sql += " AND scope = ?"
        params.append(scope)
    return await db.execute_rowcount(sql, params)


async def guilds_with_war_role(db: Database, clan_tag: str) -> list[Row]:
    return await db.fetchall("SELECT * FROM autoroles WHERE kind = 'war' AND scope = ?", (clan_tag,))


async def get_settings(db: Database, guild_id: int) -> Row:
    await db.execute("INSERT OR IGNORE INTO autorole_settings (guild_id) VALUES (?)", (guild_id,))
    row = await db.fetchone("SELECT * FROM autorole_settings WHERE guild_id = ?", (guild_id,))
    assert row is not None
    return row


async def update_settings(db: Database, guild_id: int, **cols) -> None:
    cols = {k: v for k, v in cols.items() if v is not None}
    bad = set(cols) - _SETTING_COLUMNS
    if bad:
        raise ValueError(f"Unknown settings: {bad}")
    if not cols:
        return
    await get_settings(db, guild_id)
    sets = ", ".join(f"{k} = ?" for k in cols)
    await db.execute(f"UPDATE autorole_settings SET {sets} WHERE guild_id = ?", (*cols.values(), guild_id))


async def schedule_removal(
    db: Database, guild_id: int, user_id: int, role_id: int, remove_after: str
) -> None:
    await db.execute(
        "INSERT OR IGNORE INTO autorole_pending_removals (guild_id, user_id, role_id, "
        "remove_after) VALUES (?, ?, ?, ?)",
        (guild_id, user_id, role_id, remove_after),
    )


async def cancel_removals(db: Database, guild_id: int, user_id: int, role_ids: Iterable[int]):
    for rid in role_ids:
        await db.execute(
            "DELETE FROM autorole_pending_removals WHERE guild_id = ? AND user_id = ? AND role_id = ?",
            (guild_id, user_id, rid),
        )


async def due_removals(db: Database, guild_id: int, user_id: int, now_iso: str) -> set[int]:
    rows = await db.fetchall(
        "SELECT role_id FROM autorole_pending_removals WHERE guild_id = ? AND user_id = ? "
        "AND remove_after <= ?",
        (guild_id, user_id, now_iso),
    )
    return {r["role_id"] for r in rows}


async def clear_removal(db: Database, guild_id: int, user_id: int, role_id: int) -> None:
    await cancel_removals(db, guild_id, user_id, [role_id])


# ------------------------------------------------------------------ pure logic


@dataclass(frozen=True)
class Account:
    tag: str
    name: str
    verified: bool
    is_default: bool
    clan_tag: str | None
    clan_role: str | None
    town_hall: int
    builder_hall: int
    trophies: int
    league: str | None
    builder_league: str | None

    @classmethod
    def from_row(cls, r: Row) -> Account:
        return cls(
            tag=r["player_tag"],
            name=r["name"] or r["player_tag"],
            verified=bool(r["verified"]),
            is_default=bool(r["is_default"]),
            clan_tag=r["clan_tag"],
            clan_role=r["clan_role"],
            town_hall=r["town_hall"] or 0,
            builder_hall=r["builder_hall"] or 0,
            trophies=r["trophies"] or 0,
            league=r["league"],
            builder_league=r["builder_league"],
        )


async def accounts_by_user(db: Database, user_ids: Iterable[int] | None = None) -> dict[int, list[Account]]:
    sql = (
        "SELECT l.player_tag, l.discord_user_id, l.verified, l.is_default, p.name, p.clan_tag, "
        "p.clan_role, p.town_hall, p.builder_hall, p.trophies, p.league, p.builder_league "
        "FROM player_links l LEFT JOIN players p ON p.player_tag = l.player_tag"
    )
    params: list = []
    if user_ids is not None:
        ids = list(user_ids)
        if not ids:
            return {}
        sql += f" WHERE l.discord_user_id IN ({','.join('?' * len(ids))})"
        params = ids
    out: dict[int, list[Account]] = {}
    for r in await db.fetchall(sql + " ORDER BY l.is_default DESC, l.linked_at", params):
        out.setdefault(r["discord_user_id"], []).append(Account.from_row(r))
    return out


@dataclass(frozen=True)
class Mapping:
    kind: str
    scope: str
    key: str
    role_id: int

    @classmethod
    def from_row(cls, r: Row) -> Mapping:
        return cls(r["kind"], r["scope"], r["key"], r["role_id"])


def parse_band(key: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"(\d+)-(\d+)", key)
    return (int(m.group(1)), int(m.group(2))) if m else None


def managed_roles(mappings: Sequence[Mapping]) -> set[int]:
    """Roles the periodic sync owns (war roles are handled by war events)."""
    return {m.role_id for m in mappings if m.kind != "war"}


def desired_roles(
    accounts: Sequence[Account],
    family_clans: set[str],
    mappings: Sequence[Mapping],
    *,
    only_verified_clan_roles: bool = False,
    allow_non_family: bool = False,
) -> set[int]:
    """Roles a Discord user should hold given their linked accounts."""
    by: dict[tuple[str, str, str], int] = {(m.kind, m.scope, m.key): m.role_id for m in mappings}
    out: set[int] = set()
    family = [a for a in accounts if a.clan_tag in family_clans]
    considered = accounts if allow_non_family else family

    for a in family:
        if only_verified_clan_roles and not a.verified:
            continue
        for key in (a.clan_role, "everyone"):
            if key and (rid := by.get(("clan_role", a.clan_tag, key))):
                out.add(rid)

    for a in considered:
        if rid := by.get(("town_hall", "", str(a.town_hall))):
            out.add(rid)
        if a.builder_hall and (rid := by.get(("builder_hall", "", str(a.builder_hall)))):
            out.add(rid)
        if a.league and (rid := by.get(("league", "", a.league.lower()))):
            out.add(rid)
        if a.builder_league and (rid := by.get(("builder_league", "", a.builder_league.lower()))):
            out.add(rid)
        if is_legend_tier(a.league):
            for m in mappings:
                if m.kind == "trophy_band" and (band := parse_band(m.key)):
                    if band[0] <= a.trophies <= band[1]:
                        out.add(m.role_id)

    fam_keys: set[str] = set()
    if accounts:
        fam_keys.add("linked")
        fam_keys.add("family" if family else "guest")
        if family and len(family) == len(accounts):
            fam_keys.add("exclusive")
        if any(a.verified for a in accounts):
            fam_keys.add("verified")
        if any(a.clan_role in ("leader", "coLeader") for a in family):
            fam_keys.add("leaders")
    for key in fam_keys:
        if rid := by.get(("family", "", key)):
            out.add(rid)
    return out


def role_diff(current: set[int], desired: set[int], managed: set[int]) -> tuple[set[int], set[int]]:
    """(to_add, to_remove). Only roles we manage are ever removed."""
    return desired - current, (current & managed) - desired


def pick_account(
    accounts: Sequence[Account], family_clans: set[str], preference: str = "default"
) -> tuple[Account | None, bool]:
    """Account used for the nickname, and whether it's a family account."""
    if not accounts:
        return None, False
    family = [a for a in accounts if a.clan_tag in family_clans]
    pool = family or list(accounts)

    def best(xs: Sequence[Account]) -> Account:
        return max(xs, key=lambda a: (a.town_hall, a.trophies))

    if preference == "best":
        chosen = best(pool)
    else:
        default = next((a for a in pool if a.is_default), None)
        chosen = default or pool[0]
    return chosen, bool(family)


_VARS = {
    "NAME": "name",
    "PLAYER_NAME": "name",
    "TH": "th",
    "TOWN_HALL": "th",
    "ROLE": "role",
    "CLAN_ROLE": "role",
    "ALIAS": "alias",
    "CLAN": "clan",
    "CLAN_NAME": "clan",
    "DISCORD": "discord",
    "DISCORD_NAME": "discord",
}
CLAN_VARS = {"ROLE", "CLAN_ROLE", "ALIAS", "CLAN", "CLAN_NAME"}


def validate_format(fmt: str, *, family: bool) -> str | None:
    """Error message for an invalid nickname format, or None."""
    used = set(re.findall(r"\{([A-Z_]+)\}", fmt))
    unknown = used - set(_VARS)
    if unknown:
        return f"Unknown variable(s): {', '.join('{' + u + '}' for u in sorted(unknown))}"
    if not used & {"NAME", "PLAYER_NAME", "DISCORD", "DISCORD_NAME"}:
        return "The format must include {NAME} or {DISCORD}."
    if not family and used & CLAN_VARS:
        return "Non-family formats can't use clan variables ({ROLE}, {ALIAS}, {CLAN})."
    return None


def format_nickname(
    fmt: str,
    *,
    name: str = "",
    th: int | str = "",
    role: str | None = None,
    alias: str | None = None,
    clan: str | None = None,
    discord: str = "",
) -> str:
    values = {
        "name": name,
        "th": str(th),
        "role": NICK_ROLE_LABELS.get(role or "", role or ""),
        "alias": alias or "",
        "clan": clan or "",
        "discord": discord,
    }
    out = re.sub(r"\{([A-Z_]+)\}", lambda m: values.get(_VARS.get(m.group(1), ""), m.group(0)), fmt)
    out = re.sub(r"\s+", " ", out).strip(" |-")
    return out[:NICK_MAX].strip()
