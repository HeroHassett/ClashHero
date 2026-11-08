"""Entry point: `python -m clashhero` or `clashhero`."""

from __future__ import annotations

import asyncio
import logging
import sys

import coc
import discord

from clashhero.bot import ClashHeroBot
from clashhero.config import Config, ConfigError


def main() -> None:
    try:
        config = Config.from_env()
        logging.basicConfig(
            level=config.log_level,
            format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        )
        bot = ClashHeroBot(config)
        asyncio.run(bot.start(config.discord_token))
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
