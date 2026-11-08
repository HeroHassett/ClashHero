from clashhero.cogs.accounts import account_line, link_list_lines
from clashhero.cogs.setup import parse_hex_color


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
    assert lines[0].startswith("✅ Bob") and "<@42>" in lines[0]
    assert [line[:1] for line in lines[1:]] == ["❌", "❌"]


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
    assert line.startswith("⭐ ✅ [Alice]") and "TH16" in line and "Clan" in line
