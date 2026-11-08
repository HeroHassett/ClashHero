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
    attack_table,
    defense_table,
    history_embed,
    lineup_embed,
    period_bounds,
    resolve_player_tags,
    state_value,
    war_overview_embed,
    war_sides,
)
from clashhero.repos import war as war_repo
from clashhero.utils.checks import has_permission, require
from clashhero.utils.fmt import guild_color, make_embed, stars, ts
from clashhero.utils.resolve import (
    ResolveError,
    clan_autocomplete,
    player_autocomplete,
    resolve_clan,
    resolve_clans,
    resolve_player,
)

RESULT_ICON = {"win": "🟢", "lose": "🔴", "tie": "⚪"}


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
        lines = [
            f"`{m['map_position']:>2}` TH{m['town_hall']} {discord.utils.escape_markdown(m['name'])}"
            f" — {n} left" + (f" <@{links[m['tag']]}>" if m["tag"] in links else "")
            for m, n in left
        ]
        state = state_value(w)
        when = "starts" if state == "preparation" else "ends"
        t = w.start_time if state == "preparation" else w.end_time
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title=f"Remaining attacks · {ours.name}",
            description=(
                f"War {when} {ts(getattr(t, 'time', t))}\n\n"
                + ("\n".join(lines) or "Everyone has attacked! 🎉")
            )[:4096],
        )
        await interaction.followup.send(embed=e, allowed_mentions=discord.AllowedMentions(users=mention))

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
        entries = await self.coc.get_war_log(tag, limit=count)
        lines = []
        for e in list(entries)[:count]:
            res = str(getattr(e.result, "value", e.result) or "")
            end = getattr(e.end_time, "time", None)
            if getattr(e, "is_league_entry", False):
                lines.append(f"🏆 CWL · {e.clan.stars}★ · {ts(end)}")
                continue
            icon = RESULT_ICON.get(res, "▫️")
            opp = discord.utils.escape_markdown(e.opponent.name or "?")
            lines.append(
                f"{icon} {e.team_size}v{e.team_size} vs **{opp}**"
                f" · {e.clan.stars}-{e.opponent.stars}★ · {e.clan.destruction:.1f}%-"
                f"{e.opponent.destruction:.1f}% · {ts(end)}"
            )
        clan_row = await self.db.get_clan_row(tag)
        title = f"War log · {clan_row['name'] if clan_row else tag}"
        e = await make_embed(
            self.bot, interaction.guild_id, title=title, description="\n".join(lines) or "No wars in the log."
        )
        await interaction.followup.send(embed=e)

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
        await interaction.response.send_message(embed=history_embed(name, label, h, color))

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
            existing = [c for c in await war_repo.get_callers(self.db, w["id"]) if c["position"] == position]
            if (
                existing
                and existing[0]["user_id"] != interaction.user.id
                and not await has_permission(interaction, "bot")
            ):
                raise ResolveError("Only the person who claimed that base (or a manager) can clear it.")
        n = await war_repo.clear_caller(self.db, w["id"], position)
        msg = f"Cleared {n} claim(s)."
        await interaction.response.send_message(msg, embed=await self._callers_embed(interaction, w))

    @caller.command(name="list", description="Show claimed bases in the current war")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def caller_list(self, interaction: discord.Interaction, clan: str | None = None) -> None:
        w = await self._caller_war(interaction, clan)
        await interaction.response.send_message(embed=await self._callers_embed(interaction, w))

    async def _callers_embed(self, interaction: discord.Interaction, w: dict) -> discord.Embed:
        callers = await war_repo.get_callers(self.db, w["id"])
        enemies = {m["map_position"]: m for m in await war_repo.war_members(self.db, w["id"], opponent=True)}
        attacks = await war_repo.war_attacks(self.db, w["id"])
        best: dict[str, int] = {}
        for a in attacks:
            if not a["is_opponent"]:
                best[a["defender_tag"]] = max(best.get(a["defender_tag"], 0), a["stars"])
        names = await war_repo.player_names(self.db, [c["player_tag"] for c in callers if c["player_tag"]])
        lines = []
        for c in callers:
            enemy = enemies.get(c["position"])
            base = f"TH{enemy['town_hall']} {discord.utils.escape_markdown(enemy['name'])}" if enemy else "?"
            got = best.get(enemy["player_tag"], 0) if enemy else 0
            who = names.get(c["player_tag"], "") if c["player_tag"] else ""
            who = f"{who} <@{c['user_id']}>" if c["user_id"] else who
            note = f" — _{c['note']}_" if c["note"] else ""
            lines.append(f"`{c['position']:>2}` {base} {stars(got) if got else ''} → {who}{note}")
        return await make_embed(
            self.bot,
            interaction.guild_id,
            title=f"Calls · vs {w['opponent_name']}",
            description="\n".join(lines) or "No bases claimed yet. Use `/caller assign`.",
        )

    # ------------------------------------------------------------------ /stats

    @stats.command(name="attacks", description="War hit rates per player (3★ %, avg stars, missed)")
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
        rows = await war_repo.attack_leaderboard(
            self.db,
            tags,
            since=since,
            until=until,
            war_type=war_type.value if war_type else None,
            town_hall=town_hall,
        )
        rows = [r for r in rows if r[1].attacks >= min_attacks][:40]
        names = await war_repo.player_names(self.db, [t for t, _ in rows])
        desc = (
            f"{label}"
            + (f" · TH{town_hall}" if town_hall else "")
            + (f" · {war_type.name}" if war_type else "")
        )
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title="War attack stats",
            description=(desc + "\n" + attack_table(rows, names))[:4096],
        )
        await interaction.response.send_message(embed=e)

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
        rows = (
            await war_repo.defense_leaderboard(
                self.db,
                tags,
                since=since,
                until=until,
                war_type=war_type.value if war_type else None,
                town_hall=town_hall,
            )
        )[:40]
        names = await war_repo.player_names(self.db, [t for t, _ in rows])
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title="War defense stats",
            description=(label + "\n" + defense_table(rows, names))[:4096],
        )
        await interaction.response.send_message(embed=e)


async def setup(bot) -> None:
    await bot.add_cog(War(bot))
