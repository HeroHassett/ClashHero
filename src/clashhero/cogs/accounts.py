"""Discord <-> Clash account links: /link, /verify, /profile, /whois.

Linking fires ``bot.dispatch("clashhero_account_linked", guild_id, user_id, player_tag)`` so
autoroles/nicknames update immediately, and posts the "welcome" server log.
"""

from __future__ import annotations

import discord
from discord import app_commands

from clashhero.cogs._base import BaseCog
from clashhero.services.outbox import send_server_log
from clashhero.utils.checks import has_permission, require
from clashhero.utils.fmt import chunk_lines, make_embed, player_link, role_name, ts
from clashhero.utils.resolve import (
    ResolveError,
    clan_autocomplete,
    parse_tag,
    player_autocomplete,
    resolve_clan,
)
from clashhero.utils.style import SEP, chip, header, ok_embed

MAX_ACCOUNTS = 25


def account_line(r, clan_names: dict[str, str]) -> str:
    marks = ("⭐ " if r["is_default"] else "") + ("✅ " if r["verified"] else "")
    name = r["name"] or "?"
    th = f"TH{r['town_hall']}" if r["town_hall"] else "TH?"
    clan = clan_names.get(r["clan_tag"] or "", r["clan_tag"] or "no clan")
    trophies = f"🏆 {r['trophies']:,}" if r["trophies"] is not None else None
    facts = SEP.join(x for x in (chip(th), trophies, clan) if x)
    return f"{marks}[{name}]({player_link(r['player_tag'])}) `{r['player_tag']}` · {facts}"


def account_label(name: str, tag: str, town_hall: int | None = None) -> str:
    """``**dev** `#20LGL8GV0` (TH18)`` for result messages."""
    th = f" (TH{town_hall})" if town_hall else ""
    return f"**{name}** `{tag}`{th}"


def link_list_lines(members, links: dict[str, int]) -> list[str]:
    """Clan members with their linked user under a "Linked" header; unlinked members last,
    under their own header."""
    linked, unlinked = [], []
    for m in members:
        uid = links.get(m["player_tag"])
        if uid:
            linked.append(f"{m['name']} `{m['player_tag']}` → <@{uid}>")
        else:
            unlinked.append(f"{m['name']} `{m['player_tag']}`")
    lines = []
    if linked:
        lines += [header("Linked", "✅", chip(len(linked))), *linked]
    if unlinked:
        lines += ([""] if linked else []) + [header("Not linked", "❌", chip(len(unlinked))), *unlinked]
    return lines


