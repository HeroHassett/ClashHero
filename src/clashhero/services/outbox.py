"""Sending to configured log channels and maintaining live (auto-edited) messages."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from clashhero.bot import ClashHeroBot

log = logging.getLogger(__name__)


async def _channel(bot: ClashHeroBot, channel_id: int) -> discord.abc.Messageable | None:
    ch = bot.get_channel(channel_id)
    if ch is None:
        try:
            ch = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden):
            return None
    return ch  # type: ignore[return-value]


async def send_to_channel(
    bot: ClashHeroBot,
    channel_id: int,
    *,
    content: str | None = None,
    embed: discord.Embed | None = None,
    embeds: list[discord.Embed] | None = None,
    view: discord.ui.View | None = None,
    file: discord.File | None = None,
) -> discord.Message | None:
    ch = await _channel(bot, channel_id)
    if ch is None:
        return None
    kwargs = {
        "content": content,
        "allowed_mentions": discord.AllowedMentions(everyone=False, roles=True, users=True),
    }
    if embeds:
        kwargs["embeds"] = embeds
    elif embed:
        kwargs["embed"] = embed
    if view is not None:
        kwargs["view"] = view
    if file is not None:
        kwargs["file"] = file
    try:
        return await ch.send(**kwargs)
    except (discord.Forbidden, discord.HTTPException) as e:
        log.warning("Could not send to channel %s: %s", channel_id, e)
        return None


async def send_clan_log(
    bot: ClashHeroBot,
    clan_tag: str,
    log_type: str,
    embed: discord.Embed,
    *,
    content: str | None = None,
    view: discord.ui.View | None = None,
) -> None:
    """Send ``embed`` to every channel subscribed to ``log_type`` for ``clan_tag``.
    Applies each subscription's color override and ping role."""
    for target in await bot.db.clan_log_targets(clan_tag, log_type):
        e = embed.copy()
        if target["color"] is not None:
            e.color = discord.Color(target["color"])
        ping = f"<@&{target['ping_role_id']}>" if target["ping_role_id"] else None
        text = " ".join(x for x in (ping, content) if x) or None
        await send_to_channel(bot, target["channel_id"], content=text, embed=e, view=view)


async def send_server_log(bot: ClashHeroBot, guild_id: int, log_type: str, embed: discord.Embed):
    channel_id = await bot.db.server_log_channel(guild_id, log_type)
    if channel_id:
        await send_to_channel(bot, channel_id, embed=embed)


async def update_live_message(
    bot: ClashHeroBot,
    row,
    *,
    embeds: list[discord.Embed],
    view: discord.ui.View | None = None,
) -> None:
    """Edit the live message described by a ``live_messages`` row, reposting it if it was
    deleted. Removes the row if the channel is gone."""
    ch = await _channel(bot, row["channel_id"])
    if ch is None:
        await bot.db.delete_live_message(row["guild_id"], row["kind"], row["scope"])
        return
    if row["message_id"]:
        try:
            msg = ch.get_partial_message(row["message_id"])  # type: ignore[attr-defined]
            await msg.edit(embeds=embeds, view=view)
            return
        except discord.NotFound:
            pass
        except (discord.Forbidden, discord.HTTPException) as e:
            log.warning("Live message edit failed: %s", e)
            return
    try:
        msg = await ch.send(embeds=embeds, view=view)
    except (discord.Forbidden, discord.HTTPException) as e:
        log.warning("Live message send failed: %s", e)
        return
    await bot.db.set_live_message_id(row["guild_id"], row["kind"], row["scope"], msg.id)
