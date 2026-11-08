from clashhero.utils.style import (
    FAILURE,
    SUCCESS,
    bar,
    chip,
    chip_width,
    fail_embed,
    grid,
    header,
    ok_embed,
    stars,
    summary,
)


def test_chips_align():
    w = chip_width(["78%", "100%"])
    assert chip("78%", w) == "` 78%`" and chip("100%", w) == "`100%`"
    assert chip("海神", 6, right=False) == "`海神  `"
    assert chip("a`b") == "`a'b`"


def test_blocks():
    assert stars(2) == "★★☆" and stars(None) == "☆☆☆"
    assert header("Nov 2026", "cwl", "(#15, TH18)") == "🏆 **Nov 2026** (#15, TH18)"
    assert summary("war", "7/7 wars", "21★", None, "700%") == "⚔️ 7/7 wars · 21★ · 700%"
    assert bar(0.5, 4) == "▰▰▱▱"
    assert grid(["a", "b", "c", "d"], 3) == ["a b c", "d"]


def test_result_embeds():
    assert ok_embed("Linked").color == SUCCESS and ok_embed("Linked").description == "✅ Linked"
    assert fail_embed("Nope").color == FAILURE
