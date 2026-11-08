"""Permission model:

- **Bot manager**: Manage Server, or the configured ``bot_manager_role``. Full admin.
- **Roster / Links / Flags manager**: bot managers, or the matching configured role.
Use as decorators on app commands, or call :func:`has_permission` inside a command/button.
"""

from __future__ import annotations

from typing import Literal

import discord
from discord import app_commands

Scope = Literal["bot", "roster", "links", "flags"]
_COLUMN = {
    "bot": "bot_manager_role",
    "roster": "roster_manager_role",
    "links": "links_manager_role",
    "flags": "flags_manager_role",
}


class MissingManagerRole(app_commands.CheckFailure):
    def __init__(self, scope: Scope) -> None:
        label = {
            "bot": "Bot Manager",
            "roster": "Roster Manager",
            "links": "Links Manager",
            "flags": "Flags Manager",
        }[scope]
        super().__init__(f"You need Manage Server or the {label} role to do that.")


async def has_permission(interaction: discord.Interaction, scope: Scope = "bot") -> bool:
    member = interaction.user
    if not isinstance(member, discord.Member) or interaction.guild_id is None:
        return False
    if member.guild_permissions.manage_guild:
        return True
    settings = await interaction.client.db.get_guild_settings(interaction.guild_id)
    role_ids = {r.id for r in member.roles}
    if settings["bot_manager_role"] in role_ids:
        return True
    return scope != "bot" and settings[_COLUMN[scope]] in role_ids


def manager(scope: Scope = "bot"):
    """App-command check decorator: ``@manager("roster")``."""

    async def predicate(interaction: discord.Interaction) -> bool:
        if await has_permission(interaction, scope):
            return True
        raise MissingManagerRole(scope)

    return app_commands.check(predicate)


async def require(interaction: discord.Interaction, scope: Scope = "bot") -> None:
    """Imperative form for buttons/selects; raises MissingManagerRole."""
    if not await has_permission(interaction, scope):
        raise MissingManagerRole(scope)
