"""Bot class: owns the Discord connection, the Clash API client, the database and the
background collector. Feature cogs in ``clashhero/cogs`` are discovered automatically."""

from __future__ import annotations

import asyncio
import logging
import pkgutil
from datetime import UTC, datetime

import coc
import discord
from discord.ext import commands

from clashhero import cogs
from clashhero.cogs._base import send_error
from clashhero.config import Config, ConfigError
from clashhero.db import Database
from clashhero.services import emojis
from clashhero.services.collector import Collector
from clashhero.utils import ranked

log = logging.getLogger(__name__)


def discover_extensions() -> list[str]:
    """Every module in clashhero.cogs not starting with an underscore."""
    return sorted(
        f"{cogs.__name__}.{m.name}" for m in pkgutil.iter_modules(cogs.__path__) if not m.name.startswith("_")
    )


class ClashHeroBot(commands.Bot):
    def __init__(self, config: Config) -> None:
        intents = discord.Intents.default()
        intents.members = True  # autoroles / nicknames need member lists (privileged intent)
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            # Never ping @everyone/@here, even if a user-written message (e.g. a reminder) contains it.
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=True, users=True),
        )
        self.config = config
        self.db = Database(config.database_path)
        # raw_attribute keeps the raw payload so we can read fields coc.py doesn't model yet
        # (e.g. leagueTier, see utils/ranked.py).
        self.coc = coc.Client(key_names=config.coc_key_name, key_count=1, raw_attribute=True)
        self.collector = Collector(self)
        self.tree.on_error = send_error

    async def setup_hook(self) -> None:
        await self.db.connect()
        await self.connect_clash_api()

        self._emoji_task = asyncio.create_task(self._sync_emojis())

        for ext in discover_extensions():
            await self.load_extension(ext)
            log.info("Loaded %s", ext)

        if self.config.dev_guild_id:
            guild = discord.Object(id=self.config.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %d commands to dev guild %s", len(synced), guild.id)
        else:
            synced = await self.tree.sync()
            log.info("Synced %d global commands", len(synced))

    async def connect_clash_api(self) -> None:
        """Authenticate with the official Clash of Clans API.

        Only the public developer API is used. The bot never signs in to a game account.
        With API tokens nothing is signed in to at all; with email/password coc.py signs in to
        the developer portal (developer.clashofclans.com) to create IP-whitelisted keys.
        """
        if self.config.uses_api_tokens:
            await self.coc.login_with_tokens(*self.config.coc_api_tokens)
            mode = f"{len(self.config.coc_api_tokens)} API token(s)"
        else:
            await self.coc.login(self.config.coc_email, self.config.coc_password)
            mode = "developer-portal credentials"
        try:
            await self.coc.search_locations(limit=1)  # one cheap request to validate access
        except coc.Maintenance:
            log.warning("Clash of Clans API is in maintenance; data collection resumes when it ends")
            self.collector.note_maintenance(datetime.now(UTC), announce=False)
        except (coc.Forbidden, coc.InvalidCredentials) as e:
            raise ConfigError(
                "The Clash of Clans API rejected the key. API keys only work from the IP "
                "addresses listed on the key at developer.clashofclans.com; add this machine's "
                f"public IP to the key (or create a new key) and restart. API said: {e}"
            ) from e
        log.info("Connected to the Clash of Clans API using %s", mode)
        await self._refresh_league_tiers()

    async def _refresh_league_tiers(self) -> None:
        """Load the current ranked tiers so tiers added in a game update are recognised."""
        try:
            data = await self.coc.http.request(coc.http.Route("GET", self.coc.http.base_url, "/leaguetiers"))
            items = sorted(data.get("items") or [], key=lambda i: i.get("id", 0))
            added = ranked.set_tiers([i.get("name") for i in items])
            if added:
                log.warning("New ranked league tiers from the API: %s", ", ".join(added))
        except Exception:
            log.warning("Couldn't load ranked league tiers; using the built-in list", exc_info=True)

    async def _sync_emojis(self) -> None:
        """Load ClashHero's app emojis and upload missing official icons (league tiers, labels)."""
        try:
            await emojis.sync_official(self)
        except Exception:
            log.warning("Emoji sync failed; messages fall back to text", exc_info=True)

    async def on_ready(self) -> None:
        log.info("Ready as %s (id=%s)", self.user, getattr(self.user, "id", "?"))
        if not self.collector.clan_loop.is_running():
            await self.collector.start()

    async def close(self) -> None:
        self.collector.stop()
        if self.coc.http is not None:  # only set once login() succeeded
            await self.coc.close()
        await self.db.close()
        await super().close()
