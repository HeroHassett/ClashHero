---
title: ClashHero Commands
---

# ClashHero commands

Type `/` in Discord to use these. Options in [brackets] are optional. Commands that change server
settings need **Manage Server** or the matching manager role set with `/config roles`.

## Server setup

| Command | What it does |
| --- | --- |
| `/alias list` | List clan aliases |
| `/alias remove clan` | Remove a clan's alias |
| `/alias set clan alias` | Give a clan a short alias (e.g. AH2) |
| `/category list` | Show clans grouped by category |
| `/category set clan [category] [order]` | Put a clan in a category (leave empty to clear) |
| `/config color color` | Set the embed color (hex, e.g. #FF8800) |
| `/config roles [bot_manager] [roster_manager] [links_manager] [flags_manager] [clear]` | Set manager roles (omit to keep; use clear to unset) |
| `/config show` | Show current bot configuration |
| `/debug` | Check the bot's permissions in this channel |
| `/setup channel [clan] [channel]` | Set or clear a channel's default clan |
| `/setup clan clan_tag [channel]` | Link a clan to this server |
| `/setup list` | Show linked clans and enabled features |
| `/setup remove-clan clan` | Unlink a clan and all its features |

## Accounts

| Command | What it does |
| --- | --- |
| `/link create player_tag [user] [is_default]` | Link a Clash account (to you or, for managers, a user) |
| `/link default player_tag` | Choose your default account |
| `/link delete player_tag` | Unlink a Clash account |
| `/link list [clan]` | Show which clan members are linked |
| `/profile [user]` | Show a user's linked accounts |
| `/verify player_tag token` | Verify you own an account with its API token |
| `/whois player_tag` | Who owns a Clash account, plus its name history |

## Logs

| Command | What it does |
| --- | --- |
| `/logs clan clan log_type [channel] [ping_role] [color] [disable]` | Enable or disable a clan log in a channel |
| `/logs list` | Show configured logs |
| `/logs server log_type [channel] [disable]` | Enable or disable a server-wide log |

## Live messages

| Command | What it does |
| --- | --- |
| `/live clan [clan] [channel]` | Live clan showcase embed |
| `/live clan-games [clan] [channel]` | Live Clan Games leaderboard |
| `/live cwl [clan] [channel]` | Live embed of the clan's current CWL round |
| `/live last-seen [clan] [channel]` | Live last-seen / activity board |
| `/live list` | List live embeds in this server |
| `/live remove kind [clan]` | Stop a live embed |
| `/live war [clan] [channel]` | Live embed of the clan's current war |

## Reminders

| Command | What it does |
| --- | --- |
| `/reminders config type [role] [mode]` | Limit who gets pinged with a role |
| `/reminders create type duration message [clans] [channel] [exclude_participant_list] [min_th] [max_th] [clan_roles] [war_type] [games_below] [raid_min_left]` | Ping members who still have attacks before an event ends |
| `/reminders delete id` | Delete a reminder |
| `/reminders edit id [duration] [message] [channel] [clans] [min_th] [max_th] [clan_roles] [war_type] [games_below] [raid_min_left]` | Edit a reminder (0 or "any" clears a filter) |
| `/reminders list [type]` | List reminders |
| `/reminders now type [message] [clans]` | Ping members with remaining attacks right now |

## To-do and activity

| Command | What it does |
| --- | --- |
| `/inactive [clan] [days] [ping]` | Clan members who haven't been seen for a while |
| `/todo [user]` | What your linked accounts still have to do right now |

## Scheduled boards

| Command | What it does |
| --- | --- |
| `/autoboard create board schedule [channel] [clans] [at] [weekday] [interval]` | Post a board on a schedule or keep it live (Bot Manager) |
| `/autoboard delete board_id` | Stop and remove a board (Bot Manager) |
| `/autoboard list` | Scheduled and live boards in this server |
| `/autoboard post-now board_id` | Post (or refresh, if live) a board right now (Bot Manager) |

## Legends and ranked

| Command | What it does |
| --- | --- |
| `/legendboard add player` | Add a player to the leaderboard |
| `/legendboard disable` | Stop updating the live leaderboard |
| `/legendboard import clan [mode]` | Add a clan's ranked members (Dragon 28+) to the leaderboard |
| `/legendboard list` | Players on the leaderboard |
| `/legendboard post` | Refresh (or re-post) the live leaderboard now |
| `/legendboard remove player` | Remove a player from the leaderboard |
| `/legendboard setchannel channel` | Post the live legend leaderboard in a channel |
| `/legends clan [clan]` | Today's legend leaderboard for a clan's members |
| `/legends day [player] [user] [day_offset]` | A player's ranked attacks and defenses for a legend day |
| `/legends history [player] [user]` | A player's legend results across seasons |
| `/legends leaderboard [clans]` | Family-wide legend ranking across this server's clans |
| `/legends logs [player] [user] [season]` | Day-by-day legend stats for a season |
| `/legends week [player] [user] [weeks_ago]` | A player's ranked week (weekly tournaments) |
| `/top clans [location]` | Top clans globally or in a country |
| `/top players [location]` | Top players globally or in a country |
| `/top today mode [min_battles]` | Best offense or defense today among tracked players |
| `/track add player [destination] [channel] [attacks] [defenses] [ping_role]` | Get an alert after every ranked attack/defense of a player |
| `/track edit player [attacks] [defenses] [channel] [ping_role] [clear_ping]` | Change alerts for a player |
| `/track list` | List your alerts (managers also see this server's) |
| `/track remove player` | Stop alerts for a player |

## War

| Command | What it does |
| --- | --- |
| `/caller assign position [player] [note] [clan]` | Claim an enemy base in the current war |
| `/caller clear [position] [clan]` | Remove a claim (or all claims) in the current war |
| `/caller list [clan]` | Show claimed bases in the current war |
| `/stats attacks [clans] [period] [war_type] [town_hall] [min_attacks]` | War hit rates per player (3★ %, avg stars, missed) |
| `/stats defense [clans] [period] [war_type] [town_hall]` | War defense hold rates per player |
| `/war current [clan]` | Score, timers and TH matchup of the current war |
| `/war history [player] [user] [period] [war_type]` | A player's war performance (hit rate, 3★%, defense) |
| `/war lineup [clan]` | Both lineups by map position with stars on each base |
| `/war log [clan] [count]` | Recent war results from the in-game war log |
| `/war remaining [clan] [mention]` | Members who still have war attacks left |

## Clan War League

| Command | What it does |
| --- | --- |
| `/cwl attacks [clan] [season]` | Season participation: attacks used and missed per member |
| `/cwl history [player] [user] [season]` | A player's round-by-round CWL attacks and defenses |
| `/cwl lineup [clan] [round]` | Both lineups for a CWL round |
| `/cwl members [clan]` | Town Hall spread of every clan in the CWL group |
| `/cwl round [clan] [round]` | Score and timers for a CWL round |
| `/cwl stars [clan] [season]` | Season star leaderboard for the clan's CWL |
| `/cwl stats [clan]` | Group standings (stars incl. +10 per win, destruction) |

## Clan Capital

| Command | What it does |
| --- | --- |
| `/capital contributions [clan] [season]` | Capital gold contributed per member this season |
| `/capital history [player] [user]` | A player's raid weekends (attacks and loot) |
| `/capital raids [clan] [weeks_back]` | Raid weekend summary with per-member attacks and loot |

## Clan Games

| Command | What it does |
| --- | --- |
| `/clangames [clan] [month] [cap] [show_missing]` | Clan Games points leaderboard |

## Clan and player info

| Command | What it does |
| --- | --- |
| `/clan activity [clan]` | Hourly activity graph over the last 7 days |
| `/clan compo [clan]` | Town Hall composition of a clan |
| `/clan donations [clan] [season]` | Season donation leaderboard for a clan |
| `/clan history [player] [user]` | A player's join/leave history across tracked clans |
| `/clan info [clan]` | Clan overview: leagues, war record, requirements, TH spread |
| `/clan lastseen [clan]` | When each member was last active (estimated) |
| `/clan members [clan] [view]` | Member list with a chosen view |
| `/player info [player] [user]` | Player profile, linked user, name history and activity |
| `/player rushed [player] [user]` | Units below the previous Town Hall's max levels |
| `/player units [player] [user]` | Troop, spell, hero and pet levels vs max for the TH |
| `/player upgrades [player] [user]` | Remaining upgrades for the current Town Hall |

## Family summaries

| Command | What it does |
| --- | --- |
| `/summary activity [season] [clans]` | Most active players this season |
| `/summary best [season] [clans]` | Top performers this season across categories |
| `/summary capital [clans]` | Last raid weekend results per clan |
| `/summary clan-games [clans]` | Clan Games points per clan and top scorers |
| `/summary cwl-status [clans]` | CWL league and current status for every clan |
| `/summary donations [season] [clans]` | Season donations per clan and top donors |
| `/summary missed-wars [season] [clans]` | Missed war attacks this season, per clan and player |
| `/summary trophies [clans]` | Trophy totals per clan and top players |
| `/summary war-results [season] [clans]` | Season war wins/losses per clan |
| `/summary wars [clans]` | Current war status for every clan |

## Super troop boosts

| Command | What it does |
| --- | --- |
| `/boosts [clan]` | Show which clan members have super troops boosted |

## Armies and help

| Command | What it does |
| --- | --- |
| `/army link link [name]` | Show the troops, spells, heroes and CC in an army link |
| `/help` | Overview of every ClashHero command |

## Rosters

| Command | What it does |
| --- | --- |
| `/roster autoping off roster` | Stop automatic pings for a roster |
| `/roster autoping set roster [hours_before_end] [hours_before_start] [message] [channel]` | Ping unregistered clan members before a roster starts or closes |
| `/roster clear roster` | Remove every signup from a roster |
| `/roster clone roster new_name` | Copy a roster's settings and groups (not members) |
| `/roster close roster` | Lock a roster (no self signups) |
| `/roster create clan name [category] [max_members] [min_th] [max_th] [min_heroes] [max_accounts_per_user] [allow_unlinked] [allow_multi_signup] [allow_group_selection] [roster_role] [sort_by] [start_time] [end_time] [color] [import_members]` | Create a roster |
| `/roster delete roster` | Delete a roster and its signups |
| `/roster edit roster [name] [clan] [category] [max_members] [min_th] [max_th] [min_heroes] [max_accounts_per_user] [allow_unlinked] [allow_multi_signup] [allow_group_selection] [roster_role] [remove_role] [sort_by] [start_time] [end_time] [color]` | Change a roster's settings |
| `/roster export roster` | Download a roster as CSV |
| `/roster groups create roster name` | Add a group to a roster |
| `/roster groups delete roster group` | Delete a roster group (its members become ungrouped) |
| `/roster groups list roster` | List a roster's groups |
| `/roster groups rename roster group new_name` | Rename a roster group |
| `/roster list [clan] [category]` | List rosters in this server |
| `/roster manage add roster [tags] [user] [group]` | Add players to a roster (tags in any format, or a user's accounts) |
| `/roster manage move roster tags [to_roster] [group]` | Move players to another roster and/or group |
| `/roster manage remove roster [tags] [user]` | Remove players from a roster |
| `/roster open roster` | Unlock a roster |
| `/roster optout roster account` | Remove one of your accounts from a roster |
| `/roster ping roster [ping_option] [group] [message]` | Ping roster members (asks for confirmation) |
| `/roster post roster [channel]` | Post a roster with Signup / Opt-out buttons (kept up to date) |
| `/roster show roster` | Show a roster |
| `/roster signup roster account [group]` | Sign one of your linked accounts up for a roster |
| `/roster suggest roster [size] [substitutes] [cwl_seasons] [apply]` | Rank eligible players by war history, activity and TH/heroes |

## Bases

| Command | What it does |
| --- | --- |
| `/bases add name town_hall [type] [link] [image] [notes]` | Register a base layout |
| `/bases delete name` | Delete a base layout and its usage history |
| `/bases edit name [new_name] [town_hall] [type] [link] [image] [notes]` | Edit a base layout |
| `/bases list [town_hall] [type] [unused_only]` | List bases and how often each was used this CWL |
| `/bases post name [channel]` | Post a base card with a copy-link button |
| `/bases unuse name [war_round]` | Undo the latest usage of a base this CWL |
| `/bases usage` | Which bases were used in which CWL rounds, and by whom |
| `/bases use name [war_round] [player]` | Mark a base as used in this month's CWL |
| `/bases voting enabled` | Turn vote reactions on base posts on or off |

## Bans and strikes

| Command | What it does |
| --- | --- |
| `/flag create flag_type player [reason] [expiry_days] [weight] [dm]` | Flag a player (Flags Manager) |
| `/flag delete flag_id` | Delete a flag by id (Flags Manager) |
| `/flag list [flag_type] [player] [clans] [include_expired] [min_strike_weight]` | List active flags |
| `/flag summary [clans] [min_strike_weight]` | Players' active strike totals, highest first |

## Autoroles and nicknames

| Command | What it does |
| --- | --- |
| `/autorole builder-hall level [role]` | Role for a Builder Hall level |
| `/autorole builder-leagues league [role]` | Role for a Builder Base league |
| `/autorole clan-roles clan [leader] [co_leader] [elder] [member] [everyone] [only_verified]` | Roles by in-game clan role |
| `/autorole config [auto_update] [removal_delay_hours] [allow_not_linked] [allow_non_family]` | Autorole behaviour settings |
| `/autorole disable kind [clan]` | Remove all autoroles of a type |
| `/autorole family [family_role] [exclusive_family_role] [guest_role] [verified_role] [account_linked_role] [family_leaders_role]` | Family / guest / verified / linked / leader roles |
| `/autorole leagues [role] [league] [min_trophies] [max_trophies]` | Role for a league, or a Legend trophy band |
| `/autorole list` | Show autorole configuration |
| `/autorole refresh [user] [everyone]` | Re-apply roles and nicknames now |
| `/autorole town-hall level [role]` | Role for a Town Hall level (omit role to remove) |
| `/autorole wars clan [role]` | Role for a clan's war lineup while the war runs |
| `/nickname config [family_format] [non_family_format] [preference] [enabled]` | Nickname formats. Variables: {NAME} {TH} {ROLE} {ALIAS} {CLAN} {DISCORD} |
| `/nickname preview [user]` | Preview the nickname a user would get |

## Exports

| Command | What it does |
| --- | --- |
| `/export attacks [clans] [season]` | Every war attack made by your clans in the season |
| `/export capital [clans] [season]` | Capital raid participation (season) |
| `/export clangames [clans] [season]` | Clan Games points (this month) |
| `/export cwl [clans] [season]` | CWL attacks in the season |
| `/export members [clans] [season]` | Current members of your clans |
| `/export missed [clans] [season]` | Missed war attacks in the season |
| `/export season [clans] [season]` | Seasonal member stats (donations, capital gold, activity) |
| `/export users [clans] [season]` | Discord users and their linked accounts |
| `/export wars [clans] [season]` | Wars in the season with results |

## Privacy and about

| Command | What it does |
| --- | --- |
| `/about` | About ClashHero, its terms and privacy policy |
| `/privacy delete` | Delete all data linked to your Discord account |
| `/privacy mydata` | See what ClashHero stores that is linked to you |


---

This page is generated from the bot's code. This material is unofficial and is not endorsed by
Supercell. For more information see Supercell's Fan Content Policy:
[www.supercell.com/fan-content-policy](https://www.supercell.com/fan-content-policy).