class Accounts(BaseCog):
    link = app_commands.Group(name="link", description="Link Clash accounts to Discord users")

    async def _clan_names(self) -> dict[str, str]:
        rows = await self.db.fetchall("SELECT clan_tag, name FROM clans")
        return {r["clan_tag"]: r["name"] for r in rows}

    async def _after_link(self, interaction: discord.Interaction, user: discord.abc.User, player):
        if interaction.guild_id is None:
            return
        self.bot.dispatch("clashhero_account_linked", interaction.guild_id, user.id, player.tag)
        embed = await make_embed(
            self.bot,
            interaction.guild_id,
            title="Account linked",
            description=f"{user.mention} linked **{player.name}** (`{player.tag}`)",
        )
        embed.add_field(name="Town Hall", value=str(player.town_hall))
        embed.add_field(name="Trophies", value=str(player.trophies))
        clan = getattr(player, "clan", None)
        embed.add_field(name="Clan", value=f"{clan.name} ({clan.tag})" if clan else "—")
        await send_server_log(self.bot, interaction.guild_id, "welcome", embed)

    # ------------------------------------------------------------------ /link

    @link.command(name="create", description="Link a Clash account (to you or, for managers, a user)")
    @app_commands.describe(user="Link to someone else (Links Manager)", is_default="Make it the default")
    async def link_create(
        self,
        interaction: discord.Interaction,
        player_tag: str,
        user: discord.User | None = None,
        is_default: bool = False,
    ) -> None:
        target = user or interaction.user
        tag = parse_tag(player_tag)
        is_manager = await has_permission(interaction, "links")
        if target.id != interaction.user.id and not is_manager:
            raise app_commands.CheckFailure(
                "You need Manage Server or the Links Manager role to link for others."
            )
        existing = await self.db.get_link(tag)
        if existing and existing["discord_user_id"] != target.id and existing["verified"] and not is_manager:
            raise ResolveError(
                "That account is verified by another user. Ask a Links Manager, or `/verify` it."
            )
        owned = await self.db.get_linked_tags(target.id)
        if tag not in owned and len(owned) >= MAX_ACCOUNTS:
            raise ResolveError(f"A user can link at most {MAX_ACCOUNTS} accounts.")
        await interaction.response.defer(ephemeral=True)
        player = await self.coc.get_player(tag)
        await self.db.link_player(player.tag, target.id)
        if is_default:
            await self.db.set_default_account(target.id, player.tag)
        moved = (
            f" (moved from <@{existing['discord_user_id']}>)"
            if existing and existing["discord_user_id"] != target.id
            else ""
        )
        await interaction.followup.send(
            embed=ok_embed(
                f"Linked {account_label(player.name, player.tag, player.town_hall)} "
                f"to {target.mention}{moved}."
            )
        )
        await self._after_link(interaction, target, player)

    @link.command(name="delete", description="Unlink a Clash account")
    @app_commands.autocomplete(player_tag=player_autocomplete)
    async def link_delete(self, interaction: discord.Interaction, player_tag: str) -> None:
        tag = parse_tag(player_tag)
        link = await self.db.get_link(tag)
        if link is None:
            raise ResolveError(f"`{tag}` isn't linked.")
        if link["discord_user_id"] != interaction.user.id:
            await require(interaction, "links")
        player = await self.db.get_player_row(tag)
        await self.db.unlink_player(tag)
        label = account_label(player["name"], tag) if player and player["name"] else f"`{tag}`"
        await interaction.response.send_message(
            embed=ok_embed(f"Unlinked {label} from <@{link['discord_user_id']}>."), ephemeral=True
        )

    @link.command(name="default", description="Choose your default account")
    @app_commands.autocomplete(player_tag=player_autocomplete)
    async def link_default(self, interaction: discord.Interaction, player_tag: str) -> None:
        tag = parse_tag(player_tag)
        if tag not in await self.db.get_linked_tags(interaction.user.id):
            raise ResolveError(f"`{tag}` isn't linked to you.")
        await self.db.set_default_account(interaction.user.id, tag)
        player = await self.db.get_player_row(tag)
        label = (
            account_label(player["name"], tag, player["town_hall"])
            if player and player["name"]
            else f"`{tag}`"
        )
        await interaction.response.send_message(
            embed=ok_embed(f"{label} is now your default account."), ephemeral=True
        )

    @link.command(name="list", description="Show which clan members are linked")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def link_list(self, interaction: discord.Interaction, clan: str | None = None) -> None:
        tag = await resolve_clan(interaction, clan)
        members = await self.db.clan_member_rows(tag)
        links = await self.db.links_for_tags(m["player_tag"] for m in members)
        clan_row = await self.db.get_clan_row(tag)
        title = f"Links · {clan_row['name'] if clan_row else tag}"
        lines = link_list_lines(members, links)
        if members:
            lines.insert(0, f"🔗 {chip(f'{len(links)}/{len(members)}')} linked\n")
        embeds = []
        for i, chunk in enumerate(chunk_lines(lines or ["No members known yet."])):
            e = await make_embed(self.bot, interaction.guild_id, title=title if i == 0 else None)
            e.description = chunk
            embeds.append(e)
        await interaction.response.send_message(embeds=embeds[:10], ephemeral=True)

    # ------------------------------------------------------------------ /verify

    @app_commands.command(name="verify", description="Verify you own an account with its API token")
    @app_commands.describe(
        player_tag="Your player tag",
        token="In-game: Settings → More Settings → API Token (one-time)",
    )
    async def verify(self, interaction: discord.Interaction, player_tag: str, token: str) -> None:
        tag = parse_tag(player_tag)
        await interaction.response.defer(ephemeral=True)
        ok = await self.coc.verify_player_token(tag, token.strip())
        if not ok:
            raise ResolveError("That token is invalid or expired. Generate a fresh one in-game.")
        player = await self.coc.get_player(tag)
        await self.db.link_player(player.tag, interaction.user.id, verified=True)
        await interaction.followup.send(
            embed=ok_embed(
                f"Account verified. You control {account_label(player.name, player.tag, player.town_hall)}"
            )
        )
        await self._after_link(interaction, interaction.user, player)

    # ------------------------------------------------------------------ /profile, /whois

    @app_commands.command(name="profile", description="Show a user's linked accounts")
    async def profile(self, interaction: discord.Interaction, user: discord.User | None = None) -> None:
        target = user or interaction.user
        rows = await self.db.get_links(target.id)
        names = await self._clan_names()
        embed = await make_embed(self.bot, interaction.guild_id, title=f"{target.display_name}'s accounts")
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.description = ("\n".join(account_line(r, names) for r in rows) or "No linked accounts.")[:4096]
        embed.set_footer(text="⭐ default · ✅ verified")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="whois", description="Who owns a Clash account, plus its name history")
    @app_commands.autocomplete(player_tag=player_autocomplete)
    async def whois(self, interaction: discord.Interaction, player_tag: str) -> None:
        tag = parse_tag(player_tag)
        link = await self.db.get_link(tag)
        player = await self.db.get_player_row(tag)
        history = await self.db.name_history(tag)
        name = player["name"] if player else tag
        embed = await make_embed(
            self.bot, interaction.guild_id, title=f"{name} ({tag})", url=player_link(tag)
        )
        if link:
            embed.add_field(
                name="Linked to",
                value=f"<@{link['discord_user_id']}> {'✅ verified' if link['verified'] else ''}\n"
                f"since {ts(link['linked_at'], 'D')}",
                inline=False,
            )
        else:
            embed.add_field(name="Linked to", value="Nobody", inline=False)
        if player:
            embed.add_field(name="Town Hall", value=str(player["town_hall"] or "?"))
            embed.add_field(name="Clan", value=player["clan_tag"] or "—")
            embed.add_field(name="Role", value=role_name(player["clan_role"]))
        if history:
            embed.add_field(
                name="Name history",
                value="\n".join(f"{h['name']} · {ts(h['first_seen'], 'd')}" for h in history[-15:]),
                inline=False,
            )
        await interaction.response.send_message(embed=embed)


async def setup(bot) -> None:
    await bot.add_cog(Accounts(bot))
