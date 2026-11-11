"""/todo (what your accounts still have to do right now) and /inactive (members not seen lately)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import discord
from discord import app_commands

from clashhero.cogs._base import BaseCog
from clashhero.cogs._info_embeds import ago, short
from clashhero.cogs._live_embeds import clan_row_header
from clashhero.cogs._log_embeds import player_ref, th_icon
from clashhero.repos import todo as T
from clashhero.services.emojis import EMOJIS
from clashhero.utils.checks import require
from clashhero.utils.fmt import chunk_lines, make_embed, role_name, table, ts
from clashhero.utils.resolve import ResolveError, clan_autocomplete, resolve_clan
from clashhero.utils.seasons import utcnow
from clashhero.utils.style import ICONS, fail_embed, ok_embed

MAX_PAGES = 5  # /todo messages at most (one embed each)
PAGE_LIMIT = 4000  # characters of account blocks per embed description
MAX_ROWS = 60
# What each kind of to-do counts, and its game icon (an ICONS key; ranked uses the league's icon).
KIND_NOUN = {"war": "war attack", "cwl": "CWL attack", "ranked": "attack", "raid": "raid attack"}
KIND_ICON = {"war": "war", "cwl": "cwl", "ranked": "trophy", "raid": "capital", "games": "games"}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def item_icon(item: T.TodoItem) -> str:
    if item.kind == "ranked" and (league := EMOJIS.league(item.label)):
        return league
    return ICONS[KIND_ICON[item.kind]] if item.kind in KIND_ICON else "•"


def item_line(item: T.TodoItem) -> str:
    """One line of /todo with the kind's game icon, e.g.
    "⚔ **1** of 2 war attacks left in **Main** · ends <t:..:R>"."""
    where = f" in **{discord.utils.escape_markdown(item.label)}**" if item.label else ""
    if item.kind == "games":
        text = f"**{item.done:,}**/{item.total:,} Clan Games points · ends {ts(item.ends_at)}"
    elif item.total is None:
        text = f"**{item.done}** {'attack' if item.done == 1 else 'attacks'} {item.period}".rstrip() + where
    else:
        noun = KIND_NOUN.get(item.kind, "attack")
        period = f" {item.period}" if item.period else ""
        verb = "resets" if item.kind == "ranked" else "ends"
        text = (
            f"**{item.left}** of {_plural(item.total, noun)} left{period}{where} · {verb} {ts(item.ends_at)}"
        )
    return f"{item_icon(item)} {text}"


def account_block(acc: T.AccountTodo) -> list[str]:
    """The account (Town Hall icon, name linked to the profile, tag), then what it has left."""
    head = " ".join(x for x in (th_icon(acc.town_hall), f"**{player_ref(acc.name, acc.player_tag)}**") if x)
    head += f" ({acc.player_tag})"
    if acc.all_done:
        head += f" · {ICONS['done']} All done"
    return [head, *(item_line(i) for i in acc.items)]


def todo_pages(accounts: Sequence[T.AccountTodo], limit: int = PAGE_LIMIT) -> list[str]:
    """Account blocks (those with something left first) packed into descriptions of at most
    ``limit`` characters, a blank line between accounts."""
    pages: list[str] = []
    cur = ""
    for acc in sorted(accounts, key=lambda a: a.all_done):
        block = "\n".join(account_block(acc))[:limit]
        if cur and len(cur) + 2 + len(block) > limit:
            pages.append(cur)
            cur = ""
        cur = f"{cur}\n\n{block}" if cur else block
    if cur:
        pages.append(cur)
    return pages


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
        summary = (
            f"{ICONS['done']} All done. Nothing left right now."
            if not left
            else f"**{left}** of {_plural(len(accounts), 'account')} with something left to do."
        )
        pages = todo_pages(accounts, PAGE_LIMIT - len(summary) - 2)
        shown = pages[:MAX_PAGES]
        embeds = []
        for i, page in enumerate(shown, 1):
            suffix = f" ({i}/{len(shown)})" if len(shown) > 1 else ""
            embed = await make_embed(
                self.bot,
                interaction.guild_id,
                title=f"To-do · {target.display_name}{suffix}",
                description=f"{summary}\n\n{page}" if i == 1 else page,
            )
            embeds.append(embed)
        if len(pages) > MAX_PAGES:
            embeds[-1].set_footer(text=f"Some of the {len(accounts)} linked accounts aren't shown")
        await interaction.response.send_message(embed=embeds[0])
        for embed in embeds[1:]:
            await interaction.followup.send(embed=embed)

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
        more = f" (showing {MAX_ROWS})" if len(rows) > MAX_ROWS else ""
        head = f"**{len(rows)}** of {len(members)} members not seen for **{days}+** days{more}"
        embed = await make_embed(
            self.bot,
            interaction.guild_id,
            title=f"Inactive {days}+ days",
            description=f"{head}\n{body}",
        )
        clan_row_header(embed, clan_row, tag)
        embed.set_footer(
            text="Last seen is estimated from observed activity. — means not seen since tracking began."
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
