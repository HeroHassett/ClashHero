"""Live API compatibility check: ``clashhero-check`` (or ``python -m clashhero.healthcheck``).

Run it after a Clash of Clans update (new Town Hall, new ranked tiers, balance changes). It makes a
handful of real API calls with the bot's credentials and checks that everything the bot reads
still parses, reporting anything this version doesn't recognise. It changes nothing.

Exit code 0 = all good, 1 = something needs attention.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from urllib.parse import quote

import coc

from clashhero.config import Config, ConfigError
from clashhero.services.battlelog import KNOWN_TYPES, parse_battles
from clashhero.services.snapshots import ClanSnap, PlayerSnap
from clashhero.utils import ranked
from clashhero.utils.seasons import game_season, ranked_week


class Report:
    def __init__(self) -> None:
        self.problems = 0

    def ok(self, msg: str) -> None:
        print(f"  OK    {msg}")

    def warn(self, msg: str) -> None:
        self.problems += 1
        print(f"  CHECK {msg}")


async def _get(client: coc.Client, path: str) -> dict:
    return await client.http.request(coc.http.Route("GET", client.http.base_url, path))


async def run(cfg: Config) -> int:
    r = Report()
    client = coc.Client(key_names=cfg.coc_key_name, key_count=1, raw_attribute=True)
    try:
        if cfg.uses_api_tokens:
            await client.login_with_tokens(*cfg.coc_api_tokens)
        else:
            await client.login(cfg.coc_email, cfg.coc_password)
        print("Clash of Clans API")
        try:
            await client.search_locations(limit=1)
            r.ok("API reachable and the key works from this IP")
        except coc.Maintenance:
            r.warn("API is in maintenance; run this again once it's over")
            return 1

        print("Ranked league tiers")
        tiers = sorted((await _get(client, "/leaguetiers")).get("items") or [], key=lambda i: i.get("id", 0))
        names = [t.get("name") for t in tiers]
        new = [n for n in names if ranked.tier_number(n) is None]
        gone = [n for n in ranked.LEAGUE_TIERS if n not in names]
        if new or gone:
            r.warn(
                f"tier list changed (new: {new or 'none'}, removed: {gone or 'none'}). The bot loads the "
                "live list at startup; review utils/ranked.py caps and tracked range"
            )
        else:
            r.ok(f"{len(names)} tiers, same as the built-in list")

        print("Players")
        top = await client.get_location_players("global", limit=5)
        tag = top[0].tag
        player = await client.get_player(tag)
        snap = PlayerSnap.from_coc(player)
        r.ok(f"top player parses: TH{snap.town_hall}, {snap.league}, {len(snap.units)} units")
        if not ranked.is_tracked_ranked(snap.league):
            r.warn(f"top player's tier {snap.league!r} isn't tracked; check utils/ranked.py")
        max_th = max(
            [snap.town_hall] + [p.town_hall or 0 for p in [await client.get_player(t.tag) for t in top[1:3]]]
        )
        try:
            from clashhero.cogs._info_embeds import unit_levels

            units = unit_levels(player)
            behind = [u.name for u in units if u.approximate]
            if behind:
                r.warn(
                    f"coc.py's game data is behind the game for {len(behind)}/{len(units)} units "
                    f"({', '.join(behind[:5])}, ...). /player upgrades uses the overall max for them "
                    "and /player rushed skips them; update coc.py when a release catches up"
                )
            else:
                r.ok("coc.py's game data covers every unit (/player units/upgrades/rushed)")
        except Exception as e:  # noqa: BLE001
            r.warn(f"unit level lookup failed: {e!r}")
        print(f"        highest Town Hall among top players: {max_th}")

        print("Battle log")
        log = await _get(client, f"/players/{quote(tag, safe='')}/battlelog")
        battles = parse_battles(log.get("items") or [])
        types = sorted({b.battle_type for b in battles})
        unknown = [t for t in types if t not in KNOWN_TYPES]
        if unknown:
            r.warn(f"unknown battle types {unknown}: decide if they're ranked (services/battlelog.py)")
        else:
            r.ok(f"{len(battles)} battles, types {types}")
        if not any(b.ranked for b in battles):
            r.warn("no ranked battles in the top player's log; ranked battle types may have changed")

        print("Season schedule")
        raw = getattr(player, "_raw_data", {}) or {}
        week_id = raw.get("currentLeagueSeasonId")
        if isinstance(week_id, int):
            api_week = datetime.fromtimestamp(week_id, UTC)
            if api_week == ranked_week().start:
                r.ok(f"weekly tournament matches ({api_week:%Y-%m-%d %H:%M} UTC)")
            else:
                r.warn(
                    f"weekly tournament start {api_week:%Y-%m-%d %H:%M} UTC != bot's "
                    f"{ranked_week().start:%Y-%m-%d %H:%M}; set SEASON_ANCHOR"
                )
        print(f"        current season per the bot: {game_season().id} to {game_season().end:%Y-%m-%d}")

        print("Clans, wars and raids")
        clan_tag = player.clan.tag if player.clan else (await client.search_clans(limit=1))[0].tag
        clan = await client.get_clan(clan_tag)
        cs = ClanSnap.from_coc(clan)
        r.ok(f"clan parses: {len(cs.members)} members, {len(cs.settings)} settings")
        try:
            war = await client.get_current_war(clan_tag)
            r.ok(f"current war parses (state: {getattr(war, 'state', 'none')})")
        except coc.PrivateWarLog:
            r.ok("current war: clan's war log is private (fine)")
        raids = await client.get_raid_log(clan_tag, limit=1)
        r.ok(f"raid log parses ({len(raids)} season)")
    except Exception as e:  # noqa: BLE001
        r.warn(f"unexpected error: {e!r}")
    finally:
        if client.http is not None:
            await client.close()
    print()
    print("All checks passed." if not r.problems else f"{r.problems} thing(s) to check (see CHECK lines).")
    return 1 if r.problems else 0


def main() -> None:
    try:
        cfg = Config.from_env()
    except ConfigError as e:
        sys.exit(f"Configuration error: {e}")
    sys.exit(asyncio.run(run(cfg)))


if __name__ == "__main__":
    main()
