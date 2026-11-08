"""ClashHero's own emojis (Discord application emojis, shown as "from the ClashHero app").

Two sources:
- **Official icons from the API**, synced automatically at startup: the ranked league tiers and the
  clan/player labels. Anything already uploaded is skipped.
- **Game art you provide** (e.g. from Supercell's Fan Kit): put PNG/JPG files in a folder and run
  ``clashhero-emojis <folder>``. File names become emoji names: ``Barbarian King.png`` ->
  ``barbarian_king``, ``Town Hall 18.png`` or ``th18.png`` -> ``th18``.

Everything that shows an emoji goes through :data:`EMOJIS` and falls back to plain text when an
emoji isn't uploaded, so missing icons never break a message.

Naming (2-32 chars, lowercase letters, digits, underscores):
- league tier ``Legend I`` -> ``lt_legend_i``; label ``Clan Wars`` -> ``lbl_clan_wars``
- units: slug of the in-game name (``barbarian_king``, ``rage_spell``, ``l_a_s_s_i``)
- town halls: ``th1`` ... ``th20``
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from clashhero.bot import ClashHeroBot

log = logging.getLogger(__name__)

EMOJI_SIZE = 128  # px; Discord recommends 128x128 and allows up to 256 KB
MAX_APP_EMOJIS = 2000


def slug(name: str) -> str:
    """Emoji-safe name: lowercase letters, digits and underscores, 2-32 chars."""
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return (s or "x")[:32].ljust(2, "_")


def unit_key(name: str) -> str:
    return slug(name)


def town_hall_key(level: int | str) -> str:
    return f"th{int(level)}"


def file_key(path: Path) -> str:
    """Emoji name for an image file in a Fan Kit folder."""
    stem = path.stem
    m = re.fullmatch(r"(?i)(?:town\s*hall|th)[\s_-]*(\d{1,2})", stem.strip())
    return town_hall_key(m.group(1)) if m else slug(stem)


class EmojiRegistry:
    """name -> rendered emoji (``<:name:id>``). Empty strings for anything not uploaded."""

    def __init__(self) -> None:
        self._by_name: dict[str, str] = {}

    def load(self, emojis: list[discord.Emoji]) -> None:
        self._by_name = {e.name: str(e) for e in emojis}

    def add(self, emoji: discord.Emoji) -> None:
        self._by_name[emoji.name] = str(emoji)

    def __len__(self) -> int:
        return len(self._by_name)

    def __contains__(self, name: str) -> bool:
        return name in self._by_name

    def get(self, name: str, fallback: str = "") -> str:
        return self._by_name.get(name, fallback)

    def emoji_id(self, name: str) -> int | None:
        """The Discord ID of the uploaded emoji ``name``, or None."""
        m = re.fullmatch(r"<a?:\w+:(\d+)>", self._by_name.get(name, ""))
        return int(m.group(1)) if m else None

    def league(self, tier: str | None, fallback: str = "") -> str:
        return self.get(f"lt_{slug(tier)}"[:32], fallback) if tier else fallback

    def label(self, name: str | None, fallback: str = "") -> str:
        return self.get(f"lbl_{slug(name)}"[:32], fallback) if name else fallback

    def unit(self, name: str | None, fallback: str = "") -> str:
        return self.get(unit_key(name), fallback) if name else fallback

    def town_hall(self, level: int | None, fallback: str = "") -> str:
        return self.get(town_hall_key(level), fallback) if level else fallback


EMOJIS = EmojiRegistry()


def emoji_image(data: bytes) -> bytes:
    """Square PNG at EMOJI_SIZE with transparent padding, so any icon uploads cleanly."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGBA")
        im.thumbnail((EMOJI_SIZE, EMOJI_SIZE), Image.LANCZOS)
        canvas = Image.new("RGBA", (EMOJI_SIZE, EMOJI_SIZE), (0, 0, 0, 0))
        canvas.paste(im, ((EMOJI_SIZE - im.width) // 2, (EMOJI_SIZE - im.height) // 2), im)
        out = io.BytesIO()
        canvas.save(out, "PNG", optimize=True)
        return out.getvalue()


async def _upload(client: discord.Client, name: str, data: bytes) -> discord.Emoji | None:
    try:
        emoji = await client.create_application_emoji(name=name, image=emoji_image(data))
        await asyncio.sleep(1)  # be gentle with Discord's rate limits on a first big sync
        return emoji
    except discord.HTTPException as e:
        log.warning("Couldn't upload emoji %s: %s", name, e)
        return None


async def official_icons(bot: ClashHeroBot) -> dict[str, str]:
    """emoji name -> icon URL for every official icon the API provides."""
    import coc

    out: dict[str, str] = {}
    http = bot.coc.http
    for path, prefix, size in (
        ("/leaguetiers", "lt_", "small"),
        ("/labels/clans", "lbl_", "small"),
        ("/labels/players", "lbl_", "small"),
    ):
        data = await http.request(coc.http.Route("GET", http.base_url, path))
        for item in data.get("items") or []:
            url = (item.get("iconUrls") or {}).get(size)
            if item.get("name") and url:
                out[f"{prefix}{slug(item['name'])}"[:32]] = url
    return out


async def sync_official(bot: ClashHeroBot) -> int:
    """Load the app's emojis and upload any official API icon that's missing. Returns uploads."""
    EMOJIS.load(await bot.fetch_application_emojis())
    try:
        wanted = await official_icons(bot)
    except Exception:
        log.warning("Couldn't list official icons; using emojis already uploaded", exc_info=True)
        return 0
    missing = {name: url for name, url in wanted.items() if name not in EMOJIS}
    if not missing:
        return 0
    if len(EMOJIS) + len(missing) > MAX_APP_EMOJIS:
        log.warning("Not enough app emoji slots for %d official icons", len(missing))
        return 0
    import aiohttp

    uploaded = 0
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        for name, url in sorted(missing.items()):
            try:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    data = await resp.read()
            except Exception as e:  # noqa: BLE001
                log.warning("Couldn't download icon %s: %s", name, e)
                continue
            emoji = await _upload(bot, name, data)
            if emoji is not None:
                EMOJIS.add(emoji)
                uploaded += 1
    log.info("Uploaded %d official icons as emojis (%d total)", uploaded, len(EMOJIS))
    return uploaded


# ------------------------------------------------------------------ CLI: upload your own icons

IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp"}


async def upload_folder(token: str, folder: Path, *, replace: bool = False) -> None:
    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in IMAGE_TYPES)
    if not files:
        print(f"No images found in {folder}")
        return
    client = discord.Client(intents=discord.Intents.none())
    await client.login(token)
    try:
        existing = {e.name: e for e in await client.fetch_application_emojis()}
        done = skipped = failed = 0
        for path in files:
            name = file_key(path)
            if name in existing:
                if not replace:
                    skipped += 1
                    continue
                await existing[name].delete()
            emoji = await _upload(client, name, path.read_bytes())
            if emoji is None:
                failed += 1
            else:
                done += 1
                print(f"  uploaded {name}  <- {path.name}")
        print(f"Done: {done} uploaded, {skipped} already there, {failed} failed.")
    finally:
        await client.close()


def main() -> None:
    from clashhero.config import Config, ConfigError

    parser = argparse.ArgumentParser(
        prog="clashhero-emojis",
        description="Upload a folder of icons (e.g. from Supercell's Fan Kit) as ClashHero emojis.",
    )
    parser.add_argument(
        "folder", type=Path, help="folder of PNG/JPG/WebP icons; file names become emoji names"
    )
    parser.add_argument("--replace", action="store_true", help="re-upload icons that already exist")
    args = parser.parse_args()
    try:
        cfg = Config.from_env()
    except ConfigError as e:
        sys.exit(f"Configuration error: {e}")
    if not args.folder.is_dir():
        sys.exit(f"Not a folder: {args.folder}")
    asyncio.run(upload_folder(cfg.discord_token, args.folder, replace=args.replace))


if __name__ == "__main__":
    main()
