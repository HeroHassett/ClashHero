"""Discord <-> Clash account links: /link, /verify, /profile, /whois.

Linking fires ``bot.dispatch("clashhero_account_linked", guild_id, user_id, player_tag)`` so
autoroles/nicknames update immediately, and posts the "welcome" server log.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import discord
from discord import app_commands
from discord.utils import escape_markdown

from clashhero.cogs._base import BaseCog
from clashhero.cogs._info_embeds import fit
from clashhero.cogs._log_embeds import player_ref, th_icon
from clashhero.cogs.info import badge_of, clan_heading
from clashhero.services.emojis import EMOJIS
from clashhero.services.outbox import send_server_log
from clashhero.utils.checks import has_permission, require
from clashhero.utils.fmt import chunk_lines, make_embed, player_link, role_name, ts
from clashhero.utils.ranked import league_name
from clashhero.utils.resolve import (
    ResolveError,
    clan_autocomplete,
    parse_tag,
    player_autocomplete,
    resolve_clan,
)
from clashhero.utils.style import SEP, header, ok_embed

MAX_ACCOUNTS = 25


def _field(row: Any, key: str) -> Any:
    """``row[key]`` for a dict or sqlite Row, None when it has no such column."""
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def town_hall_stat(level: int | None) -> str:
    """``<th18>**18**`` (``TH **18**`` until the icon is uploaded); "" when unknown."""
    if not level:
        return ""
    icon = EMOJIS.town_hall(level)
    return f"{icon}**{level}**" if icon else f"TH **{level}**"


def trophy_stat(trophies: int | None, league: str | None = None) -> str:
    """Trophies in bold beside the league's icon (the trophy icon when it has none)."""
    if trophies is None:
        return ""
    return f"{EMOJIS.league(league) or EMOJIS.get('res_trophy', '🏆')}**{trophies:,}**"


def account_line(r, clan_names: dict[str, str]) -> str:
    """/profile: ⭐ default and ✅ verified, then the Town Hall's icon, the name linked to the
    in-game profile with its tag, trophies and clan."""
    marks = ("⭐ " if r["is_default"] else "") + ("✅ " if r["verified"] else "")
    th = th_icon(r["town_hall"]) or "TH?"
    name = f"**{player_ref(r['name'] or '?', r['player_tag'])}** ({r['player_tag']})"
    clan = escape_markdown(clan_names.get(r["clan_tag"] or "", r["clan_tag"] or "no clan"))
    facts = SEP.join(x for x in (trophy_stat(r["trophies"]), clan) if x)
    return f"{marks}{th} {name} · {facts}"


def account_label(name: str, tag: str, town_hall: int | None = None) -> str:
    """``<th18> **[dev](link)** (#20LGL8GV0)`` for result messages: the Town Hall's icon (TH18
    until it's uploaded), then the name linked to the in-game profile."""
    return " ".join(x for x in (th_icon(town_hall), f"**{player_ref(name, tag)}** ({tag})") if x)


def link_list_lines(members, links: dict[str, int]) -> list[str]:
    """Clan members (Town Hall icon, name and tag) with their linked user under a "Linked"
    header; unlinked members last, under their own header."""
    linked, unlinked = [], []
    for m in members:
        who = " ".join(
            x
            for x in (th_icon(_field(m, "town_hall")), f"{escape_markdown(m['name'])} ({m['player_tag']})")
            if x
        )
        uid = links.get(m["player_tag"])
        if uid:
            linked.append(f"{who} → <@{uid}>")
        else:
            unlinked.append(who)
    lines = []
    if linked:
        lines += [header("Linked", "✅", f"({len(linked)})"), *linked]
    if unlinked:
        lines += ([""] if linked else []) + [header("Not linked", "❌", f"({len(unlinked)})"), *unlinked]
    return lines


