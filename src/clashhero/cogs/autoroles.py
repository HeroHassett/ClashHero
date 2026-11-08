"""Autoroles (clan role, TH, BH, league, trophy bands, family, war lineup) and nicknames.

Desired roles are computed from linked accounts + collected player data by pure functions in
``repos.autoroles``; this cog applies the diff to Discord members. A DB-only loop re-syncs every
10 minutes for servers with auto-update on, and a member is synced immediately when they link.
War roles follow war state events instead of the loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from clashhero.cogs._base import BaseCog
from clashhero.events import WarStateChanged
from clashhero.repos import autoroles as repo
from clashhero.repos.autoroles import Account, Mapping
from clashhero.utils.checks import manager, require
from clashhero.utils.fmt import make_embed
from clashhero.utils.ranked import LEAGUE_TIERS
from clashhero.utils.resolve import ResolveError, clan_autocomplete, resolve_clan
from clashhero.utils.style import SEP, chip, fail_embed, ok_embed

log = logging.getLogger(__name__)

KIND_CHOICES = [
    app_commands.Choice(name=n, value=v)
    for n, v in [
        ("Clan roles", "clan_role"),
        ("Town Hall", "town_hall"),
        ("Builder Hall", "builder_hall"),
        ("Leagues", "league"),
        ("Legend trophy bands", "trophy_band"),
        ("Builder leagues", "builder_league"),
        ("Family", "family"),
        ("War lineup", "war"),
    ]
]
KIND_LABEL = {c.value: c.name for c in KIND_CHOICES}
DEFAULT_LEAGUES = list(LEAGUE_TIERS)  # ranked tiers, from utils/ranked.py


def _onoff(value) -> str:
    return "✅" if value else "❌"


def settings_text(s) -> str:
    """The Settings field of /autorole list."""
    return "\n".join(
        (
            SEP.join(
                (
                    f"Auto update {_onoff(s['auto_update'])}",
                    f"Removal delay {chip(str(s['removal_delay_hours']) + 'h')}",
                    f"Allow not linked {_onoff(s['allow_not_linked'])}",
                )
            ),
            SEP.join(
                (
                    f"Non-family accounts count {_onoff(s['allow_non_family'])}",
                    f"Verified-only clan roles {_onoff(s['only_verified_clan_roles'])}",
                )
            ),
        )
    )


def sync_text(added: int, removed: int, who: str | None = None, nick_changed: bool = False) -> str:
    """``Roles synced for @user · `+2` added · `-1` removed · nickname updated``."""
    parts = [
        f"Roles synced for {who}" if who else "Roles synced",
        f"{chip(f'+{added}')} added",
        f"{chip(f'-{removed}')} removed",
    ]
    if nick_changed:
        parts.append("nickname updated")
    return SEP.join(parts) + "."


@dataclass
class GuildContext:
    mappings: list[Mapping]
    managed: set[int]
    settings: object
    family: dict[str, object]  # clan_tag -> guild_clans row (name, alias)


@dataclass
class SyncResult:
    added: int = 0
    removed: int = 0
    nick_changed: bool = False


class Autoroles(BaseCog):
    autorole = app_commands.Group(
        name="autorole", description="Automatic Discord roles from Clash data", guild_only=True
    )
    nickname = app_commands.Group(
        name="nickname", description="Automatic nicknames from Clash data", guild_only=True
    )

    async def cog_load(self) -> None:
        self.sync_loop.start()

    async def cog_unload(self) -> None:
        self.sync_loop.cancel()

    # ------------------------------------------------------------------ sync engine

    async def context(self, guild_id: int) -> GuildContext:
        mappings = [Mapping.from_row(r) for r in await repo.get_mappings(self.db, guild_id)]
        family = {r["clan_tag"]: r for r in await self.db.get_guild_clan_rows(guild_id)}
        return GuildContext(
            mappings=mappings,
            managed=repo.managed_roles(mappings),
            settings=await repo.get_settings(self.db, guild_id),
            family=family,
        )

    def _assignable(self, guild: discord.Guild, role_ids: set[int]) -> list[discord.Role]:
        me = guild.me
        out = []
        for rid in role_ids:
            role = guild.get_role(rid)
            if role and not role.managed and role < me.top_role:
                out.append(role)
        return out

    def nickname_for(self, member: discord.Member, accounts: list[Account], ctx: GuildContext) -> str | None:
        s = ctx.settings
        if not s["nick_enabled"]:
            return None
        account, is_family = repo.pick_account(accounts, set(ctx.family), s["nick_preference"])
        if account is None:
            return None
        fmt = s["family_format"] if is_family else s["non_family_format"]
        if not fmt:
            return None
        clan = ctx.family.get(account.clan_tag or "") if is_family else None
        return repo.format_nickname(
            fmt,
            name=account.name,
            th=account.town_hall,
            role=account.clan_role if is_family else None,
            alias=clan["alias"] if clan else None,
            clan=clan["name"] if clan else None,
            discord=member.global_name or member.name,
        )

    async def sync_member(
        self, member: discord.Member, ctx: GuildContext, accounts: list[Account]
    ) -> SyncResult:
        result = SyncResult()
        if member.bot:
            return result
        s = ctx.settings
        guild = member.guild
        if not accounts and s["allow_not_linked"]:
            return result
        desired = repo.desired_roles(
            accounts,
            set(ctx.family),
            ctx.mappings,
            only_verified_clan_roles=bool(s["only_verified_clan_roles"]),
            allow_non_family=bool(s["allow_non_family"]),
        )
        current = {r.id for r in member.roles}
        to_add, to_remove = repo.role_diff(current, desired, ctx.managed)
        await repo.cancel_removals(self.db, guild.id, member.id, desired)
        delay = s["removal_delay_hours"] or 0
        if to_remove and delay:
            now = datetime.now(UTC)
            for rid in to_remove:
                await repo.schedule_removal(
                    self.db, guild.id, member.id, rid, (now + timedelta(hours=delay)).isoformat()
                )
            to_remove = to_remove & await repo.due_removals(self.db, guild.id, member.id, now.isoformat())
        try:
            add_roles = self._assignable(guild, to_add)
            if add_roles:
                await member.add_roles(*add_roles, reason="ClashHero autoroles")
                result.added = len(add_roles)
            remove_roles = self._assignable(guild, to_remove)
            if remove_roles:
                await member.remove_roles(*remove_roles, reason="ClashHero autoroles")
                result.removed = len(remove_roles)
                for r in remove_roles:
                    await repo.clear_removal(self.db, guild.id, member.id, r.id)
        except discord.Forbidden:
            log.info("Missing permission to edit roles for %s in %s", member, guild)
        nick = self.nickname_for(member, accounts, ctx)
        if nick and nick != member.nick and member.id != guild.owner_id:
            if member.top_role < guild.me.top_role:
                try:
                    await member.edit(nick=nick, reason="ClashHero nickname")
                    result.nick_changed = True
                except discord.Forbidden:
                    pass
        return result

    async def sync_guild(self, guild: discord.Guild) -> SyncResult:
        ctx = await self.context(guild.id)
        total = SyncResult()
        if not ctx.mappings and not ctx.settings["nick_enabled"]:
            return total
        accounts = await repo.accounts_by_user(self.db)
        for member in guild.members:
            holds_managed = any(r.id in ctx.managed for r in member.roles)
            if member.id not in accounts and not holds_managed:
                continue
            r = await self.sync_member(member, ctx, accounts.get(member.id, []))
            total.added += r.added
            total.removed += r.removed
            total.nick_changed |= r.nick_changed
        return total

    @tasks.loop(minutes=10)
    async def sync_loop(self) -> None:
        for guild in self.bot.guilds:
            try:
                settings = await repo.get_settings(self.db, guild.id)
                if settings["auto_update"]:
                    await self.sync_guild(guild)
            except Exception:
                log.exception("Autorole sync failed for guild %s", guild.id)

    @sync_loop.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_clashhero_account_linked(self, guild_id: int, user_id: int, player_tag: str):
        guild = self.bot.get_guild(guild_id)
        member = guild.get_member(user_id) if guild else None
        if member is None:
            return
        ctx = await self.context(guild_id)
        accounts = (await repo.accounts_by_user(self.db, [user_id])).get(user_id, [])
        await self.sync_member(member, ctx, accounts)

    @commands.Cog.listener()
    async def on_clash_war_state(self, event: WarStateChanged) -> None:
        rows = await repo.guilds_with_war_role(self.db, event.clan_tag)
        if not rows:
            return
        in_war = event.new_state in ("preparation", "inWar")
        lineup_users: set[int] = set()
        if in_war:
            tags = await self.db.fetchall(
                "SELECT player_tag FROM war_members WHERE war_id = ? AND is_opponent = 0",
                (event.war["id"],),
            )
            lineup_users = set((await self.db.links_for_tags(r["player_tag"] for r in tags)).values())
        for row in rows:
            guild = self.bot.get_guild(row["guild_id"])
            role = guild.get_role(row["role_id"]) if guild else None
            if role is None or role >= guild.me.top_role:
                continue
            try:
                for member in list(role.members):
                    if member.id not in lineup_users:
                        await member.remove_roles(role, reason="ClashHero war role")
                for uid in lineup_users:
                    member = guild.get_member(uid)
                    if member and role not in member.roles:
                        await member.add_roles(role, reason="ClashHero war role")
            except discord.Forbidden:
                log.info("Missing permission for war role in %s", guild)

    # ------------------------------------------------------------------ /autorole config

    async def _set(self, interaction, kind, scope, key, role) -> None:
        await repo.set_mapping(self.db, interaction.guild_id, kind, scope, key, role.id if role else None)

    @autorole.command(name="clan-roles", description="Roles by in-game clan role")
    @app_commands.autocomplete(clan=clan_autocomplete)
    @app_commands.describe(
        everyone="Given to every member of the clan",
        only_verified="Only give clan roles for verified accounts (server-wide setting)",
    )
    @manager()
    async def clan_roles(
        self,
        interaction: discord.Interaction,
        clan: str,
        leader: discord.Role | None = None,
        co_leader: discord.Role | None = None,
        elder: discord.Role | None = None,
        member: discord.Role | None = None,
        everyone: discord.Role | None = None,
        only_verified: bool | None = None,
    ) -> None:
        tag = await resolve_clan(interaction, clan)
        given = {
            "leader": leader,
            "coLeader": co_leader,
            "admin": elder,
            "member": member,
            "everyone": everyone,
        }
        for key, role in given.items():
            if role is not None:
                await self._set(interaction, "clan_role", tag, key, role)
        if only_verified is not None:
            await repo.update_settings(
                self.db, interaction.guild_id, only_verified_clan_roles=int(only_verified)
            )
        await interaction.response.send_message(
            embed=ok_embed(f"Clan roles updated for `{tag}`. Run `/autorole refresh` to apply now."),
            ephemeral=True,
        )

    @autorole.command(name="town-hall", description="Role for a Town Hall level (omit role to remove)")
    @manager()
    async def town_hall(
        self,
        interaction: discord.Interaction,
        level: app_commands.Range[int, 1, 20],
        role: discord.Role | None = None,
    ) -> None:
        await self._set(interaction, "town_hall", "", str(level), role)
        await interaction.response.send_message(
            embed=ok_embed(f"TH{level} → {role.mention if role else 'removed'}."), ephemeral=True
        )

    @autorole.command(name="builder-hall", description="Role for a Builder Hall level")
    @manager()
    async def builder_hall(
        self,
        interaction: discord.Interaction,
        level: app_commands.Range[int, 1, 15],
        role: discord.Role | None = None,
    ) -> None:
        await self._set(interaction, "builder_hall", "", str(level), role)
        await interaction.response.send_message(
            embed=ok_embed(f"BH{level} → {role.mention if role else 'removed'}."), ephemeral=True
        )

    async def _league_autocomplete(self, interaction, current: str):
        rows = await self.db.fetchall("SELECT DISTINCT league FROM players WHERE league IS NOT NULL")
        names = sorted({r["league"] for r in rows} | set(DEFAULT_LEAGUES))
        return [app_commands.Choice(name=n, value=n) for n in names if current.lower() in n.lower()][:25]

    async def _builder_league_autocomplete(self, interaction, current: str):
        rows = await self.db.fetchall(
            "SELECT DISTINCT builder_league FROM players WHERE builder_league IS NOT NULL"
        )
        names = sorted(r["builder_league"] for r in rows)
        return [app_commands.Choice(name=n, value=n) for n in names if current.lower() in n.lower()][:25]

    @autorole.command(name="leagues", description="Role for a league, or a Legend trophy band")
    @app_commands.describe(
        league="League name (exact)",
        min_trophies="With max_trophies: a Legend trophy band instead of a league",
    )
    @app_commands.autocomplete(league=_league_autocomplete)
    @manager()
    async def leagues(
        self,
        interaction: discord.Interaction,
        role: discord.Role | None = None,
        league: str | None = None,
        min_trophies: int | None = None,
        max_trophies: int | None = None,
    ) -> None:
        if min_trophies is not None and max_trophies is not None:
            if min_trophies > max_trophies:
                raise ResolveError("min_trophies must be ≤ max_trophies.")
            key = f"{min_trophies}-{max_trophies}"
            await self._set(interaction, "trophy_band", "", key, role)
            label = f"Legend {key} trophies"
        elif league:
            await self._set(interaction, "league", "", league.strip().lower(), role)
            label = league
        else:
            raise ResolveError("Give a league, or both min_trophies and max_trophies.")
        await interaction.response.send_message(
            embed=ok_embed(f"{label} → {role.mention if role else 'removed'}."), ephemeral=True
        )

    @autorole.command(name="builder-leagues", description="Role for a Builder Base league")
    @app_commands.autocomplete(league=_builder_league_autocomplete)
    @manager()
    async def builder_leagues(
        self, interaction: discord.Interaction, league: str, role: discord.Role | None = None
    ) -> None:
        await self._set(interaction, "builder_league", "", league.strip().lower(), role)
        await interaction.response.send_message(
            embed=ok_embed(f"{league} → {role.mention if role else 'removed'}."), ephemeral=True
        )

    @autorole.command(name="family", description="Family / guest / verified / linked / leader roles")
    @app_commands.describe(
        family_role="Any linked account is in a family clan",
        exclusive_family_role="All linked accounts are in family clans",
        guest_role="Linked, but no account in a family clan",
        verified_role="At least one verified account",
        account_linked_role="At least one linked account",
        family_leaders_role="Leader/Co-Leader of a family clan",
    )
    @manager()
    async def family(
        self,
        interaction: discord.Interaction,
        family_role: discord.Role | None = None,
        exclusive_family_role: discord.Role | None = None,
        guest_role: discord.Role | None = None,
        verified_role: discord.Role | None = None,
        account_linked_role: discord.Role | None = None,
        family_leaders_role: discord.Role | None = None,
    ) -> None:
        given = {
            "family": family_role,
            "exclusive": exclusive_family_role,
            "guest": guest_role,
            "verified": verified_role,
            "linked": account_linked_role,
            "leaders": family_leaders_role,
        }
        changed = [k for k, r in given.items() if r is not None]
        for k in changed:
            await self._set(interaction, "family", "", k, given[k])
        await interaction.response.send_message(
            embed=ok_embed(f"Family roles updated: {', '.join(changed)}.")
            if changed
            else fail_embed("Nothing changed. Pick at least one role."),
            ephemeral=True,
        )

    @autorole.command(name="wars", description="Role for a clan's war lineup while the war runs")
    @app_commands.autocomplete(clan=clan_autocomplete)
    @manager()
    async def wars(
        self, interaction: discord.Interaction, clan: str, role: discord.Role | None = None
    ) -> None:
        tag = await resolve_clan(interaction, clan)
        await self._set(interaction, "war", tag, "war", role)
        await interaction.response.send_message(
            embed=ok_embed(f"War role for `{tag}` → {role.mention if role else 'removed'}."), ephemeral=True
        )

    @autorole.command(name="list", description="Show autorole configuration")
    async def list_(self, interaction: discord.Interaction) -> None:
        ctx = await self.context(interaction.guild_id)
        embed = await make_embed(self.bot, interaction.guild_id, title="Autoroles")
        groups: dict[str, list[str]] = {}
        for m in ctx.mappings:
            label = f"{m.scope} " if m.scope else ""
            groups.setdefault(m.kind, []).append(f"{label}{m.key} → <@&{m.role_id}>")
        for kind, lines in groups.items():
            embed.add_field(
                name=f"{KIND_LABEL.get(kind, kind)} ({len(lines)})",
                value="\n".join(lines)[:1024],
                inline=False,
            )
        embed.add_field(name="Settings", value=settings_text(ctx.settings), inline=False)
        if not groups:
            embed.description = "No autoroles configured."
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @autorole.command(name="disable", description="Remove all autoroles of a type")
    @app_commands.choices(kind=KIND_CHOICES)
    @app_commands.autocomplete(clan=clan_autocomplete)
    @manager()
    async def disable(
        self,
        interaction: discord.Interaction,
        kind: app_commands.Choice[str],
        clan: str | None = None,
    ) -> None:
        scope = await resolve_clan(interaction, clan) if clan else None
        n = await repo.delete_kind(self.db, interaction.guild_id, kind.value, scope)
        await interaction.response.send_message(
            embed=ok_embed(
                f"Removed {chip(n)} {kind.name.lower()} mapping(s). "
                "Existing roles stay until you remove them."
            ),
            ephemeral=True,
        )

    @autorole.command(name="refresh", description="Re-apply roles and nicknames now")
    @app_commands.describe(user="One user (default: you); managers can omit to refresh everyone")
    async def refresh(
        self, interaction: discord.Interaction, user: discord.Member | None = None, everyone: bool = False
    ) -> None:
        if everyone:
            await require(interaction, "bot")
            await interaction.response.defer(ephemeral=True)
            r = await self.sync_guild(interaction.guild)
            await interaction.followup.send(embed=ok_embed(sync_text(r.added, r.removed, "everyone")))
            return
        target = user or interaction.user
        if target.id != interaction.user.id:
            await require(interaction, "links")
        await interaction.response.defer(ephemeral=True)
        ctx = await self.context(interaction.guild_id)
        accounts = (await repo.accounts_by_user(self.db, [target.id])).get(target.id, [])
        r = await self.sync_member(target, ctx, accounts)
        await interaction.followup.send(
            embed=ok_embed(sync_text(r.added, r.removed, target.mention, r.nick_changed))
        )

    @autorole.command(name="config", description="Autorole behaviour settings")
    @app_commands.describe(
        auto_update="Re-sync every 10 minutes (off = only on refresh/link)",
        removal_delay_hours="Keep roles this long after they stop applying",
        allow_not_linked="Leave roles of users with no linked accounts untouched",
        allow_non_family="TH/league roles also count accounts outside your clans",
    )
    @manager()
    async def config(
        self,
        interaction: discord.Interaction,
        auto_update: bool | None = None,
        removal_delay_hours: app_commands.Range[int, 0, 720] | None = None,
        allow_not_linked: bool | None = None,
        allow_non_family: bool | None = None,
    ) -> None:
        await repo.update_settings(
            self.db,
            interaction.guild_id,
            auto_update=None if auto_update is None else int(auto_update),
            removal_delay_hours=removal_delay_hours,
            allow_not_linked=None if allow_not_linked is None else int(allow_not_linked),
            allow_non_family=None if allow_non_family is None else int(allow_non_family),
        )
        await interaction.response.send_message(embed=ok_embed("Autorole settings saved."), ephemeral=True)

    # ------------------------------------------------------------------ /nickname

    @nickname.command(
        name="config", description="Nickname formats. Variables: {NAME} {TH} {ROLE} {ALIAS} {CLAN} {DISCORD}"
    )
    @app_commands.describe(
        family_format="For members with a family account, e.g. '{NAME} | {ALIAS}' ('none' to clear)",
        non_family_format="For others; no clan variables ('none' to clear)",
        preference="Which account names the user",
        enabled="Turn automatic nicknames on/off",
    )
    @app_commands.choices(
        preference=[
            app_commands.Choice(name="Default account", value="default"),
            app_commands.Choice(name="Best account (highest TH)", value="best"),
        ]
    )
    @manager()
    async def nickname_config(
        self,
        interaction: discord.Interaction,
        family_format: str | None = None,
        non_family_format: str | None = None,
        preference: app_commands.Choice[str] | None = None,
        enabled: bool | None = None,
    ) -> None:
        updates: dict = {}
        for col, value, fam in (
            ("family_format", family_format, True),
            ("non_family_format", non_family_format, False),
        ):
            if value is None:
                continue
            if value.strip().lower() == "none":
                await self.db.execute(
                    f"UPDATE autorole_settings SET {col} = NULL WHERE guild_id = ?",
                    (interaction.guild_id,),
                )
                continue
            err = repo.validate_format(value, family=fam)
            if err:
                raise ResolveError(err)
            updates[col] = value
        if preference:
            updates["nick_preference"] = preference.value
        if enabled is not None:
            updates["nick_enabled"] = int(enabled)
        await repo.get_settings(self.db, interaction.guild_id)
        await repo.update_settings(self.db, interaction.guild_id, **updates)
        await interaction.response.send_message(
            embed=ok_embed("Nickname settings saved. Use `/nickname preview` to check."), ephemeral=True
        )

    @nickname.command(name="preview", description="Preview the nickname a user would get")
    async def nickname_preview(
        self, interaction: discord.Interaction, user: discord.Member | None = None
    ) -> None:
        target = user or interaction.user
        ctx = await self.context(interaction.guild_id)
        accounts = (await repo.accounts_by_user(self.db, [target.id])).get(target.id, [])
        s = ctx.settings
        enabled = bool(s["nick_enabled"])
        # Preview even when disabled.
        ctx.settings = {**dict(s), "nick_enabled": 1}
        nick = self.nickname_for(target, accounts, ctx)
        status = "" if enabled else "\nℹ️ Nicknames are currently disabled."
        embed = await make_embed(
            self.bot,
            interaction.guild_id,
            title="Nickname preview",
            description=(f"{target.mention} → `{nick}`" if nick else "No nickname would be set.") + status,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(Autoroles(bot))
