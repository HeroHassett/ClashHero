import io

from PIL import Image, ImageDraw

from clashhero.services import cards


def badge_png(color=(40, 90, 200)) -> bytes:
    im = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    ImageDraw.Draw(im).polygon([(100, 8), (184, 40), (176, 130), (100, 192), (24, 130), (16, 40)], fill=color)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def open_png(data: bytes) -> Image.Image:
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


ROWS = [
    cards.CardRow(1, "up", "The Iron Legion", badge_png(), 87, 9123.4, "#IRON"),
    cards.CardRow(2, "down", "龍の騎士団", badge_png((160, 30, 40)), 84, 8970, "#DRAGON"),
    cards.CardRow(
        3, "same", "Supercalifragilistic Warlords of the Frozen North " * 3, None, 79, 8702, "#LONG"
    ),
    cards.CardRow(4, "up", "⚔️ Night Owls ⚔️", None, 71, 8420.6, "#OWLS"),
    cards.CardRow(5, "down", "전설의 용사들", badge_png((120, 60, 170)), 66, 8011, "#KR"),
    cards.CardRow(6, "same", "Pixel Pirates", b"not an image", 58, 7655, "#PIX"),
    cards.CardRow(7, "up", "🔥🔥", None, 49, 7012, "#EMOJI"),
    cards.CardRow(8, "down", "Last Stand", None, 0, 0, "#LAST"),
]


def test_render_full_group_is_a_png_of_the_expected_size():
    data = cards.render_cwl_standings(
        season="November 2026", round_label="4/7", league="Master League II", rows=ROWS, highlight="#IRON"
    )
    im = open_png(data)
    assert im.format == "PNG"
    assert im.size == (cards.WIDTH, cards.card_height(8))
    assert im.width == 1000


def test_render_accepts_tuples_no_league_and_no_highlight():
    rows = [(1, "same", "Alpha", None, 10, 450.0), (2, "same", "Beta", badge_png(), 3, 120.0)]
    im = open_png(
        cards.render_cwl_standings(season="November 2026", round_label="1/7", league=None, rows=rows)
    )
    assert im.size == (1000, cards.card_height(2))


def test_render_without_rows():
    im = open_png(cards.render_cwl_standings(season="November 2026", round_label="1/7", league=None, rows=[]))
    assert im.size == (1000, cards.card_height(0))


def test_highlight_row_is_drawn_differently():
    base = {"season": "November 2026", "round_label": "2/7", "league": "Gold League I", "rows": ROWS[:2]}
    plain = open_png(cards.render_cwl_standings(**base))
    marked = open_png(cards.render_cwl_standings(**base, highlight="#IRON"))
    top = cards.ROWS_TOP
    box = (cards.PAD - 2, top - 2, cards.WIDTH - cards.PAD + 2, top + cards.ROW_H + 2)
    assert plain.crop(box).tobytes() != marked.crop(box).tobytes()
    second = (box[0], box[1] + cards.ROW_H + cards.ROW_GAP, box[2], box[3] + cards.ROW_H + cards.ROW_GAP)
    assert plain.crop(second).tobytes() == marked.crop(second).tobytes()


def test_long_names_are_cut_with_an_ellipsis():
    fonts = cards._text_fonts(22, 600)
    runs = cards.fit_runs("A very long clan name that never ends " * 4, fonts, 400)
    assert runs[-1][0] == "…"
    assert cards._runs_width(runs) <= 400
    assert "".join(s for s, _ in runs).startswith("A very long")


def test_short_names_are_kept_and_unknown_symbols_dropped():
    fonts = cards._text_fonts(22, 600)
    assert "".join(s for s, _ in cards.fit_runs("Night Owls", fonts, 10_000)) == "Night Owls"
    # Emoji have no glyph in the bundled fonts: left out, and the spaces around them collapse.
    assert "".join(s for s, _ in cards.fit_runs("⚔️  Night Owls ⚔️", [fonts[0]], 10_000)) == "Night Owls"


# ------------------------------------------------------------------ badge downloads

URL = "https://api-assets.clashofclans.com/badges/200/abc.png"


async def test_badge_failures_fall_back_to_none(monkeypatch):
    cache = cards.BadgeCache()

    async def boom(self, session, url):
        raise TimeoutError

    monkeypatch.setattr(cards.BadgeCache, "_download", boom)
    assert await cache.fetch([URL, None, ""]) == {URL: None}
    assert URL not in cache._cache  # failures aren't cached; the next call tries again


async def test_badges_are_cached_and_only_fetched_from_the_api_host(monkeypatch):
    cache = cards.BadgeCache(max_entries=2)
    calls = []

    async def ok(self, session, url):
        calls.append(url)
        return b"png:" + url.encode()

    monkeypatch.setattr(cards.BadgeCache, "_download", ok)
    other = "https://example.com/badge.png"
    plain_http = "http://api-assets.clashofclans.com/badges/200/x.png"
    got = await cache.fetch([URL, URL, other, plain_http])
    assert got == {URL: b"png:" + URL.encode(), other: None, plain_http: None}
    assert calls == [URL]
    assert (await cache.fetch([URL]))[URL] == b"png:" + URL.encode()
    assert calls == [URL]  # served from the cache

    more = [f"https://api-assets.clashofclans.com/badges/200/{i}.png" for i in range(3)]
    await cache.fetch(more)
    assert list(cache._cache) == more[1:]  # least recently used dropped


async def test_fetch_badges_uses_the_shared_cache(monkeypatch):
    async def boom(self, session, url):
        raise OSError("offline")

    monkeypatch.setattr(cards.BadgeCache, "_download", boom)
    assert await cards.fetch_badges([URL]) == {URL: None}
