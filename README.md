# ClashHero

A Discord bot for Clash of Clans that cuts down the managerial overhead of running clans: clan
management, legend and ranked tracking, war and CWL tools, and battle alerts in one bot.

## Setup

1. Create a Discord application + bot at https://discord.com/developers/applications.
   - Under **Bot → Privileged Gateway Intents**, enable **Server Members Intent** (needed for
     autoroles, nicknames and roster roles).
   - Invite it with the `bot` and `applications.commands` scopes. It needs Manage Roles and
     Manage Nicknames for autoroles/nicknames, plus Send Messages / Embed Links in log channels.
2. Create a Clash of Clans developer account at https://developer.clashofclans.com (this is
   separate from your game account; the bot never signs in to a game account). Then either:
   - put the developer-portal email and password in `COC_EMAIL` / `COC_PASSWORD`, and the bot
     creates an API key for whatever IP it runs on; or
   - create a key yourself, allowing your machine's public IP, and put its token in
     `COC_API_TOKENS`. The key stops working if your IP changes.
3. `cp .env.example .env` and fill it in. Set `DEV_GUILD_ID` to your test server so slash
   commands show up instantly.
4. Install and run:

   ```bash
   uv sync
   uv run clashhero
   ```

5. In Discord: `/setup clan` to add your clan(s), then `/logs clan`, `/live ...`,
   `/autorole ...`, `/reminders create` and so on. `/help` lists everything.

## Features

| Area | Commands | Notes |
| --- | --- | --- |
| Server setup | `/setup`, `/alias`, `/category`, `/config`, `/debug` | Clan aliases, categories, channel default clans, manager roles (bot/roster/links/flags), embed color |
| Accounts | `/link`, `/verify`, `/profile`, `/whois` | Multiple accounts per user, default account, API-token verification, name history |
| Logs | `/logs clan`, `/logs server` | Join/leave (with where members went next), donations, roles, names, TH upgrades, troop/spell/hero/equipment upgrades, super troop boosts, clan settings changes, war preference, leagues, war/CWL attacks, missed attacks, CWL lineup changes, capital contributions/raids/summary, Clan Games, clan achievements, legend battles + daily summary, flag alerts, roster changes, maintenance, welcome |
| Live embeds | `/live`, `/legendboard` | Auto-edited war, CWL, Clan Games, last-seen, clan showcase and legend leaderboard messages |
| Reminders | `/reminders` | War, CWL, raid and Clan Games pings for members with attacks left, filtered by Town Hall, clan role, war type and thresholds; opt-in/opt-out roles |
| To-do & activity | `/todo`, `/inactive` | Everything left to do across a user's accounts; members inactive for N days |
| Scheduled boards | `/autoboard` | Post leaderboards daily, weekly, at season end or as a live message |
| Legends & ranked | `/track`, `/legends`, `/top` | Attack/defense alerts (DM or channel) with opponent, stars, destruction and View/Copy army buttons for Dragon League 28 through Legend I, day/week/season logs, history, clan & family leaderboards, global/local rankings |
| War & CWL | `/war`, `/caller`, `/stats`, `/cwl` | Current war, lineup, remaining attacks, war log, hit-rate and defense stats, war history, target calling, CWL rounds/standings/stars/history |
| Capital & games | `/capital`, `/clangames` | Raid weekends, contributions, Clan Games leaderboard |
| Info | `/clan`, `/player`, `/summary`, `/army`, `/boosts` | Clan & player info, members, compo, donations, last seen, activity graph, units/upgrades/rushed, family-wide summaries, army link parsing |
| Rosters | `/roster` | Rosters with signup buttons, groups, eligibility rules, pings, export; suggested lineups ranked by war and CWL history; automatic signup reminders |
| Bases | `/bases` | Base layout library, posts with copy link + voting, CWL usage tracking per round |
| Moderation | `/flag`, `/autorole`, `/nickname` | Ban flags and weighted, expiring strikes with optional DMs, roles by clan role/TH/league/family/war, nickname templates |
| Exports | `/export` | CSV: members, season stats, wars, attacks, missed, CWL, capital, Clan Games, users |
| Privacy | `/privacy`, `/about` | See or delete your data; links to terms, privacy policy and support |

Mutating commands require **Manage Server** or the relevant manager role from `/config roles`.

### Limits of the official API

- Ranked battles (opponent, stars, destruction, army) come from each player's battle log
  (`/players/{tag}/battlelog`, see `services/battlelog.py`). The log has no per-battle trophy change,
  so the trophy change seen between polls is split across the new battles; when an attack and a
  defense land between the same two polls, the split is approximate. If the battle log can't be
  read, battles are inferred from trophy changes alone. Ranked tiers come from the API's
  `leagueTier` field (see `utils/ranked.py`); weekly tournament group standings aren't available.
- History (wars, donations, Clan Games, activity) only exists from when the bot started tracking.
- Global rankings only cover the top 200 the API returns.

## Hosting

ClashHero needs one always-on machine: a spare computer, a Raspberry Pi or a small VPS is plenty
for a few servers. With `COC_EMAIL`/`COC_PASSWORD` the bot creates an API key for whatever IP it
runs on, so a changing home IP is fine.

