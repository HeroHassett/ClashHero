"""Unit icons for image cards (services/icons.py): local folder first, then the app's emojis."""

import logging

import pytest

from clashhero.services import icons
from clashhero.services.emojis import EMOJIS, EmojiRegistry

CDN = "https://cdn.discordapp.com/emojis/{}.png?size=128"


class E:
    def __init__(self, name, eid):
        self.name, self.id = name, eid

    def __str__(self):
        return f"<:{self.name}:{self.id}>"


class FakeResponse:
    def __init__(self, status=200, body=b"\x89PNG icon", content_length=None):
        self.status = status
        self.body = body
        self.content_length = len(body) if content_length is None else content_length
        self.content = self

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    async def iter_chunked(self, n):
        for i in range(0, len(self.body), n):
            yield self.body[i : i + n]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Stands in for aiohttp.ClientSession: serves ``responses`` by URL, 404 otherwise."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def __call__(self):  # used as the session factory
        return self

    def get(self, url, allow_redirects=True):
        self.calls.append((url, allow_redirects))
        return self.responses.get(url) or FakeResponse(404, b"")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def emojis():
    saved = dict(EMOJIS._by_name)
    EMOJIS.load([E("archer_queen", 111), E("rage_spell", 222), E("l_a_s_s_i", 333)])
    yield EMOJIS
    EMOJIS._by_name = saved


@pytest.fixture
def icon_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(icons.ICON_DIR_ENV, str(tmp_path))
    return tmp_path


def test_emoji_id_accessor():
    r = EmojiRegistry()
    r.load([E("barbarian_king", 42), E("lt_legend_i", 7)])
    assert r.emoji_id("barbarian_king") == 42
    assert r.emoji_id("archer_queen") is None
    r._by_name["animated"] = "<a:animated:99>"
    assert r.emoji_id("animated") == 99


def test_icon_dir_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.delenv(icons.ICON_DIR_ENV, raising=False)
    assert icons.icon_dir() == icons.DEFAULT_ICON_DIR
    assert icons.DEFAULT_ICON_DIR.parts[-2:] == ("game-assets", "icons")
    monkeypatch.setenv(icons.ICON_DIR_ENV, str(tmp_path))
    assert icons.icon_dir() == tmp_path


async def test_local_folder_comes_first(icon_dir, emojis):
    (icon_dir / "archer_queen.png").write_bytes(b"local queen")
    (icon_dir / "l_a_s_s_i.png").write_bytes(b"local lassi")
    session = FakeSession()
    cache = icons.IconCache(session_factory=session)
    got = await cache.fetch(["Archer Queen", "L.A.S.S.I", "Archer Queen", None, ""])
    assert got == {"Archer Queen": b"local queen", "L.A.S.S.I": b"local lassi"}
    assert session.calls == []  # nothing downloaded


async def test_oversized_local_files_are_skipped(icon_dir, monkeypatch):
    monkeypatch.setattr(icons, "MAX_ICON_BYTES", 10)
    (icon_dir / "barbarian.png").write_bytes(b"x" * 11)
    cache = icons.IconCache(session_factory=FakeSession())
    assert await cache.fetch(["Barbarian"]) == {}


async def test_app_emojis_from_discords_cdn(icon_dir, emojis):
    session = FakeSession({CDN.format(111): FakeResponse(body=b"queen png")})
    cache = icons.IconCache(session_factory=session)
    got = await cache.fetch(["Archer Queen", "Barbarian"])  # no Barbarian emoji: left out
    assert got == {"Archer Queen": b"queen png"}
    assert session.calls == [(CDN.format(111), False)]  # https, Discord's CDN, no redirects

    again = await cache.fetch(["Archer Queen"])
    assert again == {"Archer Queen": b"queen png"}
    assert len(session.calls) == 1  # served from the cache


async def test_only_discords_cdn_over_https(icon_dir, emojis, monkeypatch):
    for template in (
        "https://evil.example.com/emojis/{id}.png",
        "http://cdn.discordapp.com/emojis/{id}.png",
        "https://cdn.discordapp.com.evil.example/emojis/{id}.png",
    ):
        monkeypatch.setattr(icons, "EMOJI_URL", template)
        session = FakeSession()
        cache = icons.IconCache(session_factory=session)
        assert await cache.fetch(["Archer Queen", "Rage Spell"]) == {}
        assert session.calls == []
    assert icons.allowed(CDN.format(1))
    assert not icons.allowed("https://media.discordapp.net/emojis/1.png")


