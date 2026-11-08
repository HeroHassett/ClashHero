"""Unit icons (troops, spells, heroes, pets, equipment) for image cards.

``fetch_icons(names)`` maps in-game names to PNG bytes, looking in order at:

1. a local folder of game art: ``$CLASHHERO_ICON_DIR``, else ``game-assets/icons`` in the project
   (git-ignored, filled from Supercell's Fan Kit), with files named ``unit_key(name) + ".png"``;
2. ClashHero's own Discord application emoji of that name, downloaded from Discord's CDN.

Names found in neither are left out and the card draws a placeholder tile. Downloads only go to
Discord's CDN over https, give up after a few seconds and are capped in size. Icons found are kept
in a small in-memory LRU cache; failures aren't, so the next card tries again.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import OrderedDict
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from clashhero.services.emojis import EMOJIS, unit_key

log = logging.getLogger(__name__)

ICON_DIR_ENV = "CLASHHERO_ICON_DIR"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ICON_DIR = PROJECT_ROOT / "game-assets" / "icons"

EMOJI_URL = "https://cdn.discordapp.com/emojis/{id}.png?size=128"
ICON_HOSTS = frozenset({"cdn.discordapp.com"})
MAX_ICON_BYTES = 512 * 1024
TIMEOUT = 4.0
MAX_CONCURRENT = 16


def icon_dir() -> Path:
    raw = os.getenv(ICON_DIR_ENV, "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_ICON_DIR


def allowed(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and (parsed.hostname or "") in ICON_HOSTS


def emoji_url(name: str) -> str | None:
    """CDN URL of the app emoji for the unit ``name``, or None when it isn't uploaded."""
    emoji_id = EMOJIS.emoji_id(unit_key(name))
    return EMOJI_URL.format(id=emoji_id) if emoji_id else None


def _read_local(folder: Path, names: list[str]) -> dict[str, bytes]:
    out = {}
    for name in names:
        path = folder / f"{unit_key(name)}.png"
        try:
            if path.is_file() and path.stat().st_size <= MAX_ICON_BYTES:
                out[name] = path.read_bytes()
        except OSError as exc:
            log.debug("Couldn't read icon %s: %r", path, exc)
    return out


class IconCache:
    """Icon bytes by in-game name, with an LRU cache of everything found.

    ``session_factory`` makes the HTTP session for downloads (an ``aiohttp.ClientSession`` by
    default); tests pass a fake one."""

    def __init__(
        self,
        *,
        max_entries: int = 300,
        timeout: float = TIMEOUT,
        session_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.max_entries = max_entries
        self.timeout = timeout
        self._session_factory = session_factory
        self._cache: OrderedDict[str, bytes] = OrderedDict()

    def _remember(self, name: str, data: bytes) -> None:
        self._cache[name] = data
        self._cache.move_to_end(name)
        while len(self._cache) > self.max_entries:
            self._cache.popitem(last=False)

    async def fetch(self, names: Iterable[str | None]) -> dict[str, bytes]:
        """``{name: PNG bytes}`` for each distinct name an icon was found for."""
        out: dict[str, bytes] = {}
        wanted = []
        for name in dict.fromkeys(n for n in names if n):
            if name in self._cache:
                self._cache.move_to_end(name)
                out[name] = self._cache[name]
            else:
                wanted.append(name)
        if not wanted:
            return out

        local = await asyncio.to_thread(_read_local, icon_dir(), wanted)
        downloads: dict[str, str] = {}
        for name in wanted:
            if name in local:
                out[name] = local[name]
                self._remember(name, local[name])
                continue
            url = emoji_url(name)
            if url is None:
                continue
            if allowed(url):
                downloads[name] = url
            else:
                log.debug("Not fetching icon %s from %s: host not allowed", name, url)
        if downloads:
            got = await self._download_all(list(downloads.values()))
            for name, data in zip(downloads, got, strict=True):
                if data:
                    out[name] = data
                    self._remember(name, data)
        return out

    def _session(self):
        if self._session_factory is not None:
            return self._session_factory()
        import aiohttp

        return aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout),
            connector=aiohttp.TCPConnector(limit=MAX_CONCURRENT),
        )

    async def _download_all(self, urls: list[str]) -> list[bytes | None]:
        try:
            async with self._session() as session:
                return list(await asyncio.gather(*(self._safe(session, u) for u in urls)))
        except Exception as exc:
            log.debug("Icon downloads failed: %r", exc)
            return [None] * len(urls)

    async def _safe(self, session, url: str) -> bytes | None:
        try:
            return await asyncio.wait_for(self._download(session, url), self.timeout)
        except Exception as exc:  # timeouts, HTTP errors, oversized bodies: no icon
            log.debug("Icon download failed for %s: %r", url, exc)
            return None

    async def _download(self, session, url: str) -> bytes:
        async with session.get(url, allow_redirects=False) as resp:
            resp.raise_for_status()
            if resp.status != 200:
                raise ValueError(f"unexpected status {resp.status}")
            if (resp.content_length or 0) > MAX_ICON_BYTES:
                raise ValueError("icon too large")
            data = bytearray()
            async for chunk in resp.content.iter_chunked(64 * 1024):
                data += chunk
                if len(data) > MAX_ICON_BYTES:
                    raise ValueError("icon too large")
            return bytes(data)


icon_cache = IconCache()


async def fetch_icons(names: Iterable[str | None]) -> dict[str, bytes]:
    """In-game name -> PNG bytes for every name an icon was found for (see the module docs)."""
    return await icon_cache.fetch(names)
