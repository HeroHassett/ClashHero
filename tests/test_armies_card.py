"""The legend armies image card (services/cards.render_legend_armies)."""

import io
from datetime import UTC, date, datetime

import pytest
from PIL import Image

from clashhero.services import cards
from clashhero.services.army import parse_army

# Real battle-log codes (Nov 2026).
DEFENSE = (
    "h0p10e32_14-1p16e16_48-2p7e4_19-7p4e57_53i1x64-1x147d1x53-1x98"
    "u2x1-2x58-5x7-3x28-1x23-4x5-5x80-1x97-2x12-1x135-1x82-1x6s1x10-3x35-1x120-2x2-1x5-1x9"
)
DRAGONS = (
    "h1p9e17_48-2m1p16e34_4-6p17e49_43-7p4e52_60i1x188-1x63-1x76d1x2-1x70"
    "u6x57-1x63-5x65-8x8-2x10s3x109-2x98-1x9-2x120-1x5"
)
ROOTS = "h0p10e32_14-1p16e16_48-2p7e4_19-6p11e43_49i3x110d1x70u10x110-4x123-3x95-1x97s5x70-2x98-1x10-1x9"
NOW = datetime(2026, 11, 8, 4, 30, tzinfo=UTC)


def png(color=(220, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (128, 128), (*color, 255)).save(buf, "PNG")
    return buf.getvalue()


def open_png(data: bytes) -> Image.Image:
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


def battle(code=DEFENSE, trophies=-32, **kw) -> cards.ArmyBattle:
    base = dict(
        opponent="Hoyalouis",
        opponent_th=17,
        occurred_at="2026-11-07T10:00:00+00:00",
        stars=2,
        destruction=87,
        army=parse_army(code),
    )
    return cards.ArmyBattle(trophies=trophies, **{**base, **kw})


def render(battles, *, kind="defense", icons=None, name="Sir Alex", trophies=5421):
    return cards.render_legend_armies(
        kind=kind,
        player_name=name,
        player_tag="#2PQ8VLYC0",
        day="2026-11-07",
        trophies=trophies,
        total=sum(b.trophies for b in battles),
        count=f"{len(battles)}/8",
        battles=battles,
        icons=icons,
        now=NOW,
    )


def test_render_is_a_png_of_the_expected_size():
    battles = [battle(c) for c in (DEFENSE, DRAGONS, ROOTS, DEFENSE, DRAGONS, ROOTS, DEFENSE, DRAGONS)]
    im = open_png(render(battles, icons={"Barbarian King": png(), "Healer": png((40, 200, 90))}))
    assert im.format == "PNG"
    assert im.size == (cards.WIDTH, cards.armies_card_height(battles))
    assert im.width == 1000


def test_height_grows_with_the_battle_count():
    heights = [cards.armies_card_height([battle()] * n) for n in range(5)]
    assert heights == sorted(set(heights))  # strictly increasing
    for n in (1, 3):
        assert open_png(render([battle()] * n)).height == heights[n]


def test_attack_side_renders():
    im = open_png(render([battle(DRAGONS, trophies=40, stars=3, destruction=100)], kind="attack"))
    assert im.size == (1000, cards.armies_card_height([battle(DRAGONS)]))


def _tile_box(line: int, index: int, size: int = cards.TILE) -> tuple[int, int, int, int]:
    """Where the ``index``-th tile of icon ``line`` sits in a one-battle card (1x pixels)."""
    lines = cards.army_lines(parse_army("u3x999-2x0"))
    x, block = lines[line][index]
    left = cards.CONTENT_L + round(x)
    top = (
        cards.ARMY_TOP + cards.BODY_TOP + line * (cards.LINE_H + cards.LINE_GAP) + (cards.LINE_H - size) // 2
    )
    return left + 4, top + 4, left + size - 4, top + size - 4


def test_missing_and_unknown_units_get_placeholder_tiles():
    # One unknown troop (id 999, shown as "?") and Barbarians.
    b = [battle("u3x999-2x0", opponent="Test")]
    with_icon = open_png(render(b, icons={"Barbarian": png()})).convert("RGB")
    without = open_png(render(b, icons={})).convert("RGB")

    unknown, barbarian = _tile_box(0, 0), _tile_box(0, 1)
    # The unknown unit is the same "?" tile either way, and not empty background.
    assert with_icon.crop(unknown).tobytes() == without.crop(unknown).tobytes()
    assert len(without.crop(unknown).getcolors(maxcolors=100_000)) > 4
    # Barbarian: the icon when there is one, otherwise a placeholder with its initials.
    assert with_icon.crop(barbarian).tobytes() != without.crop(barbarian).tobytes()
    r, g, b_ = with_icon.getpixel(((barbarian[0] + barbarian[2]) // 2, barbarian[1] + 6))
    assert r > 180 and g < 80 and b_ < 80  # the red test icon
    r, g, b_ = without.getpixel(((barbarian[0] + barbarian[2]) // 2, barbarian[1] + 6))
    assert r < 120  # a dark placeholder tile


def test_undecodable_icons_fall_back_to_placeholders():
    b = [battle("u2x0", opponent="Test")]
    assert render(b, icons={"Barbarian": b"not a png"}) == render(b, icons={})


def test_initials():
    assert cards.initials("Super Bowler") == "SB"
    assert cards.initials("Healer") == "He"
    assert cards.initials("Rage Spell") == "Ra"
    assert cards.initials("Bag of Frostmites") == "BO"
    assert cards.initials("P.E.K.K.A") == "PE"
    assert cards.initials("") == "?"


def test_autofill_no_army_and_inferred_battles_are_compact():
    battles = [
        cards.ArmyBattle(trophies=-31, occurred_at="2026-11-08T04:59:59+00:00", autofill=True),
        battle(army=None),  # from the battle log, but no army code
        cards.ArmyBattle(trophies=-28, occurred_at="2026-11-07T07:00:00+00:00"),  # inferred
        battle(army=parse_army("garbage")),  # an unreadable code is treated as no army
    ]
    assert all(cards.army_lines(b.army) == [] or b.autofill for b in battles)
    expected = cards.ARMY_TOP + 4 * cards.COMPACT_H + 3 * cards.PANEL_GAP + cards.BOTTOM_PAD
    assert cards.armies_card_height(battles) == expected
    assert open_png(render(battles)).size == (1000, expected)


def test_autofill_ignores_any_army():
    autofill = cards.ArmyBattle(trophies=-31, army=parse_army(DEFENSE), autofill=True)
    assert cards.armies_card_height([autofill]) == cards.armies_card_height([cards.ArmyBattle(-31)])


@pytest.mark.parametrize("kind", ["defense", "attack"])
def test_zero_battles(kind):
    im = open_png(render([], kind=kind, trophies=None))
    assert im.size == (1000, cards.ARMY_TOP + cards.COMPACT_H + cards.BOTTOM_PAD)


def test_cjk_and_long_opponent_names():
    b = [
        battle(opponent="ドラゴン使い"),
        battle(opponent="전설의 용사"),
        battle(opponent="Mr. Supercalifragilistic Expialidocious Raider " * 3),
        battle(opponent="⚡🔥"),  # nothing drawable: shown as "?"
        battle(opponent=None),
    ]
    im = open_png(render(b, name="龍の騎士団 with a very long player name indeed"))
    assert im.size == (1000, cards.armies_card_height(b))

    fonts = cards._text_fonts(21, 600)
    runs = cards.fit_runs("Mr. Supercalifragilistic Expialidocious Raider " * 3, fonts, cards._px(380))
    assert runs[-1][0] == "…" and cards._runs_width(runs) <= cards._px(380)
    if not any(cards._has_glyph(f, "ド") for f in fonts):
        pytest.skip("no CJK font on this host")
    assert "".join(s for s, _ in cards.fit_runs("ドラゴン使い", fonts, 10_000)) == "ドラゴン使い"
    long = cards.fit_runs("龍" * 60, fonts, cards._px(300))
    assert long[-1][0] == "…" and long[0][1] is not fonts[0]  # drawn with a CJK fallback font


def test_large_armies_wrap_and_split_the_clan_castle():
    troops = "-".join(f"2x{i}" for i in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 17, 22, 23))
    cc = "i1x64-1x147-2x65-1x8-1x63-1x76-1x110d1x2-1x70-1x98"
    hero_code = "h0p10e32_14-1p16e16_48-2p7e4_19-4p2e7_6-6p17e49_43-7p4e52_60"
    army = parse_army(f"{hero_code}{cc}u{troops}s1x0-1x1-1x2-1x3-1x5-1x9-1x10-1x11")
    lines = cards.army_lines(army)
    assert len(lines) >= 5
    for line in lines:
        assert all(x >= 0 and x + block.width <= cards.CONTENT_W + 0.01 for x, block in line)
    cc_parts = [block for line in lines for _, block in line if block.style == "cc"]
    assert len(cc_parts) == 2
    assert cc_parts[0].label == "CC" and cc_parts[1].label is None
    assert sum(len(p.tiles) for p in cc_parts) == 10
    heroes = [block for line in lines for _, block in line if block.style == "hero"]
    assert [len(h.tiles) for h in heroes] == [4] * 6
    im = open_png(render([battle(army=army)]))
    assert im.height == cards.armies_card_height([battle(army=army)])


def test_clan_castle_sits_flush_right_next_to_spells():
    lines = cards.army_lines(parse_army(DEFENSE))
    assert len(lines) == 3
    spells_line = lines[1]
    x, cc = spells_line[-1]
    assert cc.style == "cc" and cc.label == "CC"
    assert x + cc.width == pytest.approx(cards.CONTENT_W)
    assert [t.unit.name for t in cc.tiles] == ["Super Valkyrie", "Super Yeti", "Recall Spell", "Revive Spell"]


def test_only_the_latest_battles_are_drawn():
    many = [battle(trophies=-i) for i in range(cards.MAX_CARD_BATTLES + 3)]
    shown = many[-cards.MAX_CARD_BATTLES :]
    assert cards.armies_card_height(many) == cards.armies_card_height(shown) + cards.NOTE_H


def test_small_helpers():
    assert cards._signed(-32) == "−32" and cards._signed(12) == "+12" and cards._signed(0) == "0"
    assert cards._day_label("2026-11-07") == "Sat 7 Nov 2026"
    assert cards._day_label(date(2026, 1, 5)) == "Mon 5 Jan 2026"
    assert cards._day_label("soon") == "soon"
    assert cards._ago("2026-11-07T11:52:00+00:00", NOW) == "16h 38m ago"
    assert cards._ago(datetime(2026, 11, 8, 4, 0), NOW) == "30m ago"  # naive means UTC
    assert cards._ago("2026-11-05T00:00:00+00:00", NOW) == "3d 4h ago"
    assert cards._ago("2026-11-08T04:30:30+00:00", NOW) == "just now"
    assert cards._ago(None, NOW) is None and cards._ago("bad", NOW) is None