async def test_failures_are_not_cached(icon_dir, emojis, caplog):
    session = FakeSession({CDN.format(222): FakeResponse(503, b"")})
    cache = icons.IconCache(session_factory=session)
    with caplog.at_level(logging.DEBUG, logger="clashhero.services.icons"):
        assert await cache.fetch(["Rage Spell"]) == {}
    assert "Rage Spell" not in cache._cache
    assert any(
        "Icon download failed" in r.getMessage() and r.levelno == logging.DEBUG for r in caplog.records
    )

    session.responses[CDN.format(222)] = FakeResponse(body=b"rage png")
    assert await cache.fetch(["Rage Spell"]) == {"Rage Spell": b"rage png"}  # tried again
    assert len(session.calls) == 2


async def test_redirects_and_oversized_downloads_are_refused(icon_dir, emojis, monkeypatch):
    monkeypatch.setattr(icons, "MAX_ICON_BYTES", 100)
    session = FakeSession(
        {
            CDN.format(111): FakeResponse(302, b""),
            CDN.format(222): FakeResponse(body=b"x" * 101, content_length=0),  # no length header
            CDN.format(333): FakeResponse(body=b"x", content_length=5000),
        }
    )
    cache = icons.IconCache(session_factory=session)
    assert await cache.fetch(["Archer Queen", "Rage Spell", "L.A.S.S.I"]) == {}
    assert cache._cache == {}


async def test_session_errors_mean_no_icons(icon_dir, emojis):
    def broken():
        raise OSError("no network")

    cache = icons.IconCache(session_factory=broken)
    assert await cache.fetch(["Archer Queen"]) == {}


async def test_lru_keeps_the_most_recent(icon_dir):
    for name in ("a", "b", "c"):
        (icon_dir / f"{name}_.png").write_bytes(name.encode())
    cache = icons.IconCache(max_entries=2, session_factory=FakeSession())
    await cache.fetch(["a", "b"])
    await cache.fetch(["a"])  # touch a: b is now the oldest
    await cache.fetch(["c"])
    assert list(cache._cache) == ["a", "c"]


async def test_fetch_icons_uses_the_shared_cache(icon_dir, monkeypatch):
    (icon_dir / "healer.png").write_bytes(b"healer png")
    monkeypatch.setattr(icons, "icon_cache", icons.IconCache(session_factory=FakeSession()))
    assert await icons.fetch_icons(["Healer", "Healer"]) == {"Healer": b"healer png"}
    assert "Healer" in icons.icon_cache._cache


async def test_trophy_icon_by_emoji_key(icon_dir, monkeypatch):
    monkeypatch.setattr(icons, "icon_cache", icons.IconCache(session_factory=FakeSession()))
    assert await icons.fetch_trophy_icon() is None
    (icon_dir / "res_trophy.png").write_bytes(b"trophy png")
    assert await icons.fetch_trophy_icon() == b"trophy png"


async def test_trophy_icon_from_the_app_emoji(icon_dir, monkeypatch):
    saved = dict(EMOJIS._by_name)
    try:
        EMOJIS.load([E("res_trophy", 444)])
        session = FakeSession({CDN.format(444): FakeResponse(body=b"emoji trophy")})
        monkeypatch.setattr(icons, "icon_cache", icons.IconCache(session_factory=session))
        assert await icons.fetch_trophy_icon() == b"emoji trophy"
    finally:
        EMOJIS._by_name = saved


def test_font_dir_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.delenv(icons.FONT_DIR_ENV, raising=False)
    assert icons.font_dir() == icons.DEFAULT_FONT_DIR
    assert icons.DEFAULT_FONT_DIR.parts[-2:] == ("game-assets", "fonts")
    monkeypatch.setenv(icons.FONT_DIR_ENV, str(tmp_path))
    assert icons.font_dir() == tmp_path
