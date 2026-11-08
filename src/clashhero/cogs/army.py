"""Army link parsing (/army) and the command overview (/help)."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from clashhero.cogs._base import BaseCog
from clashhero.cogs._info_embeds import ArmySummary, army_link, army_param, summarize_army
from clashhero.constants import SUPERCELL_DISCLAIMER
from clashhero.utils.fmt import chunk_lines, make_embed


def army_embed_fields(summary: ArmySummary) -> list[tuple[str, str]]:
    fields = []
    if summary.heroes:
        lines = []
        for hero, pet, eq in summary.heroes:
            extra = [x for x in [pet, *eq] if x]
            lines.append(f"**{hero}**" + (f" · {', '.join(extra)}" if extra else ""))
        fields.append(("Heroes", "\n".join(lines)))
    if summary.troops:
        fields.append(
            (
                f"Troops ({summary.troop_space} space)",
                "\n".join(f"`{n:>3}x` {name}" for name, n, _ in summary.troops),
            )
        )
    if summary.spells:
        fields.append(
            (
                f"Spells ({summary.spell_space} space)",
                "\n".join(f"`{n:>3}x` {name}" for name, n, _ in summary.spells),
            )
        )
    cc = [f"`{n:>3}x` {name}" for name, n in summary.cc_troops + summary.cc_spells]
    if cc:
        fields.append(("Clan Castle", "\n".join(cc)))
    return fields


class Army(BaseCog):
    """Army links and help."""

    army = app_commands.Group(name="army", description="Army link tools")

    @army.command(name="link", description="Show the troops, spells, heroes and CC in an army link")
    @app_commands.describe(link="In-game 'share army' link", name="Optional name for this army")
    async def army_link_cmd(
        self, interaction: discord.Interaction, link: str, name: str | None = None
    ) -> None:
        try:
            param = army_param(link)
        except ValueError as e:
            raise app_commands.AppCommandError(str(e)) from e
        if not getattr(self.coc, "_static_data", None):
            self.coc._load_static()  # static game data is normally loaded at login
        try:
            recipe = self.coc.parse_army_link(param)
        except Exception as e:
            raise app_commands.AppCommandError("Couldn't parse that army link.") from e
        summary = summarize_army(recipe)
        if not (summary.troops or summary.spells or summary.heroes or summary.cc_troops):
            raise app_commands.AppCommandError("That army link is empty or unrecognised.")
        e = await make_embed(self.bot, interaction.guild_id, title=name or "Army")
        for title, value in army_embed_fields(summary):
            for i, chunk in enumerate(chunk_lines(value.split("\n"), 1000)):
                e.add_field(name=title if i == 0 else f"{title} (cont.)", value=chunk, inline=True)
        view = discord.ui.View()
        view.add_item(
            discord.ui.Button(label="Copy army in game", style=discord.ButtonStyle.link, url=army_link(param))
        )
        await interaction.response.send_message(embed=e, view=view)

    # ------------------------------------------------------------------ help

    @app_commands.command(name="help", description="Overview of every ClashHero command")
    async def help(self, interaction: discord.Interaction) -> None:
        categories = command_catalog(self.bot)
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title="ClashHero help",
            description="Pick a category below to see its commands.\n\n"
            + "\n".join(f"**{cat}** · {len(cmds)} commands" for cat, cmds in categories.items()),
        )
        e.set_footer(text=SUPERCELL_DISCLAIMER)
        await interaction.response.send_message(embed=e, view=HelpView(self.bot, categories), ephemeral=True)


def command_catalog(bot: commands.Bot) -> dict[str, list[tuple[str, str]]]:
    """{cog name: [(qualified command name, description)]} for every loaded app command."""
    out: dict[str, list[tuple[str, str]]] = {}
    for cog_name, cog in sorted(bot.cogs.items()):
        cmds = []
        for cmd in cog.walk_app_commands():
            if isinstance(cmd, app_commands.Group):
                continue
            cmds.append((f"/{cmd.qualified_name}", cmd.description))
        if cmds:
            out[cog_name] = sorted(cmds)
    return out


class HelpView(discord.ui.View):
    def __init__(self, bot: commands.Bot, categories: dict[str, list[tuple[str, str]]]) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.categories = categories
        select = discord.ui.Select(
            placeholder="Choose a category",
            options=[
                discord.SelectOption(label=cat[:100], description=f"{len(c)} commands")
                for cat, c in list(categories.items())[:25]
            ],
        )
        select.callback = self.on_select
        self.add_item(select)
        self.select = select

    async def on_select(self, interaction: discord.Interaction) -> None:
        cat = self.select.values[0]
        lines = [f"`{name}` · {desc}" for name, desc in self.categories.get(cat, [])]
        e = await make_embed(
            self.bot, interaction.guild_id, title=f"Help · {cat}", description=chunk_lines(lines, 4000)[0]
        )
        await interaction.response.edit_message(embed=e, view=self)


async def setup(bot) -> None:
    await bot.add_cog(Army(bot))
