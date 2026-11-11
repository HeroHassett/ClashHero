"""Sending to configured log channels and DMs, and maintaining live (auto-edited) messages.

Posts to one channel (or DMs to one user) go out one at a time. When Discord rate-limits a post,
for example during the burst of alerts after a restart, the post waits and tries again instead of
being dropped.
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, TypeVar

import discord

if TYPE_CHECKING:
    from clashhero.bot import ClashHeroBot

log = logging.getLogger(__name__)

T = TypeVar("T")

# Seconds to wait before each further attempt when Discord rate-limits a post. discord.py already
# retries a 429 a few times on its own; these come after that (and for rate limits it doesn't
# retry, like opening DMs too fast). Discord's own Retry-After wins when it asks for longer.
RETRY_DELAYS = (2, 5, 15, 30, 60)
# Rate limits Discord reports with an error code rather than (or as well as) a 429: slowmode, the
# channel write limit, opening direct messages too fast.
RATE_LIMIT_CODES = frozenset({20016, 20028, 40003})

# One lock per destination, per event loop (tests run several loops).
_lanes: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[object, asyncio.Lock]] = (
    weakref.WeakKeyDictionary()
)


def _lane(key: object) -> asyncio.Lock:
    locks = _lanes.setdefault(asyncio.get_running_loop(), {})
    lock = locks.get(key)
    if lock is None:
        lock = locks[key] = asyncio.Lock()
    return lock


def _retry_after(e: discord.HTTPException) -> float | None:
    """Seconds Discord asked us to wait if ``e`` is a rate limit (0 when it didn't say), or None
    for any other error."""
    if e.status != 429 and e.code not in RATE_LIMIT_CODES:
        return None
    try:
        return max(0.0, float(e.response.headers.get("Retry-After") or 0))
    except (AttributeError, TypeError, ValueError):
        return 0.0


async def _one_at_a_time(key: object, where: str, send: Callable[[], Awaitable[T]]) -> T:
    """Run ``send()`` alone for ``key``, trying again while Discord rate-limits it. Any other
    error, or a rate limit that outlasts every retry, is raised."""
    async with _lane(key):
        for delay in RETRY_DELAYS:
            try:
                return await send()
            except discord.HTTPException as e:
                asked = _retry_after(e)
                if asked is None:
                    raise
                wait = max(delay, asked)
                log.info("Rate limited sending to %s; trying again in %.0fs", where, wait)
                await asyncio.sleep(wait)
        return await send()


async def _channel(bot: ClashHeroBot, channel_id: int) -> discord.abc.Messageable | None:
    ch = bot.get_channel(channel_id)
    if ch is None:
        try:
            ch = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden) as e:
            log.warning("Log channel %s can't be reached: %s", channel_id, e)
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

    async def post() -> discord.Message:
        if file is not None:
            file.reset()  # a failed attempt may have read it already
        return await ch.send(**kwargs)

    try:
        return await _one_at_a_time(("channel", channel_id), f"channel {channel_id}", post)
    except (discord.Forbidden, discord.HTTPException) as e:
        log.warning("Could not send to channel %s: %s", channel_id, e)
        return None


async def send_dm(
    bot: ClashHeroBot,
    user_id: int,
    *,
    embed: discord.Embed,
    view: discord.ui.View | None = None,
    what: str = "message",
) -> bool:
    """DM a user, retried like a channel post while Discord rate-limits it. False (and logged)
    when it can't be delivered: DMs closed, unknown user, or still rate-limited after retrying."""
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        await _one_at_a_time(("dm", user_id), f"user {user_id}", lambda: user.send(embed=embed, view=view))
    except (discord.Forbidden, discord.NotFound, discord.HTTPException) as e:
        log.info("Could not DM %s to %s: %s", what, user_id, e)
        return False
    return True


async def send_clan_log(
    bot: ClashHeroBot,
    clan_tag: str,
    log_type: str,
    embed: discord.Embed,
    *,
    content: str | None = None,
    view: discord.ui.View | None = None,
) -> list[tuple[Any, discord.Message, discord.Embed]]:
    """Send ``embed`` to every channel subscribed to ``log_type`` for ``clan_tag``.
    Applies each subscription's color override and ping role. Returns (subscription, message,
    embed as sent) for each post that went out, so a caller can edit them later."""
    posts = []
    for target in await bot.db.clan_log_targets(clan_tag, log_type):
        e = embed.copy()
        if target["color"] is not None:
            e.color = discord.Color(target["color"])
        ping = f"<@&{target['ping_role_id']}>" if target["ping_role_id"] else None
        text = " ".join(x for x in (ping, content) if x) or None
        msg = await send_to_channel(bot, target["channel_id"], content=text, embed=e, view=view)
        if msg is not None:
            posts.append((target, msg, e))
    return posts


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
