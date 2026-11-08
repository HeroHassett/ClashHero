"""Turn user input into clan/player tags, plus autocomplete helpers.

Clan input accepts a tag, an alias, or nothing (falls back to the channel's default clan, then
to the server's only clan). Player input accepts a tag, nothing (the caller's default linked
account), or a mention-style user via the separate ``user`` option on commands.
"""

from __future__ import annotations

import discord
from discord import app_commands

from clashhero.utils.tags import is_valid_tag, normalize_tag


class ResolveError(app_commands.AppCommandError):
    pass


def parse_tag(raw: str) -> str:
    tag = normalize_tag(raw)
    if not is_valid_tag(tag):
        raise ResolveError(f"`{raw}` doesn't look like a valid tag.")
    return tag


async def resolve_clan(
    interaction: discord.Interaction, raw: str | None, *, must_be_tracked: bool = True
) -> str:
    db = interaction.client.db
    gid = interaction.guild_id
    if raw:
        raw = raw.strip()
        if gid is not None:
            aliased = await db.clan_by_alias(gid, raw)
            if aliased:
                return aliased
        tag = parse_tag(raw)
        if must_be_tracked and gid is not None and tag not in await db.get_guild_clans(gid):
            raise ResolveError(f"`{tag}` isn't linked to this server. Use `/setup clan`.")
        return tag
    if interaction.channel_id is not None:
        tag = await db.get_channel_clan(interaction.channel_id)
        if tag:
            return tag
    if gid is not None:
        clans = await db.get_guild_clans(gid)
        if len(clans) == 1:
            return clans[0]
    raise ResolveError("Pick a clan (tag or alias). Tip: `/setup clan` can set a channel default.")


async def resolve_clans(interaction: discord.Interaction, raw: str | None) -> list[str]:
    """Space/comma separated tags or aliases; empty means every clan in the server."""
    if not raw:
        clans = await interaction.client.db.get_guild_clans(interaction.guild_id)
        if not clans:
            raise ResolveError("No clans are linked to this server yet. Use `/setup clan`.")
        return clans
    parts = [p for p in raw.replace(",", " ").split() if p]
    return [await resolve_clan(interaction, p) for p in parts]


async def resolve_player(
    interaction: discord.Interaction,
    raw: str | None,
    user: discord.abc.User | None = None,
) -> str:
    db = interaction.client.db
    if raw:
        return parse_tag(raw)
    target = user or interaction.user
    tags = await db.get_linked_tags(target.id)
    if not tags:
        who = "You have" if target.id == interaction.user.id else f"{target.mention} has"
        raise ResolveError(f"{who} no linked accounts. Give a player tag or use `/link create`.")
    return tags[0]


async def resolve_user_tags(interaction: discord.Interaction, user: discord.abc.User) -> list[str]:
    return await interaction.client.db.get_linked_tags(user.id)


# ------------------------------------------------------------------ autocomplete


async def clan_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    rows = await interaction.client.db.get_guild_clan_rows(interaction.guild_id)
    cur = current.lower()
    out = []
    for r in rows:
        label = f"{r['name'] or '?'} ({r['clan_tag']})" + (f" [{r['alias']}]" if r["alias"] else "")
        if cur in label.lower():
            out.append(app_commands.Choice(name=label[:100], value=r["alias"] or r["clan_tag"]))
    return out[:25]


async def player_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Caller's linked accounts first, then members of the server's clans."""
    db = interaction.client.db
    cur = current.lower()
    rows = await db.get_links(interaction.user.id)
    seen, out = set(), []
    for r in rows:
        label = f"{r['name'] or '?'} ({r['player_tag']})"
        if cur in label.lower():
            out.append(app_commands.Choice(name=label[:100], value=r["player_tag"]))
            seen.add(r["player_tag"])
    if interaction.guild_id is not None and len(out) < 25:
        members = await db.fetchall(
            "SELECT player_tag, name, town_hall FROM players WHERE clan_tag IN "
            "(SELECT clan_tag FROM guild_clans WHERE guild_id = ?) AND "
            "(name LIKE ? OR player_tag LIKE ?) ORDER BY name LIMIT 50",
            (interaction.guild_id, f"%{current}%", f"%{current.upper()}%"),
        )
        for r in members:
            if r["player_tag"] in seen:
                continue
            label = f"{r['name']} (TH{r['town_hall']}, {r['player_tag']})"
            out.append(app_commands.Choice(name=label[:100], value=r["player_tag"]))
    return out[:25]
