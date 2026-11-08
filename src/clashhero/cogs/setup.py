"""Server setup: family clans, aliases, categories, manager roles, embed color, /debug."""

from __future__ import annotations

import sqlite3

import discord
from discord import app_commands

from clashhero.cogs._base import BaseCog
from clashhero.utils.checks import has_permission, manager
from clashhero.utils.fmt import make_embed
from clashhero.utils.resolve import ResolveError, clan_autocomplete, parse_tag, resolve_clan

ROLE_OPTIONS = {
    "bot_manager": "bot_manager_role",
    "roster_manager": "roster_manager_role",
    "links_manager": "links_manager_role",
    "flags_manager": "flags_manager_role",
}

CHANNEL_PERMS = (
    "view_channel",
    "send_messages",
    "embed_links",
    "attach_files",
    "read_message_history",
    "add_reactions",
    "use_external_emojis",
    "mention_everyone",
)
GUILD_PERMS = ("manage_roles", "manage_nicknames", "create_events", "manage_events")


def parse_hex_color(raw: str) -> int:
    value = raw.strip().lstrip("#")
    if value.lower().startswith("0x"):
        value = value[2:]
    if len(value) != 6:
        raise ValueError(raw)
    return int(value, 16)


async def is_clan_leader(bot, user_id: int, clan_tag: str) -> bool:
    """True if one of the user's *verified* linked accounts is Leader/Co-Leader of the clan."""
    links = await bot.db.get_links(user_id)
    verified = [r["player_tag"] for r in links if r["verified"]]
    if not verified:
        return False
    clan = await bot.coc.get_clan(clan_tag)
    for tag in verified:
        member = clan.get_member(tag)
        role = getattr(getattr(member, "role", None), "value", None)
        if role in ("leader", "coLeader"):
            return True
    return False