With Docker:

```bash
docker compose up -d --build   # build and start in the background, restarting on failure
docker compose logs -f         # follow the logs
```

The database lives in the `clashhero-data` volume; back it up by copying `/app/data/clashhero.db`
out of the container. Without Docker, run `uv run clashhero` under a process manager such as
systemd or launchd.

## Custom emojis

ClashHero uses its own Discord app emojis (shown as "from the ClashHero app"), with plain-text
fallbacks when one isn't uploaded:

- **Official icons** from the Clash API (ranked league tiers, clan and player labels) are uploaded
  automatically the first time the bot starts.
- **Game art** (troops, heroes, pets, spells, equipment, Town Halls) isn't in the API. Download it
  from Supercell's Fan Kit, put the icons in a folder named after each unit (`Barbarian King.png`,
  `Rage Spell.png`, `Town Hall 18.png`), and upload them:

  ```bash
  uv run clashhero-emojis path/to/icons        # or: docker compose exec clashhero clashhero-emojis /path
  ```

  Unit grids, profiles and Town Hall spreads then show the icons automatically.

## Game updates

Clash of Clans changes often (a new Town Hall, new ranked tiers, new troop levels). The bot is
built to keep running through updates and degrade gracefully rather than break:

- Everything it stores is read from the raw API data, so unknown troops, heroes, equipment or
  Town Hall levels don't stop data collection. A player or clan that can't be processed is logged
  once and skipped; everyone else keeps updating.
- Ranked league tiers are loaded from the API at startup, so new tiers are recognised.
- Unknown battle-log types are logged once. If trophies move without a recognised ranked battle,
  the battle is recorded from the trophy change after 30 minutes.
- Starting up during maintenance waits for the API instead of exiting; Docker restarts the bot if
  it ever stops.
- coc.py's bundled game data (max levels per Town Hall) lags behind the game. `/player upgrades`
  falls back to the API's overall max for units it doesn't cover, and `/player rushed` skips them.

After an update (once maintenance ends), run the compatibility check:

```bash
docker compose exec clashhero clashhero-check   # or: uv run clashhero-check
```

It makes a few real API calls and lists anything this version doesn't recognise: new tiers,
battle types, units missing from coc.py's data, or a season schedule change. Then:

1. Watch `docker compose logs -f` for "Skipping ..." or "Unknown ..." warnings.
2. If a newer coc.py is out, update it and rebuild:
   `uv lock --upgrade-package coc.py && docker compose up -d --build`
3. If ranked rules changed (battle caps, tracked tiers), edit `utils/ranked.py`; if the season
   schedule moved, set `SEASON_ANCHOR` in `.env`.
4. Command options that take a Town Hall accept up to TH20.

## Privacy and policies

- `/privacy mydata` and `/privacy delete` let users see and delete everything linked to them.
- Removing the bot from a server deletes that server's data; a daily job applies the retention
  windows in `services/privacy.py`. Keep those in sync with [docs/privacy-policy.md](docs/privacy-policy.md).
- [Terms of Service](docs/terms-of-service.md) and [Privacy Policy](docs/privacy-policy.md) live in
  `docs/`. Set `TERMS_URL`, `PRIVACY_URL` and `SUPPORT_URL` so `/about` links to the hosted copies.
- ClashHero is unofficial fan content under
  [Supercell's Fan Content Policy](https://supercell.com/en/fan-content-policy/): no paid features
  or paywalls, and the required disclaimer is shown in `/help` and `/about`.

## Architecture

```
src/clashhero/
  bot.py                Bot: Discord client, coc.py client, DB, collector; auto-loads cogs
  config.py             .env -> Config
  constants.py          Log types, live message kinds, role names
  events.py             Typed events dispatched by the collector (on_clash_* listeners)
  services/collector.py The only API poller: clans, players, wars, raids -> DB + events
  services/snapshots.py Pure snapshot/diff/legend-inference logic
  services/outbox.py    Sending to log channels, maintaining live messages
  db/schema*.sql        Core tables + one file per feature (auto-loaded)
  db/database.py        Core queries
  repos/                Feature queries and pure calculators
  cogs/                 One module per feature (files starting with _ are helpers)
  utils/                Seasons, tags, permission checks, clan/player resolution, formatting
tests/
```

To add a feature: create `cogs/<feature>.py` subclassing `BaseCog` (auto-discovered), put tables
in `db/schema_<feature>.sql` and queries in `repos/<feature>.py`, and listen to collector events
instead of polling the API yourself.

Season boundaries live in `utils/seasons.py`: 28-day Legend I seasons and weekly ranked
tournaments both reset Monday 05:00 UTC (a season started 2026-11-02), the legend day resets at
05:00 UTC, plus raid weekend and Clan Games windows. Override the cycle with `SEASON_ANCHOR` if
Supercell shifts it.

## Development

```bash
uv run pytest
uv run python -m clashhero.commands_doc   # regenerate docs/commands.md after changing commands
uv run ruff check . && uv run ruff format .
```

---

This material is unofficial and is not endorsed by Supercell. For more information see Supercell's
Fan Content Policy: www.supercell.com/fan-content-policy.
