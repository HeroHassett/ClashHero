"""/todo (what your accounts still have to do right now) and /inactive (members not seen lately)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import discord
from discord import app_commands

from clashhero.cogs._base import BaseCog
from clashhero.cogs._info_embeds import ago, short
from clashhero.repos import todo as T
from clashhero.utils.checks import require
from clashhero.utils.fmt import chunk_lines, make_embed, role_name, table, ts
from clashhero.utils.resolve import ResolveError, clan_autocomplete, resolve_clan
from clashhero.utils.seasons import utcnow
from clashhero.utils.style import fail_embed, ok_embed

MAX_FIELDS = 25
MAX_ROWS = 60
KIND_LABEL = {
    "war": "War",
    "cwl": "CWL",
    "ranked": "Ranked",
    "raid": "Raid Weekend",
    "games": "Clan Games",
}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def item_line(item: T.TodoItem) -> str:
    """One line of /todo, e.g. "**War** · Clan: 1 of 2 attacks left · ends <t:..:R>"."""
    head = f"**{KIND_LABEL.get(item.kind, item.kind)}**" + (f" · {item.label}" if item.label else "")
    if item.kind == "games":
        return f"{head}: {item.done:,}/{item.total:,} points · ends {ts(item.ends_at)}"
    if item.total is None:
        return f"{head}: {_plural(item.done, 'attack')} {item.period}".rstrip()
    left = f"{item.left} of {_plural(item.total, 'attack')} left"
    if item.kind == "ranked":
        return f"{head}: {left} {item.period} · resets {ts(item.ends_at)}"
    return f"{head}: {left} · ends {ts(item.ends_at)}"


def account_field(acc: T.AccountTodo) -> tuple[str, str]:
    th = f" · TH{acc.town_hall}" if acc.town_hall else ""
    lines = [item_line(i) for i in acc.items]
    if acc.all_done:
        lines.insert(0, "All done ✅")
    return f"{acc.name}{th} ({acc.player_tag})"[:256], "\n".join(lines)[:1024]


def ping_lines(rows: Sequence[Mapping[str, Any]], links: Mapping[str, int]) -> tuple[list[str], int]:
    """Mentions grouped per Discord user (``<@id> Name, Name``) and how many rows aren't linked."""
    by_user: dict[int, list[str]] = {}
    unlinked = 0
    for r in rows:
        uid = links.get(r["player_tag"])
        if uid is None:
            unlinked += 1
        else:
            by_user.setdefault(uid, []).append(discord.utils.escape_markdown(r["name"] or r["player_tag"]))
    return [f"<@{uid}> " + ", ".join(names) for uid, names in by_user.items()], unlinked


class Todo(BaseCog):
    @app_commands.command(name="todo", description="What your linked accounts still have to do right now")
    @app_commands.describe(user="Whose accounts to check (default: yours)")
    async def todo(self, interaction: discord.Interaction, user: discord.User | None = None) -> None:
        target = user or interaction.user
        tags = await self.db.get_linked_tags(target.id)
        if not tags:
            who = "You have" if target.id == interaction.user.id else f"{target.mention} has"
            raise ResolveError(f"{who} no linked accounts. Use `/link create` first.")
        accounts = await T.account_todos(self.db, tags, utcnow())
        left = sum(1 for a in accounts if not a.all_done)
        embed = await make_embed(
            self.bot,
            interaction.guild_id,
            title=f"To-do · {target.display_name}",
            description="All done ✅ Nothing left right now."
            if not left
            else f"{_plural(left, 'account')} with something left to do.",
        )
        for acc in accounts[:MAX_FIELDS]:
            name, value = account_field(acc)
            embed.add_field(name=name, value=value, inline=False)
        if len(accounts) > MAX_FIELDS:
            embed.set_footer(text=f"Showing {MAX_FIELDS} of {len(accounts)} linked accounts")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="inactive", description="Clan members who haven't been seen for a while")
    @app_commands.describe(
        clan="Clan tag or alias",
        days="Not seen for at least this many days (default 3)",
        ping="Mention their linked Discord users (managers only)",
    )
    @app_commands.autocomplete(clan=clan_autocomplete)
    @app_commands.guild_only()
    async def inactive(
        self,
        interaction: discord.Interaction,
        clan: str | None = None,
        days: app_commands.Range[int, 1, 365] = 3,
        ping: bool = False,
    ) -> None:
        if ping:
            await require(interaction, "bot")
        tag = await resolve_clan(interaction, clan)
        clan_row = await self.db.get_clan_row(tag)
        clan_name = clan_row["name"] if clan_row else tag
        now = utcnow()
        members = await T.clan_member_activity(self.db, tag)
        if not members:
            raise ResolveError(f"No member data for {clan_name} yet. Try again in a few minutes.")
        rows = T.pick_inactive(members, now, days)
        if not rows:
            await interaction.response.send_message(
                embed=ok_embed(
                    f"Everyone in **{clan_name}** has been seen in the last {_plural(days, 'day')}."
                )
            )
            return
        body = table(
            [
                (ago(r["last_seen"], now), r["town_hall"] or "?", role_name(r["clan_role"]), short(r["name"]))
                for r in rows[:MAX_ROWS]
            ],
            ["SEEN", "TH", "ROLE", "NAME"],
        )
        embed = await make_embed(
            self.bot,
            interaction.guild_id,
            title=f"Inactive {days}+ days · {clan_name}",
            description=body[:4096],
        )
        more = f" (showing {MAX_ROWS})" if len(rows) > MAX_ROWS else ""
        embed.set_footer(
            text=f"{len(rows)} of {len(members)} members{more} · last seen is estimated from "
            "observed activity; — means not seen since tracking began"
        )
        if not ping:
            await interaction.response.send_message(embed=embed)
            return
        lines, unlinked = ping_lines(rows, await self.db.links_for_tags([r["player_tag"] for r in rows]))
        if not lines:
            await interaction.response.send_message(
                embeds=[
                    embed,
                    fail_embed("None of them have a linked Discord account, so nobody was pinged."),
                ]
            )
            return
        head = f"Not seen in {clan_name} for {days}+ days:"
        if unlinked:
            lines.append(f"({unlinked} more not linked)")
        chunks = chunk_lines([head, *lines], limit=1900)
        mentions = discord.AllowedMentions(everyone=False, roles=False, users=True)
        await interaction.response.send_message(chunks[0], embed=embed, allowed_mentions=mentions)
        for text in chunks[1:]:
            await interaction.followup.send(text, allowed_mentions=mentions)


async def setup(bot) -> None:
    await bot.add_cog(Todo(bot))
