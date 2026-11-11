"""ClashHero's visual style for Discord messages. Use these helpers so every command looks alike.

Conventions:
- **Chips** are short inline-code boxes for numbers (`` `10/14` ``, `` ` 78%` ``). Pad them to a
  fixed width within a block so columns line up (``chip(value, width)``).
- **Blocks** are a bold header line (with an icon), compact rows underneath, and an optional
  summary line starting with an icon: ``⚔️ 7/7 wars · 21★ · 700%``.
- **Tables** (rankings) use ``utils.fmt.table`` in a code block with a short uppercase header.
- **Results**: ``ok_embed`` (green, ✅) and ``fail_embed`` (red, ❌) for one-line confirmations
  and errors. Keep the text short; put details in fields.
- Separate facts with `` · ``. Use star icons (``_log_embeds.war_stars``) outside code blocks.
- Game icons over plain text: ``ICONS`` resolves to the game's app emojis once uploaded.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence

import discord

from clashhero.utils.fmt import display_width, pad

CRIMSON = discord.Color(0xC9262B)
GOLD = discord.Color(0xE5AC52)
SUCCESS = discord.Color(0x2EAD62)
FAILURE = discord.Color(0xD83A3A)

SEP = " · "

# Category icons: the game's own icon (an app emoji) when it's uploaded, else a standard emoji.
# Looked up when used, since the app's emojis load after startup.
_ICON_FALLBACKS = {
    "elixir": "🟣",
    "dark": "⚫",
    "heroes": "👑",
    "pets": "🐾",
    "equipment": "🛡️",
    "sieges": "🪵",
    "spells": "✨",
    "troops": "🟣",
    "war": "⚔️",
    "cwl": "🏆",
    "legend": "🏅",
    "defense": "🛡️",
    "capital": "🏰",
    "games": "🎯",
    "time": "⏱️",
    "trophy": "🏆",
    "clan": "🏰",
    "rushed": "⚠️",
    "done": "✅",
}
_GAME_ICONS = {
    "elixir": "res_elixir",
    "dark": "res_dark_elixir",
    "heroes": "ui_heroes",
    "pets": "ui_pets",
    "equipment": "ui_equipment",
    "sieges": "ui_donate_sieges",
    "spells": "ui_donate_spells",
    "troops": "ui_donate_troops",
    "war": "ui_sword",
    "cwl": "ui_cwl_star",
    "legend": "lt_legend_i",
    "defense": "ui_shield",
    "capital": "res_capital_gold",
    "games": "ui_clan_games",
    "time": "ui_stopwatch",
    "trophy": "res_trophy",
    "clan": "ui_castle",
}


class _Icons(Mapping[str, str]):
    def __getitem__(self, key: str) -> str:
        from clashhero.services.emojis import EMOJIS

        game = _GAME_ICONS.get(key)
        return (EMOJIS.get(game) if game else "") or _ICON_FALLBACKS[key]

    def __iter__(self) -> Iterator[str]:
        return iter(_ICON_FALLBACKS)

    def __len__(self) -> int:
        return len(_ICON_FALLBACKS)


ICONS: Mapping[str, str] = _Icons()


def chip(value: object, width: int | None = None, *, right: bool = True) -> str:
    """An inline-code chip, optionally padded to ``width`` columns (right-aligned by default)."""
    text = str(value).replace("`", "'")
    if width is not None:
        text = pad(text, width, right=right)
    return f"`{text}`"


def chip_width(values: Iterable[object]) -> int:
    """Width that fits every value, for padding a column of chips."""
    return max((display_width(str(v)) for v in values), default=0)


def stars(n: int | None, total: int = 3) -> str:
    n = max(0, min(total, n or 0))
    return "★" * n + "☆" * (total - n)


def header(title: str, icon: str | None = None, extra: str | None = None) -> str:
    """Bold block header: ``🏆 **Nov 2026** (#15, TH18)``."""
    icon_txt = f"{ICONS.get(icon, icon)} " if icon else ""
    extra_txt = f" {extra}" if extra else ""
    return f"{icon_txt}**{title}**{extra_txt}"


def summary(icon: str, *parts: object) -> str:
    """Summary line under a block: ``⚔️ 7/7 wars · 21★ · 700%``."""
    return f"{ICONS.get(icon, icon)} " + SEP.join(str(p) for p in parts if p not in (None, ""))


def bar(fraction: float, width: int = 12) -> str:
    """Progress bar like ``▰▰▰▰▱▱▱▱``."""
    filled = round(max(0.0, min(1.0, fraction)) * width)
    return "▰" * filled + "▱" * (width - filled)


def grid(cells: Sequence[str], per_row: int = 3) -> list[str]:
    """Lay chips out ``per_row`` to a line."""
    return [" ".join(cells[i : i + per_row]) for i in range(0, len(cells), per_row)]


def ok_embed(text: str, *, title: str | None = None) -> discord.Embed:
    return discord.Embed(title=title, description=f"✅ {text}", color=SUCCESS)


def fail_embed(text: str, *, title: str | None = None) -> discord.Embed:
    return discord.Embed(title=title, description=f"❌ {text}", color=FAILURE)
