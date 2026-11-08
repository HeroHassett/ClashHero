"""War base layouts: register and post base cards, and track
which layouts have already been used in the current CWL so the clan doesn't repeat them."""

from __future__ import annotations

import sqlite3

import discord
from discord import app_commands

from clashhero.cogs._base import BaseCog
from clashhero.repos import bases as repo
from clashhero.utils.checks import manager
from clashhero.utils.fmt import chunk_lines, make_embed, ts
from clashhero.utils.resolve import ResolveError, player_autocomplete
from clashhero.utils.seasons import cwl_season_id

TYPE_CHOICES = [
    app_commands.Choice(name=t.upper() if t == "cwl" else t.title(), value=t) for t in repo.BASE_TYPES
]
VOTE_EMOJIS = ("👍", "❤️", "🔥", "👎")


async def base_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    rows = await repo.all_bases(interaction.client.db, interaction.guild_id)
    return [
        app_commands.Choice(name=f"{r['name']} (TH{r['town_hall']}, {r['type']})"[:100], value=r["name"])
        for r in repo.filter_bases(rows, current)
    ]


async def get_base_or_fail(interaction: discord.Interaction, name: str):
    row = await repo.get_base_by_name(interaction.client.db, interaction.guild_id, name)
    if row is None:
        raise ResolveError(f"No base named **{name}**.")
    return row


def render_base_card(base, color: discord.Color, uses_this_cwl: int | None = None) -> discord.Embed:
    embed = discord.Embed(title=f"TH{base['town_hall']} · {base['name']}", color=color)
    embed.add_field(
        name="Type", value=base["type"].upper() if base["type"] == "cwl" else base["type"].title()
    )
    if uses_this_cwl is not None:
        embed.add_field(name="Used this CWL", value=str(uses_this_cwl))
    if base["notes"]:
        embed.add_field(name="Notes", value=base["notes"][:1024], inline=False)
    if base["image_url"]:
        embed.set_image(url=base["image_url"])
    embed.add_field(name="Added by", value=f"<@{base['added_by']}>")
    return embed


def base_view(base) -> discord.ui.View | None:
    if not repo.is_copy_link(base["link"]):
        return None
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Copy base", url=base["link"], style=discord.ButtonStyle.link))
    return view


