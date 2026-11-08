"""Clan and player lookups: /clan and /player."""

from __future__ import annotations

import asyncio
import io
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import discord
from discord import app_commands

from clashhero.cogs._base import BaseCog, send_error
from clashhero.cogs._info_embeds import (
    PAGE_LABELS,
    PLAYER_PAGE_TEMPLATE,
    PLAYER_PAGES,
    activity_histogram,
    ago,
    pack_blocks,
    player_page_id,
    profile_parts,
    render_histogram,
    render_th_distribution,
    rushed_blocks,
    short,
    th_distribution,
    unit_levels,
    units_blocks,
    upgrades_blocks,
)
from clashhero.constants import CLAN_TYPE_LABELS, WAR_FREQUENCY_LABELS
from clashhero.repos import ranks, stats
from clashhero.services import cards
from clashhero.services.emojis import EMOJIS
from clashhero.utils.fmt import clan_link, make_embed, player_link, role_name, table, ts
from clashhero.utils.ranked import league_icon, league_name
from clashhero.utils.resolve import (
    clan_autocomplete,
    player_autocomplete,
    resolve_clan,
    resolve_player,
)
from clashhero.utils.seasons import game_season, previous_game_season

log = logging.getLogger(__name__)

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


def season_window(choice: app_commands.Choice[str] | None):
    if choice is not None and choice.value == "previous":
        return previous_game_season()
    return game_season()


def _enum(v: Any) -> str:
    return str(getattr(v, "value", v)) if v is not None else "—"


class Info(BaseCog):
    """Clan and player information."""

    clan = app_commands.Group(name="clan", description="Clan information", guild_only=True)
    player = app_commands.Group(name="player", description="Player information")

    async def cog_load(self) -> None:
        # The page buttons are persistent: register them so they keep working after a restart.
        self.bot.add_dynamic_items(PlayerPageButton)

    async def cog_unload(self) -> None:
        self.bot.remove_dynamic_items(PlayerPageButton)

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
        e.add_field(name="Type", value=CLAN_TYPE_LABELS.get(_enum(c.type), _enum(c.type)))
        e.add_field(name="War League", value=getattr(c.war_league, "name", "—") or "—")
        e.add_field(name="Capital League", value=getattr(c.capital_league, "name", "—") or "—")
        loc = getattr(c, "location", None)
        e.add_field(name="Location", value=getattr(loc, "name", None) or "—")
        record = f"{c.war_wins or 0} W"
        if c.public_war_log:
            record += f" / {c.war_losses or 0} L / {c.war_ties or 0} T"
        record += f"\nStreak: {c.war_win_streak or 0}"
        e.add_field(name="War Record", value=record)
        e.add_field(
            name="War Frequency",
            value=WAR_FREQUENCY_LABELS.get(_enum(c.war_frequency), _enum(c.war_frequency)),
        )
        e.add_field(name="War Log", value="Public" if c.public_war_log else "Private")
        reqs = [f"{c.required_trophies or 0} trophies"]
        if getattr(c, "required_townhall", None):
            reqs.append(f"TH{c.required_townhall}+")
        e.add_field(name="Requirements", value=", ".join(reqs))
        e.add_field(name="Clan Points", value=str(c.points or 0))
        e.add_field(name="Capital Points", value=str(getattr(c, "capital_points", 0) or 0))
        dist = th_distribution(getattr(m, "town_hall", None) for m in c.members)
        labels = (getattr(c, "_raw_data", None) or {}).get("labels") or []
        if labels:
            e.add_field(
                name="Labels",
                value=" · ".join(f"{EMOJIS.label(lb.get('name'))} {lb.get('name')}".strip() for lb in labels),
                inline=False,
            )
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
        image = await self._activity_image(tags, buckets, tz)
        if image is not None:
            await interaction.response.send_message(file=image)
            return
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title=f"Activity · {', '.join(tags)}",
            description=render_histogram(buckets),
        )
        e.set_footer(text=f"Last 7 days, hours in {tz}")
        await interaction.response.send_message(embed=e)

    async def _activity_image(self, tags: list[str], buckets: list[int], tz: str) -> discord.File | None:
        """The activity chart as an image, or None (logged) if it can't be drawn."""
        try:
            title, badge = f"{len(tags)} clans", None
            if len(tags) == 1:
                row = await self.db.get_clan_row(tags[0])
                title = (row["name"] if row else None) or tags[0]
                url = row["badge_url"] if row else None
                badge = (await cards.fetch_badges([url])).get(url) if url else None
            card = cards.ActivityCard(title, ", ".join(tags), tuple(buckets), tz, badge=badge)
            png = await asyncio.to_thread(cards.render_activity, card)
        except Exception:
            log.exception("Couldn't draw the activity chart for %s", tags)
            return None
        return discord.File(io.BytesIO(png), filename="clan-activity.png")

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

    async def _send_page(
        self, interaction: discord.Interaction, player: str | None, user: discord.User | None, page: str
    ) -> None:
        tag = await resolve_player(interaction, player, user)
        await interaction.response.defer()
        embeds, view = await render_player_page(self.bot, interaction.guild_id, tag, page)
        await interaction.followup.send(embeds=embeds, view=view)

    @player.command(name="info", description="Player profile, linked user, name history and activity")
    @app_commands.autocomplete(player=player_autocomplete)
    async def player_info(
        self, interaction: discord.Interaction, player: str | None = None, user: discord.User | None = None
    ) -> None:
        await self._send_page(interaction, player, user, "profile")

    @player.command(name="units", description="Troop, spell, hero and pet levels vs max for the TH")
    @app_commands.autocomplete(player=player_autocomplete)
    async def player_units(
        self, interaction: discord.Interaction, player: str | None = None, user: discord.User | None = None
    ) -> None:
        await self._send_page(interaction, player, user, "units")

    @player.command(name="upgrades", description="Remaining upgrades for the current Town Hall")
    @app_commands.autocomplete(player=player_autocomplete)
    async def player_upgrades(
        self, interaction: discord.Interaction, player: str | None = None, user: discord.User | None = None
    ) -> None:
        await self._send_page(interaction, player, user, "upgrades")

    @player.command(name="rushed", description="Units below the previous Town Hall's max levels")
    @app_commands.autocomplete(player=player_autocomplete)
    async def player_rushed(
        self, interaction: discord.Interaction, player: str | None = None, user: discord.User | None = None
    ) -> None:
        await self._send_page(interaction, player, user, "rushed")


