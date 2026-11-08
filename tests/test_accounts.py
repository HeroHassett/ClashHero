from clashhero.cogs.accounts import account_label, account_line, link_list_lines
from clashhero.cogs.setup import clan_linked_text, parse_hex_color


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
        {"player_tag": "#B", "name": "Bob"},
        {"player_tag": "#C", "name": "Cat"},
    ]
    lines = link_list_lines(members, {"#B": 42})
    assert lines == [
        "✅ **Linked** `1`",
        "Bob `#B` → <@42>",
        "",
        "❌ **Not linked** `2`",
        "Alice `#A`",
        "Cat `#C`",
    ]
    assert link_list_lines(members[:1], {"#A": 1})[0] == "✅ **Linked** `1`"
    assert link_list_lines(members[:1], {})[0] == "❌ **Not linked** `1`"
    assert link_list_lines([], {}) == []


def test_account_label():
    assert account_label("dev", "#20LGL8GV0", 18) == "**dev** `#20LGL8GV0` (TH18)"
    assert account_label("dev", "#20LGL8GV0") == "**dev** `#20LGL8GV0`"


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
    assert line.startswith("⭐ ✅ [Alice]")
    assert line.endswith("`#A` · `TH16` · 🏆 5,100 · Clan")
    plain = account_line({**row, "is_default": 0, "verified": 0, "trophies": None, "clan_tag": None}, {})
    assert plain.startswith("[Alice]") and plain.endswith("`TH16` · no clan")


def test_clan_linked_text_says_what_next():
    text = clan_linked_text("Alpha", "#2PP")
    assert text == "Linked **Alpha** `#2PP` to this server.\nNext: `/logs clan` to pick log channels."
    with_channel = clan_linked_text("Alpha", "#2PP", "<#5>")
    assert "Default clan for <#5>." in with_channel.splitlines()[0]
