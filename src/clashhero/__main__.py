"""Entry point: `python -m clashhero` or `clashhero`."""

from __future__ import annotations

import asyncio
import logging
import sys

import coc
import discord

from clashhero.bot import ClashHeroBot
from clashhero.config import Config, ConfigError


async def run(config: Config) -> None:
    # Build the bot inside the running event loop: coc.py binds its client to the loop that
    # exists when it's created, and features like fetching CWL wars or many players at once
    # fail with "attached to a different loop" if that isn't the loop the bot runs on.
    async with ClashHeroBot(config) as bot:
        await bot.start(config.discord_token)


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
