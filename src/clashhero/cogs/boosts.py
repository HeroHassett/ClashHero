"""/boosts: which clan members have a super troop boosted right now (on-demand API read)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

import discord
from discord import app_commands

from clashhero.cogs._base import BaseCog
from clashhero.cogs._log_embeds import clan_author, th_icon
from clashhero.services.emojis import EMOJIS
from clashhero.services.snapshots import active_super_troops
from clashhero.utils.fmt import chunk_lines, guild_color
from clashhero.utils.resolve import clan_autocomplete, resolve_clan

# Discord caps an embed at 6000 chars in total; leave room for title/description/footer.
EMBED_BUDGET = 5000
MAX_FIELDS = 25


def group_boosts(players: Iterable[tuple[str, int, Sequence[str]]]) -> dict[str, list[tuple[str, int]]]:
    """players: (name, town hall, active super troops) -> {troop: [(name, town hall)]}.

    Troops are ordered by how many members boosted them; players by Town Hall, then name."""
    out: dict[str, list[tuple[str, int]]] = {}
    for name, th, troops in players:
        for troop in troops:
            out.setdefault(troop, []).append((name, th))
    for members in out.values():
        members.sort(key=lambda m: (-m[1], m[0].lower()))
    return dict(sorted(out.items(), key=lambda kv: (-len(kv[1]), kv[0])))


def boosts_embeds(
    clan_name: str,
    clan_tag: str,
    by_troop: Mapping[str, Sequence[tuple[str, int]]],
    boosted: int,
    members: int,
    color: discord.Color | int | None = None,
    *,
    badge_url: str | None = None,
) -> list[discord.Embed]:
    """One field per super troop (its icon, name and how many have it) listing who has it
    active with their Town Hall icon; the clan (badge, linked) as the author. Split across embeds
    when needed."""
    fields: list[tuple[str, str]] = []
    for troop, players in by_troop.items():
        lines = [f"{th_icon(th)} {discord.utils.escape_markdown(name)}".strip() for name, th in players]
        icon = EMOJIS.unit(troop)
        for i, chunk in enumerate(chunk_lines(lines, 1000)):
            label = f"{troop} ({len(players)})" if i == 0 else f"{troop} (cont.)"
            fields.append((f"{icon} {label}" if icon else label, chunk.rstrip("\n") or "—"))

    if by_troop:
        desc = f"**{boosted}** of {members} members have a super troop active."
    else:
        desc = f"Nobody in **{clan_name}** has a super troop active right now."

    pages: list[list[tuple[str, str]]] = [[]]
    size = 0
    for name, value in fields:
        cost = len(name) + len(value)
        if pages[-1] and (len(pages[-1]) >= MAX_FIELDS or size + cost > EMBED_BUDGET):
            pages.append([])
            size = 0
        pages[-1].append((name, value))
        size += cost

    embeds = []
    for i, page in enumerate(pages, 1):
        suffix = f" ({i}/{len(pages)})" if len(pages) > 1 else ""
        e = discord.Embed(
            title=f"Super troop boosts{suffix}",
            description=desc if i == 1 else None,
            color=color,
        )
        clan_author(e, clan_name, clan_tag, badge_url)
        for name, value in page:
            e.add_field(name=name, value=value, inline=True)
        e.set_footer(text=clan_tag)
        embeds.append(e)
    return embeds


class Boosts(BaseCog):
    """Super troop boosts."""

    @app_commands.command(name="boosts", description="Show which clan members have super troops boosted")
    @app_commands.describe(clan="Clan tag or alias (defaults to this channel's clan)")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def boosts(self, interaction: discord.Interaction, clan: str | None = None) -> None:
        tag = await resolve_clan(interaction, clan, must_be_tracked=False)
        await interaction.response.defer()
        c = await self.coc.get_clan(tag)
        tags = [m.tag for m in c.members]
        players = []
        async for p in self.coc.get_players(tags):
            players.append((p.name, p.town_hall or 0, active_super_troops(getattr(p, "_raw_data", None))))
        by_troop = group_boosts(players)
        boosted = sum(1 for _, _, troops in players if troops)
        color = await guild_color(self.bot, interaction.guild_id)
        badge = getattr(getattr(c, "badge", None), "url", None)
        embeds = boosts_embeds(c.name, c.tag, by_troop, boosted, len(players), color, badge_url=badge)
        for embed in embeds:
            await interaction.followup.send(embed=embed)


async def setup(bot) -> None:
    await bot.add_cog(Boosts(bot))
