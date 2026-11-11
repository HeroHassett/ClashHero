"""Entry point: `python -m clashhero` or `clashhero`."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys

import coc
import discord

from clashhero.bot import ClashHeroBot
from clashhero.config import Config, ConfigError


async def run(config: Config) -> None:
    # Build the bot inside the running event loop: coc.py binds its client to the loop that
    # exists when it's created, and features like fetching CWL wars or many players at once
    # fail with "attached to a different loop" if that isn't the loop the bot runs on.
    stopping = _stop_on_sigterm()
    try:
        async with ClashHeroBot(config) as bot:
            await bot.start(config.discord_token)
    except asyncio.CancelledError:
        if not stopping.is_set():
            raise
    finally:
        with contextlib.suppress(NotImplementedError, RuntimeError):
            asyncio.get_running_loop().remove_signal_handler(signal.SIGTERM)


def _stop_on_sigterm() -> asyncio.Event:
    """Close the bot on SIGTERM, as Ctrl+C does: cancel this task so ``async with`` closes it.
    Docker, systemd and ``kill`` stop with SIGTERM, and a container's first process ignores it
    unless it handles it (Docker then kills the bot mid-work after 10 seconds)."""
    stopping = asyncio.Event()
    task = asyncio.current_task()

    def stop() -> None:
        if task is not None and not stopping.is_set():
            stopping.set()
            task.cancel()

    with contextlib.suppress(NotImplementedError, RuntimeError):  # no signal handlers on Windows
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop)
    return stopping


def main() -> None:
    try:
        config = Config.from_env()
        logging.basicConfig(
            level=config.log_level,
            format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        )
        asyncio.run(run(config))
    except ConfigError as e:
        sys.exit(f"Configuration error: {e}")
    except coc.InvalidCredentials:
        sys.exit(
            "Configuration error: developer.clashofclans.com rejected COC_EMAIL/COC_PASSWORD. "
            "These are your developer-portal login, not your game account."
        )
    except discord.LoginFailure:
        sys.exit("Configuration error: Discord rejected DISCORD_TOKEN. Reset it on the Bot page.")
    except discord.PrivilegedIntentsRequired:
        sys.exit(
            "Configuration error: enable 'Server Members Intent' on the Bot page of the "
            "Discord developer portal, then restart."
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
