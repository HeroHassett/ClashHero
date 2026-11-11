from types import SimpleNamespace as NS

from clashhero.cogs.accounts import account_label, account_line, link_list_lines, linked_post, whois_fields
from clashhero.cogs.setup import clan_linked_text, parse_hex_color
from clashhero.utils.fmt import player_link


def icons(monkeypatch, *names):
    from clashhero.services.emojis import EMOJIS

    monkeypatch.setattr(EMOJIS, "_by_name", {n: f"<:{n}:1>" for n in names})


def test_parse_hex_color():
    assert parse_hex_color("#FF8800") == 0xFF8800
    assert parse_hex_color("0x00ff00") == 0x00FF00
    try:
        parse_hex_color("red")
    except ValueError:
        pass
    else:
        raise AssertionError


def test_link_list_lines_unlinked_last():
    members = [
        {"player_tag": "#A", "name": "Alice"},
        {"player_tag": "#B", "name": "Bob_1"},
        {"player_tag": "#C", "name": "Cat"},
    ]
    lines = link_list_lines(members, {"#B": 42})
    assert lines == [
        "✅ **Linked** (1)",
        "Bob\\_1 (#B) → <@42>",
        "",
        "❌ **Not linked** (2)",
        "Alice (#A)",
        "Cat (#C)",
    ]
    assert link_list_lines(members[:1], {"#A": 1})[0] == "✅ **Linked** (1)"
    assert link_list_lines(members[:1], {})[0] == "❌ **Not linked** (1)"
    assert link_list_lines([], {}) == []


def test_link_list_lines_show_town_hall(monkeypatch):
    icons(monkeypatch, "th16")
    members = [
        {"player_tag": "#A", "name": "Al", "town_hall": 16},
        {"player_tag": "#B", "name": "Bo", "town_hall": 15},
    ]
    assert link_list_lines(members, {"#A": 7})[1:] == [
        "<:th16:1> Al (#A) → <@7>",
        "",
        "❌ **Not linked** (1)",
        "TH15 Bo (#B)",
    ]


def test_account_label(monkeypatch):
    link = player_link("#20LGL8GV0")
    assert account_label("dev", "#20LGL8GV0", 18) == f"TH18 **[dev]({link})** (#20LGL8GV0)"
    assert account_label("d_v", "#20LGL8GV0") == f"**[d\\_v]({link})** (#20LGL8GV0)"
    icons(monkeypatch, "th18")
    assert account_label("dev", "#20LGL8GV0", 18) == f"<:th18:1> **[dev]({link})** (#20LGL8GV0)"


def test_account_line():
    row = {
        "player_tag": "#A",
        "name": "Alice",
        "town_hall": 16,
        "trophies": 5100,
        "clan_tag": "#C",
        "is_default": 1,
        "verified": 1,
    }
    line = account_line(row, {"#C": "Clan"})
    assert line == f"⭐ ✅ TH16 **[Alice]({player_link('#A')})** (#A) · 🏆**5,100** · Clan"
    plain = account_line({**row, "is_default": 0, "verified": 0, "trophies": None, "clan_tag": None}, {})
    assert plain.startswith("TH16 **[Alice]") and plain.endswith("(#A) · no clan")


def test_account_line_uses_game_icons(monkeypatch):
    icons(monkeypatch, "th16", "res_trophy")
    row = {"player_tag": "#A", "name": "Al", "town_hall": 16, "trophies": 5100, "clan_tag": None}
    line = account_line({**row, "is_default": 0, "verified": 0}, {})
    assert line == f"<:th16:1> **[Al]({player_link('#A')})** (#A) · <:res_trophy:1>**5,100** · no clan"
    assert account_line({**row, "town_hall": None, "is_default": 0, "verified": 0}, {}).startswith("TH? ")


def test_whois_fields(monkeypatch):
    icons(monkeypatch, "th16", "lt_legend_i")
    link = {"discord_user_id": 42, "verified": 1, "linked_at": "2026-01-01T00:00:00+00:00"}
    player = {"town_hall": 16, "trophies": 5100, "league": "Legend I", "clan_role": "coLeader"}
    history = [{"name": "Old_one", "first_seen": "2025-01-01T00:00:00+00:00"}]
    fields = whois_fields(link, player, history)
    assert [(name, inline) for name, _, inline in fields] == [
        ("Linked to", False),
        ("Town Hall", True),
        ("Trophies", True),
        ("Role", True),
        ("Name history", False),
    ]
    values = [value for _, value, _ in fields]
    assert values[0].startswith("<@42> ✅ verified\nsince <t:")
    assert values[1:4] == ["<:th16:1>**16**", "<:lt_legend_i:1>**5,100**", "Co-Leader"]
    assert values[4].startswith("Old\\_one · <t:")
    assert whois_fields(None, None, []) == [("Linked to", "Nobody", False)]


def test_linked_post():
    player = NS(
        name="Al_ex", tag="#A", town_hall=18, trophies=5200, _raw_data={"leagueTier": {"name": "Legend I"}}
    )
    title, lines = linked_post(player, "<@7>")
    assert title == "Al\\_ex (#A)"
    assert lines == ["🔗 Linked to <@7>", "TH **18** 🏆**5,200**"]


def test_clan_linked_text_says_what_next():
    text = clan_linked_text("Alpha", "#2PP")
    assert text == "Linked **Alpha** `#2PP` to this server.\nNext: `/logs clan` to pick log channels."
    with_channel = clan_linked_text("Alpha", "#2PP", "<#5>")
    assert "Default clan for <#5>." in with_channel.splitlines()[0]