class Setup(BaseCog):
    setup_group = app_commands.Group(name="setup", description="Link clans to this server", guild_only=True)
    alias = app_commands.Group(name="alias", description="Short clan names", guild_only=True)
    category = app_commands.Group(name="category", description="Group clans into categories", guild_only=True)
    config = app_commands.Group(name="config", description="Bot configuration", guild_only=True)

    # ------------------------------------------------------------------ /setup

    @setup_group.command(name="clan", description="Link a clan to this server")
    @app_commands.describe(clan_tag="Clan tag", channel="Optionally make this the default clan for a channel")
    async def setup_clan(
        self,
        interaction: discord.Interaction,
        clan_tag: str,
        channel: discord.TextChannel | None = None,
    ) -> None:
        tag = parse_tag(clan_tag)
        await interaction.response.defer(ephemeral=True)
        clan = await self.coc.get_clan(tag)
        if not await has_permission(interaction, "bot") and not await is_clan_leader(
            self.bot, interaction.user.id, clan.tag
        ):
            raise app_commands.CheckFailure(
                "You need Manage Server, the Bot Manager role, or a verified Leader/Co-Leader "
                "account in that clan (`/verify`)."
            )
        await self.db.add_guild_clan(interaction.guild_id, clan.tag)
        if channel is not None:
            await self.db.set_channel_clan(channel.id, interaction.guild_id, clan.tag)
        await self.collector.refresh_clan_now(clan.tag)
        extra = f" Default clan for {channel.mention}." if channel else ""
        await interaction.followup.send(f"Linked **{clan.name}** (`{clan.tag}`).{extra}")

    @setup_group.command(name="remove-clan", description="Unlink a clan and all its features")
    @app_commands.autocomplete(clan=clan_autocomplete)
    @manager()
    async def setup_remove(self, interaction: discord.Interaction, clan: str) -> None:
        tag = await resolve_clan(interaction, clan)
        await self.db.remove_guild_clan(interaction.guild_id, tag)
        await interaction.response.send_message(
            f"Unlinked `{tag}` and removed its logs and live messages.", ephemeral=True
        )

    @setup_group.command(name="channel", description="Set or clear a channel's default clan")
    @app_commands.describe(clan="Leave empty to clear", channel="Defaults to this channel")
    @app_commands.autocomplete(clan=clan_autocomplete)
    @manager()
    async def setup_channel(
        self,
        interaction: discord.Interaction,
        clan: str | None = None,
        channel: discord.TextChannel | None = None,
    ) -> None:
        target = channel or interaction.channel
        if clan is None:
            await self.db.set_channel_clan(target.id, interaction.guild_id, None)
            msg = f"Cleared the default clan for {target.mention}."
        else:
            tag = await resolve_clan(interaction, clan)
            await self.db.set_channel_clan(target.id, interaction.guild_id, tag)
            msg = f"`{tag}` is now the default clan for {target.mention}."
        await interaction.response.send_message(msg, ephemeral=True)

    @setup_group.command(name="list", description="Show linked clans and enabled features")
    async def setup_list(self, interaction: discord.Interaction) -> None:
        gid = interaction.guild_id
        rows = await self.db.get_guild_clan_rows(gid)
        logs = await self.db.get_clan_logs(gid)
        live = await self.db.fetchall("SELECT * FROM live_messages WHERE guild_id = ?", (gid,))
        channels = await self.db.fetchall("SELECT * FROM channel_clans WHERE guild_id = ?", (gid,))
        server_logs = await self.db.fetchall("SELECT * FROM server_logs WHERE guild_id = ?", (gid,))
        embed = await make_embed(self.bot, gid, title="Server setup")
        if not rows:
            embed.description = "No clans linked yet. Use `/setup clan`."
        for r in rows[:20]:
            tag = r["clan_tag"]
            clan_logs = sorted({lg["log_type"] for lg in logs if lg["clan_tag"] == tag})
            clan_live = sorted({lv["kind"] for lv in live if lv["scope"] == tag})
            chans = [f"<#{c['channel_id']}>" for c in channels if c["clan_tag"] == tag]
            lines = [
                f"Alias: `{r['alias']}`" if r["alias"] else None,
                f"Category: {r['category']}" if r["category"] else None,
                f"Default in: {', '.join(chans)}" if chans else None,
                f"Logs: {', '.join(clan_logs)}" if clan_logs else "Logs: none",
                f"Live: {', '.join(clan_live)}" if clan_live else None,
            ]
            embed.add_field(
                name=f"{r['name'] or '?'} ({tag})",
                value="\n".join(x for x in lines if x)[:1024],
                inline=False,
            )
        if server_logs:
            embed.add_field(
                name="Server logs",
                value="\n".join(f"{s['log_type']}: <#{s['channel_id']}>" for s in server_logs),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ /alias

    @alias.command(name="set", description="Give a clan a short alias (e.g. AH2)")
    @app_commands.autocomplete(clan=clan_autocomplete)
    @manager()
    async def alias_set(self, interaction: discord.Interaction, clan: str, alias: str) -> None:
        tag = await resolve_clan(interaction, clan)
        alias = alias.strip()
        if not alias or " " in alias or alias.startswith("#") or len(alias) > 15:
            raise ResolveError("Aliases must be 1–15 characters, no spaces, not starting with #.")
        try:
            await self.db.set_clan_alias(interaction.guild_id, tag, alias)
        except sqlite3.IntegrityError as e:
            raise ResolveError(f"Alias `{alias}` is already used by another clan.") from e
        await interaction.response.send_message(f"`{tag}` → alias `{alias}`.", ephemeral=True)

    @alias.command(name="remove", description="Remove a clan's alias")
    @app_commands.autocomplete(clan=clan_autocomplete)
    @manager()
    async def alias_remove(self, interaction: discord.Interaction, clan: str) -> None:
        tag = await resolve_clan(interaction, clan)
        await self.db.set_clan_alias(interaction.guild_id, tag, None)
        await interaction.response.send_message(f"Removed alias for `{tag}`.", ephemeral=True)

    @alias.command(name="list", description="List clan aliases")
    async def alias_list(self, interaction: discord.Interaction) -> None:
        rows = await self.db.get_guild_clan_rows(interaction.guild_id)
        lines = [f"`{r['alias']}` → {r['name'] or '?'} (`{r['clan_tag']}`)" for r in rows if r["alias"]]
        await interaction.response.send_message(
            "\n".join(lines) or "No aliases set. Use `/alias set`.", ephemeral=True
        )

    # ------------------------------------------------------------------ /category

    @category.command(name="set", description="Put a clan in a category (leave empty to clear)")
    @app_commands.describe(order="Sort order within the category (lower first)")
    @app_commands.autocomplete(clan=clan_autocomplete)
    @manager()
    async def category_set(
        self,
        interaction: discord.Interaction,
        clan: str,
        category: str | None = None,
        order: int = 0,
    ) -> None:
        tag = await resolve_clan(interaction, clan)
        await self.db.set_clan_category(
            interaction.guild_id, tag, category.strip() if category else None, order
        )
        msg = f"`{tag}` → **{category}**" if category else f"Cleared category for `{tag}`"
        await interaction.response.send_message(msg + ".", ephemeral=True)

    @category.command(name="list", description="Show clans grouped by category")
    async def category_list(self, interaction: discord.Interaction) -> None:
        rows = await self.db.get_guild_clan_rows(interaction.guild_id)
        groups: dict[str, list[str]] = {}
        for r in rows:
            groups.setdefault(r["category"] or "Uncategorized", []).append(
                f"{r['name'] or '?'} (`{r['clan_tag']}`)"
            )
        embed = await make_embed(self.bot, interaction.guild_id, title="Clan categories")
        for name, clans in groups.items():
            embed.add_field(name=name, value="\n".join(clans)[:1024], inline=False)
        if not groups:
            embed.description = "No clans linked yet."
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ /config

    @config.command(name="roles", description="Set manager roles (omit to keep; use clear to unset)")
    @app_commands.choices(
        clear=[app_commands.Choice(name=k.replace("_", " "), value=k) for k in ROLE_OPTIONS]
    )
    @manager()
    async def config_roles(
        self,
        interaction: discord.Interaction,
        bot_manager: discord.Role | None = None,
        roster_manager: discord.Role | None = None,
        links_manager: discord.Role | None = None,
        flags_manager: discord.Role | None = None,
        clear: app_commands.Choice[str] | None = None,
    ) -> None:
        gid = interaction.guild_id
        given = {
            "bot_manager": bot_manager,
            "roster_manager": roster_manager,
            "links_manager": links_manager,
            "flags_manager": flags_manager,
        }
        changed = []
        for opt, role in given.items():
            if role is not None:
                await self.db.set_guild_setting(gid, ROLE_OPTIONS[opt], role.id)
                changed.append(f"{opt.replace('_', ' ')} → {role.mention}")
        if clear is not None:
            await self.db.set_guild_setting(gid, ROLE_OPTIONS[clear.value], None)
            changed.append(f"{clear.name} cleared")
        await interaction.response.send_message("\n".join(changed) or "Nothing changed.", ephemeral=True)

    @config.command(name="color", description="Set the embed color (hex, e.g. #FF8800)")
    @manager()
    async def config_color(self, interaction: discord.Interaction, color: str) -> None:
        try:
            value = parse_hex_color(color)
        except ValueError as e:
            raise ResolveError("Use a hex color like `#FF8800`.") from e
        await self.db.set_guild_setting(interaction.guild_id, "embed_color", value)
        embed = discord.Embed(description="Embed color updated.", color=value)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @config.command(name="show", description="Show current bot configuration")
    async def config_show(self, interaction: discord.Interaction) -> None:
        s = await self.db.get_guild_settings(interaction.guild_id)
        embed = await make_embed(self.bot, interaction.guild_id, title="Configuration")
        for opt, col in ROLE_OPTIONS.items():
            embed.add_field(
                name=opt.replace("_", " ").title(),
                value=f"<@&{s[col]}>" if s[col] else "—",
            )
        color = s["embed_color"]
        embed.add_field(name="Embed color", value=f"#{color:06X}" if color is not None else "default")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ /debug

    @app_commands.command(name="debug", description="Check the bot's permissions in this channel")
    @app_commands.guild_only()
    async def debug(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        me = guild.me
        channel = interaction.channel
        chan_perms = channel.permissions_for(me)
        missing_chan = [p for p in CHANNEL_PERMS if not getattr(chan_perms, p)]
        missing_guild = [p for p in GUILD_PERMS if not getattr(me.guild_permissions, p)]
        embed = await make_embed(self.bot, guild.id, title="Permission check")
        embed.add_field(
            name=f"In #{getattr(channel, 'name', '?')}",
            value=", ".join(missing_chan) if missing_chan else "All good ✅",
            inline=False,
        )
        embed.add_field(
            name="Server-wide (autoroles, nicknames, events)",
            value=", ".join(missing_guild) if missing_guild else "All good ✅",
            inline=False,
        )
        embed.add_field(name="Top role", value=me.top_role.mention, inline=False)
        embed.set_footer(text="The bot can only manage roles below its own top role.")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Setup(bot))
