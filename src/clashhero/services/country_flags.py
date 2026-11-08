"""Country flags for players in a country leaderboard: an emoji for Discord text, and a small PNG
for image cards (from flagcdn.com, cached in memory).

Only flagcdn.com is ever contacted, over https, with a short timeout and a size cap; a failure
just means no flag.
"""

from __future__ import annotations

import logging
import re
from collections import OrderedDict

log = logging.getLogger(__name__)

FLAG_URL = "https://flagcdn.com/w80/{code}.png"  # 80px wide PNGs of every ISO 3166-1 flag
TIMEOUT_SECONDS = 4
MAX_BYTES = 64 * 1024
CACHE_SIZE = 300

_CODE = re.compile(r"^[A-Za-z]{2}$")
_cache: OrderedDict[str, bytes] = OrderedDict()


def flag_emoji(code: str | None) -> str:
    """``"US"`` -> 🇺🇸 (regional indicator letters, which Discord renders as the flag)."""
    if not code or not _CODE.match(code):
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in code.upper())


async def fetch_flag(code: str | None, *, session_factory=None) -> bytes | None:
    """The country's flag as PNG bytes, or None (unknown code, network error, oversized)."""
    if not code or not _CODE.match(code):
        return None
    key = code.lower()
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    import aiohttp

    factory = session_factory or (
        lambda: aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS))
    )
    try:
        async with factory() as session:
            async with session.get(FLAG_URL.format(code=key), allow_redirects=False) as resp:
                if resp.status != 200:
                    return None
                data = await resp.content.read(MAX_BYTES + 1)
    except Exception as e:  # noqa: BLE001
        log.debug("Couldn't fetch the %s flag: %s", key, e)
        return None
    if len(data) > MAX_BYTES or not data.startswith(b"\x89PNG"):
        return None
    _cache[key] = data
    if len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    return data