# ------------------------------------------------------------------ player pages


async def render_player_page(
    client, guild_id: int | None, tag: str, page: str
) -> tuple[list[discord.Embed], discord.ui.View]:
    """Fetch a player and build one page of their card (embeds plus the page buttons)."""
    p = await client.coc.get_player(tag)
    title, url = f"{p.name} ({p.tag})", player_link(p.tag)
    if page == "profile":
        embed = await make_embed(client, guild_id, title=title, url=url)
        link = await client.db.get_link(p.tag)
        row = await client.db.get_player_row(p.tag)
        totals = await stats.player_season_totals(client.db, p.tag, game_season().id)
        names = [r["name"] for r in await client.db.name_history(p.tag) if r["name"] != p.name]
        embed.description, fields = profile_parts(
            p,
            discord_id=link["discord_user_id"] if link else None,
            verified=bool(link and link["verified"]),
            last_seen=row["last_seen"] if row else None,
            season=(totals["donations"], totals["received"] or 0)
            if totals and totals["donations"] is not None
            else None,
            names=names,
            country=await ranks.country_rank(client.db, p.tag, now=datetime.now(UTC)),
            best_finish=await ranks.best_finish(client.db, p.tag),
        )
        for name, value, inline in fields[:25]:
            embed.add_field(name=name, value=value or "—", inline=inline)
        icon = league_icon(p)
        if icon:
            embed.set_thumbnail(url=icon)
        embeds = [embed]
    else:
        builder = {"units": units_blocks, "upgrades": upgrades_blocks, "rushed": rushed_blocks}[page]
        pages = pack_blocks(builder(unit_levels(p), p.town_hall or 0))
        first = await make_embed(client, guild_id, title=title, url=url, description=pages[0])
        embeds = [first, *(discord.Embed(color=first.color, description=d) for d in pages[1:])]
    embeds[-1].set_footer(text=f"{PAGE_LABELS[page]} · TH{p.town_hall}")
    embeds[-1].timestamp = datetime.now(UTC)
    return embeds, player_page_view(page, p.tag)


def player_page_view(page: str, tag: str) -> discord.ui.View:
    """Profile · Units · Upgrades · Rushed · ↻ buttons; the current page's button is highlighted
    and disabled."""
    view = discord.ui.View(timeout=None)
    for name in PLAYER_PAGES:
        view.add_item(PlayerPageButton(name, tag, current=name == page))
    view.add_item(PlayerPageButton(page, tag, refresh=True))
    return view


class PlayerPageButton(discord.ui.DynamicItem[discord.ui.Button], template=PLAYER_PAGE_TEMPLATE):
    """Switches a player card to another page (or refreshes it), fetching the player again.
    Persistent: works on old messages after a restart. Anyone may click; the pages are read-only."""

    def __init__(self, page: str, tag: str, *, current: bool = False, refresh: bool = False) -> None:
        if refresh:
            button = discord.ui.Button(
                label="↻",
                style=discord.ButtonStyle.secondary,
                custom_id=player_page_id(page, tag, refresh=True),
            )
        else:
            button = discord.ui.Button(
                label=PAGE_LABELS[page],
                style=discord.ButtonStyle.primary if current else discord.ButtonStyle.secondary,
                disabled=current,
                custom_id=player_page_id(page, tag),
            )
        super().__init__(button)
        self.page, self.tag = page, f"#{tag.lstrip('#')}"

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(match["page"], match["tag"], refresh=bool(match["refresh"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.response.defer()
            embeds, view = await render_player_page(
                interaction.client, interaction.guild_id, self.tag, self.page
            )
            await interaction.edit_original_response(embeds=embeds, view=view)
        except Exception as e:  # buttons have no command error handler
            await send_error(interaction, e)


async def setup(bot) -> None:
    await bot.add_cog(Info(bot))