class Bases(BaseCog):
    bases = app_commands.Group(name="bases", description="War base layouts and CWL usage", guild_only=True)

    # ------------------------------------------------------------------ registry

    @bases.command(name="add", description="Register a base layout")
    @app_commands.choices(type=TYPE_CHOICES)
    @app_commands.describe(
        link="In-game 'copy base' link", image="Screenshot of the base", town_hall="Town Hall level"
    )
    @manager()
    async def add(
        self,
        interaction: discord.Interaction,
        name: app_commands.Range[str, 1, 60],
        town_hall: app_commands.Range[int, 1, 20],
        type: app_commands.Choice[str] | None = None,
        link: str | None = None,
        image: discord.Attachment | None = None,
        notes: app_commands.Range[str, 1, 500] | None = None,
    ) -> None:
        if link and not repo.is_copy_link(link):
            raise ResolveError(
                "The link should be the in-game share link (https://link.clashofclans.com/...)."
            )
        try:
            await repo.add_base(
                self.db,
                interaction.guild_id,
                name,
                town_hall,
                added_by=interaction.user.id,
                type=type.value if type else "war",
                link=link,
                image_url=image.url if image else None,
                notes=notes,
            )
        except sqlite3.IntegrityError as e:
            raise ResolveError(f"A base named **{name}** already exists.") from e
        await interaction.response.send_message(f"Added base **{name}** (TH{town_hall}).", ephemeral=True)

    @bases.command(name="edit", description="Edit a base layout")
    @app_commands.choices(type=TYPE_CHOICES)
    @app_commands.autocomplete(name=base_autocomplete)
    @manager()
    async def edit(
        self,
        interaction: discord.Interaction,
        name: str,
        new_name: app_commands.Range[str, 1, 60] | None = None,
        town_hall: app_commands.Range[int, 1, 20] | None = None,
        type: app_commands.Choice[str] | None = None,
        link: str | None = None,
        image: discord.Attachment | None = None,
        notes: app_commands.Range[str, 1, 500] | None = None,
    ) -> None:
        base = await get_base_or_fail(interaction, name)
        if link and not repo.is_copy_link(link):
            raise ResolveError(
                "The link should be the in-game share link (https://link.clashofclans.com/...)."
            )
        fields = {
            k: v
            for k, v in dict(
                name=new_name,
                town_hall=town_hall,
                type=type.value if type else None,
                link=link,
                image_url=image.url if image else None,
                notes=notes,
            ).items()
            if v is not None
        }
        try:
            await repo.update_base(self.db, base["id"], **fields)
        except sqlite3.IntegrityError as e:
            raise ResolveError(f"A base named **{new_name}** already exists.") from e
        await self._refresh_post(base["id"])
        await interaction.response.send_message(
            f"Updated **{fields.get('name', base['name'])}**.", ephemeral=True
        )

    @bases.command(name="delete", description="Delete a base layout and its usage history")
    @app_commands.autocomplete(name=base_autocomplete)
    @manager()
    async def delete(self, interaction: discord.Interaction, name: str) -> None:
        base = await get_base_or_fail(interaction, name)
        await repo.delete_base(self.db, base["id"])
        await interaction.response.send_message(f"Deleted **{base['name']}**.", ephemeral=True)

    @bases.command(name="post", description="Post a base card with a copy-link button")
    @app_commands.autocomplete(name=base_autocomplete)
    async def post(
        self, interaction: discord.Interaction, name: str, channel: discord.TextChannel | None = None
    ) -> None:
        base = await get_base_or_fail(interaction, name)
        target = channel or interaction.channel
        color = (await make_embed(self.bot, interaction.guild_id)).color
        kwargs = {"embed": render_base_card(base, color)}
        if (view := base_view(base)) is not None:
            kwargs["view"] = view
        msg = await target.send(**kwargs)
        if await repo.get_voting(self.db, interaction.guild_id):
            for emoji in VOTE_EMOJIS:
                try:
                    await msg.add_reaction(emoji)
                except discord.HTTPException:
                    break
        await repo.update_base(self.db, base["id"], channel_id=target.id, message_id=msg.id)
        await interaction.response.send_message(
            f"Posted **{base['name']}** in {target.mention}.", ephemeral=True
        )

    @bases.command(name="voting", description="Turn vote reactions on base posts on or off")
    @manager()
    async def voting(self, interaction: discord.Interaction, enabled: bool) -> None:
        await repo.set_voting(self.db, interaction.guild_id, enabled)
        await interaction.response.send_message(
            f"Vote reactions on base posts are now **{'on' if enabled else 'off'}**.", ephemeral=True
        )

    # ------------------------------------------------------------------ CWL usage

    @bases.command(name="use", description="Mark a base as used in this month's CWL")
    @app_commands.autocomplete(name=base_autocomplete, player=player_autocomplete)
    @app_commands.describe(war_round="CWL round 1-7", player="Who used it")
    @manager()
    async def use(
        self,
        interaction: discord.Interaction,
        name: str,
        war_round: app_commands.Range[int, 1, 7] | None = None,
        player: str | None = None,
    ) -> None:
        base = await get_base_or_fail(interaction, name)
        tag = self.parse_tag(player) if player else None
        season = cwl_season_id()
        await repo.record_usage(self.db, base["id"], season, war_round, tag)
        uses = len(
            [
                r
                for r in await repo.usage_report(self.db, interaction.guild_id, season)
                if r["base_id"] == base["id"]
            ]
        )
        warn = " ⚠️ It had already been used this CWL." if uses > 1 else ""
        await interaction.response.send_message(
            f"Marked **{base['name']}** as used ({uses}x this CWL).{warn}"
        )

    @bases.command(name="unuse", description="Undo the latest usage of a base this CWL")
    @app_commands.autocomplete(name=base_autocomplete)
    @manager()
    async def unuse(
        self,
        interaction: discord.Interaction,
        name: str,
        war_round: app_commands.Range[int, 1, 7] | None = None,
    ) -> None:
        base = await get_base_or_fail(interaction, name)
        if not await repo.remove_usage(self.db, base["id"], cwl_season_id(), war_round):
            raise ResolveError(
                f"**{base['name']}** has no recorded usage this CWL"
                + (f" in round {war_round}." if war_round else ".")
            )
        await interaction.response.send_message(
            f"Removed the latest usage of **{base['name']}**.", ephemeral=True
        )

    @bases.command(name="list", description="List bases and how often each was used this CWL")
    @app_commands.choices(type=TYPE_CHOICES)
    async def list_(
        self,
        interaction: discord.Interaction,
        town_hall: app_commands.Range[int, 1, 20] | None = None,
        type: app_commands.Choice[str] | None = None,
        unused_only: bool = False,
    ) -> None:
        season = cwl_season_id()
        rows = await repo.list_bases_with_usage(
            self.db,
            interaction.guild_id,
            season,
            town_hall=town_hall,
            type=type.value if type else None,
            unused_only=unused_only,
        )
        lines = [
            f"TH{r['town_hall']} · **{r['name']}** · {r['type']} · "
            + (f"used {r['uses']}x" if r["uses"] else "unused")
            + (f" · [link]({r['link']})" if repo.is_copy_link(r["link"]) else "")
            for r in rows
        ]
        pages = chunk_lines(lines) if lines else ["No bases match. Add one with `/bases add`."]
        embed = await make_embed(
            self.bot, interaction.guild_id, title=f"War bases · CWL {season}", description=pages[0]
        )
        if len(pages) > 1:
            embed.set_footer(
                text=f"Showing {pages[0].count(chr(10))} of {len(lines)} — filter by TH to narrow"
            )
        await interaction.response.send_message(embed=embed)

    @bases.command(name="usage", description="Which bases were used in which CWL rounds, and by whom")
    async def usage(self, interaction: discord.Interaction) -> None:
        season = cwl_season_id()
        rows = await repo.usage_report(self.db, interaction.guild_id, season)
        lines = []
        for r in rows:
            rnd = f"R{r['war_round']}" if r["war_round"] else "R?"
            who = r["player_name"] or r["player_tag"] or "—"
            lines.append(f"`{rnd}` TH{r['town_hall']} **{r['base_name']}** · {who} · {ts(r['used_at'], 'd')}")
        embed = await make_embed(
            self.bot,
            interaction.guild_id,
            title=f"Base usage · CWL {season}",
            description=chunk_lines(lines)[0] if lines else "No bases used yet this CWL.",
        )
        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------ helpers

    async def _refresh_post(self, base_id: int) -> None:
        base = await repo.get_base(self.db, base_id)
        if base is None or not base["channel_id"] or not base["message_id"]:
            return
        channel = self.bot.get_channel(base["channel_id"])
        if channel is None:
            return
        color = (await make_embed(self.bot, base["guild_id"])).color
        try:
            await channel.get_partial_message(base["message_id"]).edit(
                embed=render_base_card(base, color), view=base_view(base)
            )
        except discord.NotFound:
            await repo.update_base(self.db, base_id, channel_id=None, message_id=None)
        except discord.HTTPException:
            pass


async def setup(bot) -> None:
    await bot.add_cog(Bases(bot))
