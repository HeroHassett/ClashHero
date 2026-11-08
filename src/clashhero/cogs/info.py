"""Clan and player lookups: /clan and /player."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import discord
from discord import app_commands

from clashhero.cogs._base import BaseCog
from clashhero.cogs._info_embeds import (
    activity_histogram,
    ago,
    format_units,
    render_histogram,
    render_th_distribution,
    rushed_progress,
    short,
    th_distribution,
    unit_levels,
    upgrade_progress,
)
from clashhero.repos import stats
from clashhero.utils.fmt import chunk_lines, clan_link, make_embed, player_link, role_name, table, ts
from clashhero.utils.ranked import league_icon, league_name
from clashhero.utils.resolve import (
    clan_autocomplete,
    player_autocomplete,
    resolve_clan,
    resolve_player,
)
from clashhero.utils.seasons import game_season, previous_game_season

SEASON_CHOICES = [
    app_commands.Choice(name="Current season", value="current"),
    app_commands.Choice(name="Previous season", value="previous"),
]

MEMBER_VIEWS = [
    app_commands.Choice(name="Town Hall", value="th"),
    app_commands.Choice(name="Trophies", value="trophies"),
    app_commands.Choice(name="Role", value="role"),
    app_commands.Choice(name="Heroes", value="heroes"),
    app_commands.Choice(name="War preference", value="war-pref"),
    app_commands.Choice(name="Join date", value="join-date"),
]

CATEGORY_TITLES = {
    "troops": "Troops",
    "sieges": "Siege Machines",
    "spells": "Spells",
    "heroes": "Heroes",
    "pets": "Pets",
    "equipment": "Hero Equipment",
}


def season_window(choice: app_commands.Choice[str] | None):
    if choice is not None and choice.value == "previous":
        return previous_game_season()
    return game_season()


def _enum(v: Any) -> str:
    return str(getattr(v, "value", v)) if v is not None else "—"


def add_chunked_fields(embed: discord.Embed, title: str, lines: list[str]) -> None:
    """Add lines as one or more fields, each under the 1024-char limit."""
    for i, chunk in enumerate(chunk_lines(lines, 1000)):
        if len(embed.fields) >= 25:
            return
        embed.add_field(name=title if i == 0 else f"{title} (cont.)", value=chunk or "—", inline=False)


class Info(BaseCog):
    """Clan and player information."""

    clan = app_commands.Group(name="clan", description="Clan information", guild_only=True)
    player = app_commands.Group(name="player", description="Player information")

    # ------------------------------------------------------------------ helpers

    async def _member_rows(self, clan_tag: str) -> list[dict]:
        """Members from collected data, falling back to a live API call for untracked clans."""
        rows = [dict(r) for r in await self.db.clan_member_rows(clan_tag)]
        if rows:
            return rows
        c = await self.coc.get_clan(clan_tag)
        return [
            {
                "player_tag": m.tag,
                "name": m.name,
                "town_hall": getattr(m, "town_hall", None),
                "trophies": m.trophies,
                "clan_role": _enum(m.role),
                "heroes_total": None,
                "war_opted_in": None,
                "league": league_name(m),
            }
            for m in c.members
        ]

    # ------------------------------------------------------------------ /clan

    @clan.command(name="info", description="Clan overview: leagues, war record, requirements, TH spread")
    @app_commands.describe(clan="Clan tag or alias (defaults to this channel's clan)")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def clan_info(self, interaction: discord.Interaction, clan: str | None = None) -> None:
        tag = await resolve_clan(interaction, clan, must_be_tracked=False)
        await interaction.response.defer()
        c = await self.coc.get_clan(tag)
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title=f"{c.name} ({c.tag})",
            url=clan_link(c.tag),
            description=(c.description or "")[:1000] or None,
        )
        badge = getattr(getattr(c, "badge", None), "url", None)
        if badge:
            e.set_thumbnail(url=badge)
        e.add_field(name="Level", value=str(c.level))
        e.add_field(name="Members", value=f"{c.member_count}/50")
        e.add_field(name="Type", value=_enum(c.type))
        e.add_field(name="War League", value=getattr(c.war_league, "name", "—") or "—")
        e.add_field(name="Capital League", value=getattr(c.capital_league, "name", "—") or "—")
        loc = getattr(c, "location", None)
        e.add_field(name="Location", value=getattr(loc, "name", None) or "—")
        record = f"{c.war_wins or 0} W"
        if c.public_war_log:
            record += f" / {c.war_losses or 0} L / {c.war_ties or 0} T"
        record += f"\nStreak: {c.war_win_streak or 0}"
        e.add_field(name="War Record", value=record)
        e.add_field(name="War Frequency", value=_enum(c.war_frequency))
        e.add_field(name="War Log", value="Public" if c.public_war_log else "Private")
        reqs = [f"{c.required_trophies or 0} trophies"]
        if getattr(c, "required_townhall", None):
            reqs.append(f"TH{c.required_townhall}+")
        e.add_field(name="Requirements", value=", ".join(reqs))
        e.add_field(name="Clan Points", value=str(c.points or 0))
        e.add_field(name="Capital Points", value=str(getattr(c, "capital_points", 0) or 0))
        dist = th_distribution(getattr(m, "town_hall", None) for m in c.members)
        e.add_field(name="Town Hall Spread", value=render_th_distribution(dist)[:1024], inline=False)
        await interaction.followup.send(embed=e)

    @clan.command(name="members", description="Member list with a chosen view")
    @app_commands.autocomplete(clan=clan_autocomplete)
    @app_commands.choices(view=MEMBER_VIEWS)
    async def clan_members(
        self,
        interaction: discord.Interaction,
        clan: str | None = None,
        view: app_commands.Choice[str] | None = None,
    ) -> None:
        tag = await resolve_clan(interaction, clan, must_be_tracked=False)
        await interaction.response.defer()
        rows = await self._member_rows(tag)
        mode = view.value if view else "th"
        now = datetime.now(UTC)
        if mode == "trophies":
            rows.sort(key=lambda r: r["trophies"] or 0, reverse=True)
            body = table(
                [(i + 1, r["trophies"], short(r["name"])) for i, r in enumerate(rows)], ["#", "CUPS", "NAME"]
            )
        elif mode == "role":
            from clashhero.constants import ROLE_RANK

            rows.sort(key=lambda r: (-ROLE_RANK.get(r["clan_role"] or "", 0), r["name"] or ""))
            body = table([(role_name(r["clan_role"]), short(r["name"])) for r in rows], ["ROLE", "NAME"])
        elif mode == "heroes":
            rows.sort(key=lambda r: r["heroes_total"] or 0, reverse=True)
            body = table(
                [
                    (
                        r["town_hall"] or "?",
                        r["heroes_total"] if r["heroes_total"] is not None else "?",
                        short(r["name"]),
                    )
                    for r in rows
                ],
                ["TH", "HEROES", "NAME"],
            )
        elif mode == "war-pref":

            def pref(v: Any) -> str:
                return "IN" if v == 1 else "OUT" if v == 0 else "?"

            rows.sort(key=lambda r: (pref(r["war_opted_in"]) != "IN", -(r["town_hall"] or 0)))
            body = table(
                [(pref(r["war_opted_in"]), r["town_hall"] or "?", short(r["name"])) for r in rows],
                ["WAR", "TH", "NAME"],
            )
        elif mode == "join-date":
            joins = await stats.join_dates(self.db, tag)
            rows.sort(key=lambda r: joins.get(r["player_tag"]) or "", reverse=True)
            body = table(
                [(ago(joins.get(r["player_tag"]), now), short(r["name"])) for r in rows], ["JOINED", "NAME"]
            )
            body += "\n-# Join dates are only known for joins observed by the bot."
        else:
            rows.sort(key=lambda r: (-(r["town_hall"] or 0), -(r["trophies"] or 0)))
            body = table(
                [(r["town_hall"] or "?", r["trophies"], short(r["name"])) for r in rows],
                ["TH", "CUPS", "NAME"],
            )
        e = await make_embed(
            self.bot, interaction.guild_id, title=f"Members · {tag} ({len(rows)})", description=body[:4096]
        )
        await interaction.followup.send(embed=e)

    @clan.command(name="compo", description="Town Hall composition of a clan")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def clan_compo(self, interaction: discord.Interaction, clan: str | None = None) -> None:
        tag = await resolve_clan(interaction, clan, must_be_tracked=False)
        await interaction.response.defer()
        rows = await self._member_rows(tag)
        dist = th_distribution(r["town_hall"] for r in rows)
        ths = [r["town_hall"] for r in rows if r["town_hall"]]
        avg = sum(ths) / len(ths) if ths else 0
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title=f"Composition · {tag}",
            description=render_th_distribution(dist),
        )
        e.set_footer(text=f"{len(rows)} members · average TH {avg:.2f}")
        await interaction.followup.send(embed=e)

    @clan.command(name="donations", description="Season donation leaderboard for a clan")
    @app_commands.autocomplete(clan=clan_autocomplete)
    @app_commands.choices(season=SEASON_CHOICES)
    async def clan_donations(
        self,
        interaction: discord.Interaction,
        clan: str | None = None,
        season: app_commands.Choice[str] | None = None,
    ) -> None:
        tag = await resolve_clan(interaction, clan)
        window = season_window(season)
        rows = await stats.clan_donations(self.db, tag, window.id)
        lines = [
            (
                i + 1,
                r["donations"],
                r["received"],
                f"{r['donations'] / r['received']:.2f}" if r["received"] else "∞",
                short(r["name"], 14),
            )
            for i, r in enumerate(rows[:50])
        ]
        tot_d = sum(r["donations"] for r in rows)
        tot_r = sum(r["received"] for r in rows)
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title=f"Donations · {tag}",
            description=table(lines, ["#", "DON", "REC", "RATIO", "NAME"])[:4096],
        )
        e.set_footer(text=f"Season ending {window.id} · total {tot_d:,} donated / {tot_r:,} received")
        await interaction.response.send_message(embed=e)

    @clan.command(name="lastseen", description="When each member was last active (estimated)")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def clan_lastseen(self, interaction: discord.Interaction, clan: str | None = None) -> None:
        tag = await resolve_clan(interaction, clan)
        now = datetime.now(UTC)
        rows = await stats.lastseen_rows(self.db, tag, now)
        body = table(
            [(ago(r["last_seen"], now), r["day_activity"], short(r["name"])) for r in rows],
            ["SEEN", "24H", "NAME"],
        )
        e = await make_embed(
            self.bot, interaction.guild_id, title=f"Last seen · {tag}", description=body[:4096]
        )
        e.set_footer(text="Estimated from observed activity (donations, attacks, upgrades, ...)")
        await interaction.response.send_message(embed=e)

    @clan.command(name="activity", description="Hourly activity graph over the last 7 days")
    @app_commands.autocomplete(clan=clan_autocomplete)
    @app_commands.describe(clan="Clan tag or alias; leave empty for all server clans")
    async def clan_activity(self, interaction: discord.Interaction, clan: str | None = None) -> None:
        if clan:
            tags = [await resolve_clan(interaction, clan)]
        else:
            tags = await self.db.get_guild_clans(interaction.guild_id)
            if not tags:
                tags = [await resolve_clan(interaction, None)]
        settings = await self.db.get_guild_settings(interaction.guild_id)
        offset = settings["timezone_offset_min"] or 0
        rows = await stats.activity_hours(self.db, tags, datetime.now(UTC) - timedelta(days=7))
        buckets = activity_histogram(rows, offset)
        sign = "+" if offset >= 0 else "-"
        tz = f"UTC{sign}{abs(offset) // 60:02d}:{abs(offset) % 60:02d}"
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title=f"Activity · {', '.join(tags)}",
            description=render_histogram(buckets),
        )
        e.set_footer(text=f"Last 7 days, hours in {tz}")
        await interaction.response.send_message(embed=e)

    @clan.command(name="history", description="A player's join/leave history across tracked clans")
    @app_commands.autocomplete(player=player_autocomplete)
    async def clan_history(
        self, interaction: discord.Interaction, player: str | None = None, user: discord.User | None = None
    ) -> None:
        tag = await resolve_player(interaction, player, user)
        rows = await stats.membership_history(self.db, tag)
        lines = [
            f"{'📥' if r['kind'] == 'join' else '📤'} {r['kind'].title()} "
            f"**{r['clan_name'] or r['clan_tag']}** {ts(r['occurred_at'], 'f')}"
            for r in rows
        ] or ["No join/leave events recorded for this player."]
        e = await make_embed(
            self.bot, interaction.guild_id, title=f"Clan history · {tag}", description="\n".join(lines)[:4096]
        )
        await interaction.response.send_message(embed=e)

    # ------------------------------------------------------------------ /player

    @player.command(name="info", description="Player profile, linked user, name history and activity")
    @app_commands.autocomplete(player=player_autocomplete)
    async def player_info(
        self, interaction: discord.Interaction, player: str | None = None, user: discord.User | None = None
    ) -> None:
        tag = await resolve_player(interaction, player, user)
        await interaction.response.defer()
        p = await self.coc.get_player(tag)
        e = await make_embed(
            self.bot, interaction.guild_id, title=f"{p.name} ({p.tag})", url=player_link(p.tag)
        )
        icon = league_icon(p)
        if icon:
            e.set_thumbnail(url=icon)
        e.add_field(
            name="Town Hall",
            value=str(p.town_hall)
            + (f" (weapon {p.town_hall_weapon})" if getattr(p, "town_hall_weapon", None) else ""),
        )
        e.add_field(name="Level", value=str(p.exp_level))
        e.add_field(name="Trophies", value=f"{p.trophies} (best {p.best_trophies})")
        e.add_field(name="League", value=league_name(p) or "Unranked")
        e.add_field(name="War Stars", value=str(p.war_stars))
        pref = getattr(p, "war_opted_in", None)
        e.add_field(name="War Preference", value="In" if pref else "Out" if pref is False else "—")
        if p.clan:
            e.add_field(
                name="Clan", value=f"[{p.clan.name}]({clan_link(p.clan.tag)}) · {role_name(_enum(p.role))}"
            )
        e.add_field(name="Attacks / Defenses won", value=f"{p.attack_wins} / {p.defense_wins}")
        e.add_field(name="Capital Gold", value=f"{getattr(p, 'clan_capital_contributions', 0) or 0:,}")

        heroes = [h for h in (p.heroes or []) if getattr(h, "is_home_base", True)]
        if heroes:
            e.add_field(
                name="Heroes", value=" · ".join(f"{h.name} {h.level}" for h in heroes)[:1024], inline=False
            )
        equipment = getattr(p, "equipment", None) or []
        if equipment:
            e.add_field(
                name="Equipment",
                value=", ".join(f"{q.name} {q.level}" for q in equipment)[:1024],
                inline=False,
            )
        pets = getattr(p, "pets", None) or []
        if pets:
            e.add_field(
                name="Pets", value=", ".join(f"{q.name} {q.level}" for q in pets)[:1024], inline=False
            )

        link = await self.db.get_link(p.tag)
        if link:
            e.add_field(
                name="Discord", value=f"<@{link['discord_user_id']}>" + (" ✅" if link["verified"] else "")
            )
        row = await self.db.get_player_row(p.tag)
        if row and row["last_seen"]:
            e.add_field(name="Last Seen", value=ts(row["last_seen"]))
        totals = await stats.player_season_totals(self.db, p.tag, game_season().id)
        if totals and totals["donations"] is not None:
            e.add_field(
                name="Season Donations", value=f"{totals['donations']:,} / {totals['received']:,} received"
            )
        names = [r["name"] for r in await self.db.name_history(p.tag) if r["name"] != p.name]
        if names:
            e.add_field(name="Previous Names", value=", ".join(names[-10:])[:1024], inline=False)
        await interaction.followup.send(embed=e)

    async def _units(self, interaction: discord.Interaction, player: str | None, user: discord.User | None):
        tag = await resolve_player(interaction, player, user)
        await interaction.response.defer()
        p = await self.coc.get_player(tag)
        return p, unit_levels(p)

    @player.command(name="units", description="Troop, spell, hero and pet levels vs max for the TH")
    @app_commands.autocomplete(player=player_autocomplete)
    async def player_units(
        self, interaction: discord.Interaction, player: str | None = None, user: discord.User | None = None
    ) -> None:
        p, units = await self._units(interaction, player, user)
        _, prog = upgrade_progress(units)
        e = await make_embed(self.bot, interaction.guild_id, title=f"Units · {p.name} (TH{p.town_hall})")
        for cat, lines in format_units(units).items():
            add_chunked_fields(e, CATEGORY_TITLES.get(cat, cat), lines)
        e.set_footer(text=f"{prog.percent:.1f}% of TH{p.town_hall} max levels")
        await interaction.followup.send(embed=e)

    @player.command(name="upgrades", description="Remaining upgrades for the current Town Hall")
    @app_commands.autocomplete(player=player_autocomplete)
    async def player_upgrades(
        self, interaction: discord.Interaction, player: str | None = None, user: discord.User | None = None
    ) -> None:
        p, units = await self._units(interaction, player, user)
        pending, prog = upgrade_progress(units)
        e = await make_embed(
            self.bot, interaction.guild_id, title=f"Remaining upgrades · {p.name} (TH{p.town_hall})"
        )
        if not pending:
            e.description = "Everything is maxed for this Town Hall. 🎉"
        for cat, lines in format_units(pending, show="remaining").items():
            add_chunked_fields(e, CATEGORY_TITLES.get(cat, cat), lines)
        e.set_footer(text=f"{prog.percent:.1f}% complete · {prog.total - prog.done} levels remaining")
        await interaction.followup.send(embed=e)

    @player.command(name="rushed", description="Units below the previous Town Hall's max levels")
    @app_commands.autocomplete(player=player_autocomplete)
    async def player_rushed(
        self, interaction: discord.Interaction, player: str | None = None, user: discord.User | None = None
    ) -> None:
        p, units = await self._units(interaction, player, user)
        rushed, prog = rushed_progress(units)
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title=f"Rushed check · {p.name} (TH{p.town_hall})",
            description=f"**{prog.percent:.1f}% rushed** ({prog.done} levels below TH{p.town_hall - 1} max)"
            if rushed
            else f"Not rushed: everything is at least TH{p.town_hall - 1} max. ✅",
        )
        for cat, lines in format_units(rushed, show="rushed").items():
            add_chunked_fields(e, CATEGORY_TITLES.get(cat, cat), lines)
        await interaction.followup.send(embed=e)


async def setup(bot) -> None:
    await bot.add_cog(Info(bot))
