"""Regular war views, war history, target calling and hit/defense rate stats.

/war current|lineup|remaining|log|history, /caller assign|clear|list, /stats attacks|defense
"""

from __future__ import annotations

import discord
from discord import app_commands

from clashhero.cogs._base import BaseCog
from clashhero.cogs._war_embeds import (
    PERIOD_CHOICES,
    WAR_TYPE_CHOICES,
    attack_rank_table,
    attack_totals,
    calls_embed,
    defense_rank_table,
    defense_totals,
    history_embed,
    lineup_embed,
    period_bounds,
    plural,
    remaining_embed,
    resolve_player_tags,
    set_clan,
    side_badge,
    state_value,
    stats_footer,
    war_log_embed,
    war_overview_embed,
    war_sides,
)
from clashhero.repos import war as war_repo
from clashhero.utils.checks import has_permission, require
from clashhero.utils.fmt import guild_color, make_embed
from clashhero.utils.resolve import (
    ResolveError,
    clan_autocomplete,
    player_autocomplete,
    resolve_clan,
    resolve_clans,
    resolve_player,
)


class War(BaseCog):
    war = app_commands.Group(name="war", description="Current war, lineups and war history")
    caller = app_commands.Group(
        name="caller", description="Claim enemy bases in the current war", guild_only=True
    )
    stats = app_commands.Group(
        name="stats", description="War hit rates and defense hold rates", guild_only=True
    )

    async def _live_war(self, clan_tag: str):
        w = await self.coc.get_current_war(clan_tag)
        if w is None or state_value(w) == "notInWar":
            raise ResolveError(f"`{clan_tag}` isn't in a war right now.")
        return w

    # ------------------------------------------------------------------ /war

    @war.command(name="current", description="Score, timers and TH matchup of the current war")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def current(self, interaction: discord.Interaction, clan: str | None = None) -> None:
        tag = await resolve_clan(interaction, clan, must_be_tracked=False)
        await interaction.response.defer()
        w = await self._live_war(tag)
        color = await guild_color(self.bot, interaction.guild_id)
        await interaction.followup.send(embed=war_overview_embed(w, tag, color))

    @war.command(name="lineup", description="Both lineups by map position with stars on each base")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def lineup(self, interaction: discord.Interaction, clan: str | None = None) -> None:
        tag = await resolve_clan(interaction, clan, must_be_tracked=False)
        await interaction.response.defer()
        w = await self._live_war(tag)
        color = await guild_color(self.bot, interaction.guild_id)
        await interaction.followup.send(embed=lineup_embed(w, tag, color))

    @war.command(name="remaining", description="Members who still have war attacks left")
    @app_commands.describe(mention="Ping the linked Discord users")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def remaining(
        self, interaction: discord.Interaction, clan: str | None = None, mention: bool = False
    ) -> None:
        tag = await resolve_clan(interaction, clan, must_be_tracked=False)
        await interaction.response.defer()
        w = await self._live_war(tag)
        ours, _ = war_sides(w, tag)
        per = getattr(w, "attacks_per_member", None) or (1 if w.is_cwl else 2)
        members = [
            {"tag": m.tag, "name": m.name, "map_position": m.map_position, "town_hall": m.town_hall}
            for m in ours.members
        ]
        attacks = [{"attacker_tag": a.attacker_tag} for a in (ours.attacks or [])]
        left = war_repo.remaining_attacks(members, attacks, per)
        links = await self.db.links_for_tags([m["tag"] for m, _ in left]) if mention else {}
        e = await make_embed(self.bot, interaction.guild_id, title="Remaining attacks")
        remaining_embed(e, w, tag, left, links)
        # Mentions inside an embed never notify anyone, so the pings go in the message itself.
        pings = " ".join(dict.fromkeys(f"<@{links[m['tag']]}>" for m, _ in left if m["tag"] in links))
        await interaction.followup.send(
            content=pings[:2000] or None,
            embed=e,
            allowed_mentions=discord.AllowedMentions(users=mention),
        )

    @war.command(name="log", description="Recent war results from the in-game war log")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def log(
        self,
        interaction: discord.Interaction,
        clan: str | None = None,
        count: app_commands.Range[int, 1, 25] = 10,
    ) -> None:
        tag = await resolve_clan(interaction, clan, must_be_tracked=False)
        await interaction.response.defer()
        entries = list(await self.coc.get_war_log(tag, limit=count))[:count]
        clan_row = await self.db.get_clan_row(tag)
        ours = entries[0].clan if entries else None  # the log's own clan
        name = (clan_row["name"] if clan_row else None) or getattr(ours, "name", None) or tag
        badge = (clan_row["badge_url"] if clan_row else None) or side_badge(ours)
        e = await make_embed(self.bot, interaction.guild_id, title="War log")
        war_log_embed(e, entries)
        await interaction.followup.send(embed=set_clan(e, name, tag, badge))

    @war.command(name="history", description="A player's war performance (hit rate, 3★%, defense)")
    @app_commands.describe(
        player="Player tag (default: your linked accounts)", user="A Discord user's accounts"
    )
    @app_commands.choices(period=PERIOD_CHOICES, war_type=WAR_TYPE_CHOICES)
    @app_commands.autocomplete(player=player_autocomplete)
    async def history(
        self,
        interaction: discord.Interaction,
        player: str | None = None,
        user: discord.User | None = None,
        period: app_commands.Choice[str] | None = None,
        war_type: app_commands.Choice[str] | None = None,
    ) -> None:
        tags = await resolve_player_tags(interaction, player, user)
        since, _, label = period_bounds(period.value if period else "90d")
        h = await war_repo.player_war_history(
            self.db, tags, since=since, war_type=war_type.value if war_type else None
        )
        names = await war_repo.player_names(self.db, tags)
        name = (
            names.get(tags[0], tags[0])
            if len(tags) == 1
            else f"{(user or interaction.user).display_name} ({len(tags)} accounts)"
        )
        color = await guild_color(self.bot, interaction.guild_id)
        tag = tags[0] if len(tags) == 1 else None
        await interaction.response.send_message(embed=history_embed(name, label, h, color, tag=tag))

    # ------------------------------------------------------------------ /caller

    async def _caller_war(self, interaction: discord.Interaction, clan: str | None) -> dict:
        tag = await resolve_clan(interaction, clan)
        w = await war_repo.current_war(self.db, tag)
        if w is None or w["state"] == "warEnded":
            raise ResolveError(
                "No active war found for that clan yet (it may take a couple of minutes to appear)."
            )
        return w

    @caller.command(name="assign", description="Claim an enemy base in the current war")
    @app_commands.describe(
        position="Enemy map position", player="Your attacking account (default: your main)"
    )
    @app_commands.autocomplete(clan=clan_autocomplete, player=player_autocomplete)
    async def caller_assign(
        self,
        interaction: discord.Interaction,
        position: app_commands.Range[int, 1, 50],
        player: str | None = None,
        note: str | None = None,
        clan: str | None = None,
    ) -> None:
        w = await self._caller_war(interaction, clan)
        if position > (w["team_size"] or 50):
            raise ResolveError(f"This war only has {w['team_size']} bases.")
        if player:
            tag = await resolve_player(interaction, player)
        else:
            linked = await self.db.get_linked_tags(interaction.user.id)
            tag = linked[0] if linked else None
        link = await self.db.get_link(tag) if tag else None
        owner = link["discord_user_id"] if link else interaction.user.id
        await war_repo.set_caller(
            self.db,
            interaction.guild_id,
            w["id"],
            position,
            player_tag=tag,
            user_id=owner,
            note=note,
            assigned_by=interaction.user.id,
        )
        await interaction.response.send_message(embed=await self._callers_embed(interaction, w))

    @caller.command(name="clear", description="Remove a claim (or all claims) in the current war")
    @app_commands.describe(position="Enemy map position; omit to clear every claim (managers only)")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def caller_clear(
        self,
        interaction: discord.Interaction,
        position: app_commands.Range[int, 1, 50] | None = None,
        clan: str | None = None,
    ) -> None:
        w = await self._caller_war(interaction, clan)
        if position is None:
            await require(interaction, "bot")
        else:
            existing = [
                c
                for c in await war_repo.get_callers(self.db, interaction.guild_id, w["id"])
                if c["position"] == position
            ]
            if (
                existing
                and existing[0]["user_id"] != interaction.user.id
                and not await has_permission(interaction, "bot")
            ):
                raise ResolveError("Only the person who claimed that base (or a manager) can clear it.")
        n = await war_repo.clear_caller(self.db, interaction.guild_id, w["id"], position)
        msg = f"Cleared {plural(n, 'claim')}."
        await interaction.response.send_message(msg, embed=await self._callers_embed(interaction, w))

    @caller.command(name="list", description="Show claimed bases in the current war")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def caller_list(self, interaction: discord.Interaction, clan: str | None = None) -> None:
        w = await self._caller_war(interaction, clan)
        await interaction.response.send_message(embed=await self._callers_embed(interaction, w))

    async def _callers_embed(self, interaction: discord.Interaction, w: dict) -> discord.Embed:
        callers = await war_repo.get_callers(self.db, interaction.guild_id, w["id"])
        enemies = {m["map_position"]: m for m in await war_repo.war_members(self.db, w["id"], opponent=True)}
        attacks = await war_repo.war_attacks(self.db, w["id"])
        best: dict[str, int] = {}
        for a in attacks:
            if not a["is_opponent"]:
                best[a["defender_tag"]] = max(best.get(a["defender_tag"], 0), a["stars"])
        names = await war_repo.player_names(self.db, [c["player_tag"] for c in callers if c["player_tag"]])
        e = await make_embed(self.bot, interaction.guild_id)
        calls_embed(e, w, callers, enemies, best, names)
        name, badge = w.get("clan_name"), w.get("clan_badge_url")
        if not name or not badge:  # wars stored before the clan's name and badge were
            row = await self.db.get_clan_row(w["clan_tag"])
            name = name or (row["name"] if row else None)
            badge = badge or (row["badge_url"] if row else None)
        return set_clan(e, name, w["clan_tag"], badge)

    async def _one_clan(self, e: discord.Embed, tags: list[str]) -> discord.Embed:
        """With a single clan, show it (badge and name) on the stats embed."""
        if len(tags) == 1 and (row := await self.db.get_clan_row(tags[0])):
            set_clan(e, row["name"], tags[0], row["badge_url"])
        return e

    # ------------------------------------------------------------------ /stats

    @stats.command(name="attacks", description="War 3★ hit rates per player")
    @app_commands.describe(
        clans="Clan tags/aliases separated by spaces (default: all)", town_hall="Only attackers of this TH"
    )
    @app_commands.choices(period=PERIOD_CHOICES, war_type=WAR_TYPE_CHOICES)
    @app_commands.autocomplete(clans=clan_autocomplete)
    async def stats_attacks(
        self,
        interaction: discord.Interaction,
        clans: str | None = None,
        period: app_commands.Choice[str] | None = None,
        war_type: app_commands.Choice[str] | None = None,
        town_hall: app_commands.Range[int, 1, 20] | None = None,
        min_attacks: app_commands.Range[int, 1, 100] = 1,
    ) -> None:
        tags = await resolve_clans(interaction, clans)
        since, until, label = period_bounds(period.value if period else None)
        kind = war_type.value if war_type else None
        rows = await war_repo.attack_leaderboard(
            self.db, tags, since=since, until=until, war_type=kind, town_hall=town_hall
        )
        rows = [r for r in rows if r[1].attacks >= min_attacks]
        wars = await war_repo.count_wars(self.db, tags, since=since, until=until, war_type=kind)
        top = rows[:40]
        names = await war_repo.player_names(self.db, [t for t, _ in top])
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title="War hit rates (3★)",
            description=(
                attack_totals(rows) + "\n" + attack_rank_table(top, names)
                if rows
                else "No war attacks recorded for these filters."
            ),
        )
        e.set_footer(text=stats_footer(war_type.name if war_type else None, label, wars, town_hall))
        await interaction.response.send_message(embed=await self._one_clan(e, tags))

    @stats.command(name="defense", description="War defense hold rates per player")
    @app_commands.describe(
        clans="Clan tags/aliases separated by spaces (default: all)", town_hall="Only defenders of this TH"
    )
    @app_commands.choices(period=PERIOD_CHOICES, war_type=WAR_TYPE_CHOICES)
    @app_commands.autocomplete(clans=clan_autocomplete)
    async def stats_defense(
        self,
        interaction: discord.Interaction,
        clans: str | None = None,
        period: app_commands.Choice[str] | None = None,
        war_type: app_commands.Choice[str] | None = None,
        town_hall: app_commands.Range[int, 1, 20] | None = None,
    ) -> None:
        tags = await resolve_clans(interaction, clans)
        since, until, label = period_bounds(period.value if period else None)
        kind = war_type.value if war_type else None
        rows = await war_repo.defense_leaderboard(
            self.db, tags, since=since, until=until, war_type=kind, town_hall=town_hall
        )
        wars = await war_repo.count_wars(self.db, tags, since=since, until=until, war_type=kind)
        top = rows[:40]
        names = await war_repo.player_names(self.db, [t for t, _ in top])
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title="War defense hold rates (not tripled)",
            description=(
                defense_totals(rows) + "\n" + defense_rank_table(top, names)
                if rows
                else "No war defenses recorded for these filters."
            ),
        )
        e.set_footer(text=stats_footer(war_type.name if war_type else None, label, wars, town_hall))
        await interaction.response.send_message(embed=await self._one_clan(e, tags))


async def setup(bot) -> None:
    await bot.add_cog(War(bot))
