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
| Logs | `/logs clan`, `/logs server` | Join/leave, donations, roles, names, TH upgrades, war preference, leagues, war/CWL attacks, missed attacks, CWL lineup changes, capital contributions/raids/summary, Clan Games, clan achievements, legend battles + daily summary, flag alerts, roster changes, maintenance, welcome |
| Live embeds | `/live`, `/legendboard` | Auto-edited war, CWL, Clan Games, last-seen, clan showcase and legend leaderboard messages |
| Reminders | `/reminders` | War, CWL, raid and Clan Games pings for members with attacks left; opt-in/opt-out roles |
| Legends & ranked | `/track`, `/legends`, `/top` | Attack/defense alerts (DM or channel) for Dragon League 28 through Legend I, day/week/season logs, history, clan & family leaderboards, global/local rankings |
| War & CWL | `/war`, `/caller`, `/stats`, `/cwl` | Current war, lineup, remaining attacks, war log, hit-rate and defense stats, war history, target calling, CWL rounds/standings/stars/history |
| Capital & games | `/capital`, `/clangames` | Raid weekends, contributions, Clan Games leaderboard |
| Info | `/clan`, `/player`, `/summary`, `/army` | Clan & player info, members, compo, donations, last seen, activity graph, units/upgrades/rushed, family-wide summaries, army link parsing |
| Rosters | `/roster` | Rosters with signup buttons, groups, eligibility rules, pings, export |
| Bases | `/bases` | Base layout library, posts with copy link + voting, CWL usage tracking per round |
| Moderation | `/flag`, `/autorole`, `/nickname` | Ban/strike flags, roles by clan role/TH/league/family/war, nickname templates |
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
uv run ruff check . && uv run ruff format .
```

---

This material is unofficial and is not endorsed by Supercell. For more information see Supercell's
Fan Content Policy: www.supercell.com/fan-content-policy.