def whois_fields(link: Any, player: Any, history: Any) -> list[tuple[str, str, bool]]:
    """(name, value, inline) fields for /whois: who the account is linked to and since when; its
    Town Hall, trophies (with their game icons) and clan role; and its earlier names."""
    if link:
        verified = " ✅ verified" if link["verified"] else ""
        since = ts(link["linked_at"], "D")
        fields = [("Linked to", f"<@{link['discord_user_id']}>{verified}\nsince {since}", False)]
    else:
        fields = [("Linked to", "Nobody", False)]
    if player:
        fields += [
            ("Town Hall", town_hall_stat(player["town_hall"]) or "?", True),
            ("Trophies", trophy_stat(player["trophies"], _field(player, "league")) or "—", True),
            ("Role", role_name(player["clan_role"]), True),
        ]
    if history:
        names = [f"{escape_markdown(h['name'])} · {ts(h['first_seen'], 'd')}" for h in history[-15:]]
        fields.append(("Name history", fit("\n".join(names), 1024, "\n"), False))
    return fields


def linked_post(player: Any, user_mention: str) -> tuple[str, list[str]]:
    """(title, description lines) for the "Account linked" server log: the player as "Name (#TAG)",
    who linked it, and their Town Hall and trophies with their game icons."""
    stats = " ".join(
        x for x in (town_hall_stat(player.town_hall), trophy_stat(player.trophies, league_name(player))) if x
    )
    return f"{escape_markdown(player.name)} ({player.tag})", [f"🔗 Linked to {user_mention}", stats]


class Accounts(BaseCog):
    link = app_commands.Group(name="link", description="Link Clash accounts to Discord users")

    async def _clan_names(self) -> dict[str, str]:
        rows = await self.db.fetchall("SELECT clan_tag, name FROM clans")
        return {r["clan_tag"]: r["name"] for r in rows}

    async def _after_link(self, interaction: discord.Interaction, user: discord.abc.User, player):
        if interaction.guild_id is None:
            return
        self.bot.dispatch("clashhero_account_linked", interaction.guild_id, user.id, player.tag)
        title, lines = linked_post(player, user.mention)
        embed = await make_embed(
            self.bot,
            interaction.guild_id,
            title=title,
            url=player_link(player.tag),
            description="\n".join(x for x in lines if x),
        )
        clan = getattr(player, "clan", None)
        # The player's clan with its badge in the footer, as on the clan log's player posts.
        embed.set_footer(
            text=clan.name if clan else "Not in a clan", icon_url=badge_of(clan) if clan else None
        )
        embed.timestamp = datetime.now(UTC)
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
        lines = link_list_lines(members, links)
        if members:
            lines.insert(0, f"🔗 **{len(links)}**/{len(members)} linked\n")
        embeds = []
        for i, chunk in enumerate(chunk_lines(lines or ["No members known yet."])):
            e = await make_embed(self.bot, interaction.guild_id, title="Linked accounts" if i == 0 else None)
            e.description = chunk
            if i == 0:  # the clan with its badge above the list
                clan_heading(
                    e,
                    clan_row["name"] if clan_row else None,
                    tag,
                    clan_row["badge_url"] if clan_row else None,
                )
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
        lines = [account_line(r, names) for r in rows]
        embed.description = fit("\n".join(lines), 4096, "\n") if lines else "No linked accounts."
        embed.set_footer(text="⭐ default · ✅ verified")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="whois", description="Who owns a Clash account, plus its name history")
    @app_commands.autocomplete(player_tag=player_autocomplete)
    async def whois(self, interaction: discord.Interaction, player_tag: str) -> None:
        tag = parse_tag(player_tag)
        link = await self.db.get_link(tag)
        player = await self.db.get_player_row(tag)
        history = await self.db.name_history(tag)
        name = escape_markdown(player["name"]) if player else tag
        embed = await make_embed(
            self.bot, interaction.guild_id, title=f"{name} ({tag})", url=player_link(tag)
        )
        for field_name, value, inline in whois_fields(link, player, history):
            embed.add_field(name=field_name, value=value, inline=inline)
        clan_tag = player["clan_tag"] if player else None
        if clan_tag:  # the player's clan with its badge above their name
            clan = await self.db.get_clan_row(clan_tag)
            clan_heading(embed, clan["name"] if clan else None, clan_tag, clan["badge_url"] if clan else None)
        await interaction.response.send_message(embed=embed)


async def setup(bot) -> None:
    await bot.add_cog(Accounts(bot))
