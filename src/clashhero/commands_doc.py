"""Generate docs/commands.md (the website's Commands page) from the bot's real slash commands.

    uv run python -m clashhero.commands_doc        # rewrite docs/commands.md

tests/test_commands_doc.py fails if the page is out of date, so it can't drift from the code.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from discord import app_commands

SECTION_TITLES = {
    "Setup": "Server setup",
    "Accounts": "Accounts",
    "Logs": "Logs",
    "Live": "Live messages",
    "Reminders": "Reminders",
    "Todo": "To-do and activity",
    "Autoboards": "Scheduled boards",
    "Legends": "Legends and ranked",
    "War": "War",
    "CWL": "Clan War League",
    "Capital": "Clan Capital",
    "ClanGames": "Clan Games",
    "Info": "Clan and player info",
    "Summary": "Family summaries",
    "Boosts": "Super troop boosts",
    "Army": "Armies and help",
    "Rosters": "Rosters",
    "Bases": "Bases",
    "Flags": "Bans and strikes",
    "Autoroles": "Autoroles and nicknames",
    "Export": "Exports",
    "Privacy": "Privacy and about",
}

HEADER = """---
title: ClashHero Commands
---

# ClashHero commands

Type `/` in Discord to use these. Options in [brackets] are optional. Commands that change server
settings need **Manage Server** or the matching manager role set with `/config roles`.

"""

FOOTER = """
---

This page is generated from the bot's code. This material is unofficial and is not endorsed by
Supercell. For more information see Supercell's Fan Content Policy:
[www.supercell.com/fan-content-policy](https://www.supercell.com/fan-content-policy).
"""


def _usage(cmd: app_commands.Command) -> str:
    parts = [f"/{cmd.qualified_name}"]
    for p in cmd.parameters:
        parts.append(p.name if p.required else f"[{p.name}]")
    return " ".join(parts)


def render(bot) -> str:
    sections: dict[str, list[tuple[str, str]]] = {}
    for cog_name, cog in bot.cogs.items():
        rows = [
            (_usage(cmd), cmd.description)
            for cmd in cog.walk_app_commands()
            if isinstance(cmd, app_commands.Command)
        ]
        if rows:
            sections[SECTION_TITLES.get(cog_name, cog_name)] = sorted(rows)
    order = list(SECTION_TITLES.values())
    out = [HEADER]
    for title in sorted(sections, key=lambda t: (order.index(t) if t in order else len(order), t)):
        out.append(f"## {title}\n\n| Command | What it does |\n| --- | --- |\n")
        for usage, desc in sections[title]:
            out.append(f"| `{usage}` | {desc.replace('|', '/')} |\n")
        out.append("\n")
    out.append(FOOTER)
    return "".join(out).rstrip() + "\n"


async def build() -> str:
    """Load every cog offline (no Discord or Clash login) and render the page."""
    from clashhero.bot import ClashHeroBot, discover_extensions
    from clashhero.config import Config

    bot = ClashHeroBot(Config("-", "-", "-", "-", Path(":memory:"), None, 60, 60, 120, 600, "WARNING"))
    await bot.db.connect()
    try:
        for ext in discover_extensions():
            await bot.load_extension(ext)
        return render(bot)
    finally:
        for name in list(bot.cogs):
            await bot.remove_cog(name)
        await bot.db.close()


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/commands.md")
    path.write_text(asyncio.run(build()))
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
