"""Clan Capital: raid weekend summaries, capital gold contributions and player raid history."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import discord
from discord import app_commands

from clashhero.cogs._base import BaseCog
from clashhero.cogs._live_embeds import clan_row_header, fit_names, fit_table, icon_text
from clashhero.cogs._log_embeds import arrow
from clashhero.cogs._war_embeds import resolve_player_tags
from clashhero.repos import capital as cap_repo
from clashhero.services.emojis import EMOJIS
from clashhero.utils.fmt import make_embed, player_link, ts
from clashhero.utils.resolve import clan_autocomplete, player_autocomplete, resolve_clan
from clashhero.utils.seasons import game_season, previous_game_season, utcnow

SEASON_CHOICES = [
    app_commands.Choice(name="This season", value="current"),
    app_commands.Choice(name="Last season", value="last"),
]


def _stat(icon: str, value: Any, label: str) -> str:
    """``{icon} **12,345** label`` (no icon when it isn't uploaded)."""
    shown = f"{value:,}" if isinstance(value, int) else str(value)
    return icon_text(icon, f"**{shown}**", label)


def raid_weekend_lines(summary: Mapping[str, Any]) -> list[str]:
    """When the raid weekend ran, then its loot, attacks, districts and (once paid) medals."""
    gold, medal, sword = EMOJIS.get("res_capital_gold"), EMOJIS.get("res_raid_medal"), EMOJIS.get("ui_sword")
    if summary.get("state") == "ongoing":
        when = icon_text(EMOJIS.get("ui_stopwatch"), f"**Ongoing** · ends {ts(summary['end_time'])}")
    else:
        when = f"**Ended** · {ts(summary['start_time'], 'd')} – {ts(summary['end_time'], 'd')}"
    lines = [
        when,
        _stat(gold, summary.get("total_loot") or 0, "Capital Gold looted"),
        _stat(sword, summary.get("attack_count") or 0, "attacks")
        + " · "
        + _stat("", summary.get("districts_destroyed") or 0, "districts destroyed"),
    ]
    offense, defense = summary.get("offensive_reward") or 0, summary.get("defensive_reward") or 0
    if offense or defense:
        lines.append(_stat(medal, offense, "offense medals") + " · " + _stat("", defense, "defense medals"))
    return lines


def raid_weekend_text(summary: Mapping[str, Any], members: Sequence[Mapping[str, Any]]) -> str:
    """The weekend's totals, then every raider by loot in a table."""
    head = "\n".join(raid_weekend_lines(summary))
    if not members:
        return f"{head}\n\nNobody has raided yet."
    data = [
        (i, m["name"][:14], f"{m['attacks']}/{m['attack_limit']}", f"{m['loot']:,}")
        for i, m in enumerate(members, 1)
    ]
    return f"{head}\n\n{fit_table(data, ['#', 'PLAYER', 'HITS', 'LOOT'], 4090 - len(head))}"


def history_text(rows: Sequence[Mapping[str, Any]], one_player: bool) -> str:
    """Totals over the weekends shown, then one row per weekend (the player column only when
    several accounts are listed)."""
    if not rows:
        return "No raids recorded yet."
    gold, sword = EMOJIS.get("res_capital_gold"), EMOJIS.get("ui_sword")
    weekends = len({r["start_time"] for r in rows})
    head = (
        _stat(gold, sum(r["loot"] for r in rows), "Capital Gold looted")
        + f" in **{weekends}** raid weekend{'s' * (weekends != 1)}\n"
        + _stat(sword, sum(r["attacks"] for r in rows), "attacks")
    )
    if one_player:
        data = [(r["start_time"][:10], f"{r['attacks']}/{r['attack_limit']}", f"{r['loot']:,}") for r in rows]
        headers = ["WEEKEND", "HITS", "LOOT"]
    else:
        data = [
            (r["start_time"][:10], r["name"][:12], f"{r['attacks']}/{r['attack_limit']}", f"{r['loot']:,}")
            for r in rows
        ]
        headers = ["WEEKEND", "PLAYER", "HITS", "LOOT"]
    return f"{head}\n\n{fit_table(data, headers, 4090 - len(head))}"


class Capital(BaseCog):
    capital = app_commands.Group(name="capital", description="Clan Capital raids and contributions")

    @capital.command(name="raids", description="Raid weekend summary with per-member attacks and loot")
    @app_commands.describe(weeks_back="0 = latest raid weekend, 1 = the one before, ...")
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def raids(
        self,
        interaction: discord.Interaction,
        clan: str | None = None,
        weeks_back: app_commands.Range[int, 0, 9] = 0,
    ) -> None:
        tag = await resolve_clan(interaction, clan, must_be_tracked=False)
        await interaction.response.defer()
        weekend = await cap_repo.raid_weekend(self.db, tag, weeks_back)
        if weekend is not None:
            members = await cap_repo.raid_members(self.db, tag, weekend["start_time"])
            missing = await cap_repo.raid_non_participants(self.db, tag, weekend["start_time"])
            summary = dict(weekend)
        else:  # not collected yet (clan just added): read the API directly
            log = await self.coc.get_raid_log(tag, limit=weeks_back + 1)
            entries = list(log)
            if len(entries) <= weeks_back:
                await interaction.followup.send("No raid weekend found.")
                return
            entry = entries[weeks_back]
            summary = {
                "start_time": entry.start_time.time.isoformat(),
                "end_time": entry.end_time.time.isoformat(),
                "state": str(getattr(entry.state, "value", entry.state)),
                "total_loot": entry.total_loot,
                "attack_count": entry.attack_count,
                "districts_destroyed": entry.destroyed_district_count,
                "offensive_reward": entry.offensive_reward,
                "defensive_reward": entry.defensive_reward,
            }
            members = sorted(
                (
                    {
                        "name": m.name,
                        "attacks": m.attack_count,
                        "attack_limit": m.attack_limit + (m.bonus_attack_limit or 0),
                        "loot": m.capital_resources_looted,
                    }
                    for m in entry.members or []
                ),
                key=lambda m: -m["loot"],
            )
            missing = []
        clan_row = await self.db.get_clan_row(tag)
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title=icon_text(EMOJIS.get("res_capital_gold"), "Raid weekend"),
            description=raid_weekend_text(summary, members),
        )
        clan_row_header(e, clan_row, tag)
        if missing:
            names = [discord.utils.escape_markdown(m["name"]) for m in missing]
            label = f"{arrow('down')} Didn't raid ({len(missing)})"
            e.add_field(name=label, value=fit_names(names), inline=False)
        await interaction.followup.send(embed=e)

    @capital.command(name="contributions", description="Capital gold contributed per member this season")
    @app_commands.choices(season=SEASON_CHOICES)
    @app_commands.autocomplete(clan=clan_autocomplete)
    async def contributions(
        self,
        interaction: discord.Interaction,
        clan: str | None = None,
        season: app_commands.Choice[str] | None = None,
    ) -> None:
        tag = await resolve_clan(interaction, clan)
        s = previous_game_season() if season and season.value == "last" else game_season()
        rows = await cap_repo.capital_contributions(self.db, tag, s.id)
        data = [(i + 1, r["name"][:16], f"{r['capital_gold']:,}") for i, r in enumerate(rows[:50])]
        total = sum(r["capital_gold"] for r in rows)
        gold = EMOJIS.get("res_capital_gold")
        ended = s.end <= utcnow()
        givers = f"**{len(rows)}** member{'s' * (len(rows) != 1)}"
        head = "\n".join(
            [
                _stat(gold, total, f"Capital Gold contributed by {givers}"),
                icon_text(EMOJIS.get("ui_stopwatch"), f"Season {'ended' if ended else 'ends'} {ts(s.end)}"),
            ]
        )
        body = fit_table(data, ["#", "PLAYER", "GOLD"], 4090 - len(head)) if data else ""
        clan_row = await self.db.get_clan_row(tag)
        e = await make_embed(
            self.bot,
            interaction.guild_id,
            title=icon_text(gold, "Capital Gold contributions"),
            description=f"{head}\n\n{body or 'No contributions recorded yet.'}",
        )
        clan_row_header(e, clan_row, tag)
        await interaction.response.send_message(embed=e)

    @capital.command(name="history", description="A player's raid weekends (attacks and loot)")
    @app_commands.autocomplete(player=player_autocomplete)
    async def history(
        self, interaction: discord.Interaction, player: str | None = None, user: discord.User | None = None
    ) -> None:
        tags = await resolve_player_tags(interaction, player, user)
        rows = await cap_repo.player_raid_history(self.db, tags)
        one = len({r["player_tag"] for r in rows}) == 1
        if one:  # one player: "Name (#TAG)" linked to their profile
            name, tag = rows[0]["name"], rows[0]["player_tag"]
            title, url = f"{discord.utils.escape_markdown(name)} ({tag})", player_link(tag)
        else:
            title, url = "Raid history", None
        e = await make_embed(
            self.bot, interaction.guild_id, title=title, url=url, description=history_text(rows, one)
        )
        if one:
            e.set_footer(text="Raid history")
        await interaction.response.send_message(embed=e)


async def setup(bot) -> None:
    await bot.add_cog(Capital(bot))
