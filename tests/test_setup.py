import discord

from clashhero.cogs.setup import setup_list_embeds
from clashhero.utils.fmt import clan_link


def test_setup_list_embeds_one_per_clan_with_badge():
    rows = [
        {
            "clan_tag": "#A",
            "name": "Alpha",
            "badge_url": "https://b/a.png",
            "alias": "AH",
            "category": "Main",
        },
        {"clan_tag": "#B", "name": None, "badge_url": None, "alias": None, "category": None},
    ]
    logs = [
        {"clan_tag": "#A", "log_type": "war_attacks"},
        {"clan_tag": "#A", "log_type": "donations"},
        {"clan_tag": "#A", "log_type": "donations"},  # two channels: listed once
    ]
    live = [{"scope": "#A", "kind": "war_embed"}]
    channels = [{"clan_tag": "#A", "channel_id": 5}]
    server = [{"log_type": "roster_change", "channel_id": 7}, {"log_type": "flag_alert", "channel_id": 8}]
    embeds = setup_list_embeds(rows, logs, live, channels, server, discord.Color.blue())
    a, b, srv = embeds
    assert a.author.name == "Alpha (#A)" and a.author.icon_url == "https://b/a.png"
    assert a.author.url == clan_link("#A")
    assert a.description.split("\n") == [
        "Alias `AH` · Category **Main**",
        "📍 Default clan in <#5>",
        "📜 **2** logs: Donation log, War attack feed",
        "📡 Live: Current war",
    ]
    assert b.author.name == "#B (#B)" and b.author.icon_url is None
    assert b.description == "📜 No logs yet: `/logs clan`"
    assert srv.title == "Server logs"
    assert srv.description == "Flagged player joined → <#8>\nRoster changes → <#7>"


def test_setup_list_without_clans():
    (only,) = setup_list_embeds([], [], [], [], [], None)
    assert only.title == "Server setup" and "/setup clan" in only.description
