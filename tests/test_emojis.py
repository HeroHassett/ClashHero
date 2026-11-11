import io
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from PIL import Image

from clashhero.cogs import _info_embeds as info
from clashhero.services import emojis
from clashhero.services.emojis import EMOJIS, EmojiRegistry, emoji_image, file_key, slug


class E:
    def __init__(self, name, eid=1):
        self.name, self.id = name, eid

    def __str__(self):
        return f"<:{self.name}:{self.id}>"


@pytest.fixture
def registry():
    saved, loaded = dict(EMOJIS._by_name), EMOJIS.loaded
    yield EMOJIS
    EMOJIS._by_name, EMOJIS.loaded = saved, loaded


def test_names():
    assert slug("Barbarian King") == "barbarian_king"
    assert slug("L.A.S.S.I") == "l_a_s_s_i"
    assert slug("P.E.K.K.A League 22") == "p_e_k_k_a_league_22"
    assert len(slug("x" * 40)) == 32 and slug("!") == "x_"
    assert file_key(Path("Town Hall 18.png")) == "th18"
    assert file_key(Path("th7.webp")) == "th7"
    assert file_key(Path("Archer Queen.png")) == "archer_queen"


def test_registry_lookups():
    r = EmojiRegistry()
    r.load([E("lt_legend_i"), E("lbl_clan_wars"), E("barbarian_king"), E("th18")])
    assert r.league("Legend I") == "<:lt_legend_i:1"[:-1] + "1>"
    assert r.label("Clan Wars").startswith("<:lbl_clan_wars:")
    assert r.unit("Barbarian King").startswith("<:barbarian_king:")
    assert r.town_hall(18).startswith("<:th18:")
    assert r.league("Electro League 32") == "" and r.unit(None, "?") == "?"


def test_emoji_image_is_square_png():
    src = io.BytesIO()
    Image.new("RGB", (300, 120), (200, 30, 30)).save(src, "PNG")
    with Image.open(io.BytesIO(emoji_image(src.getvalue()))) as out:
        assert out.size == (emojis.EMOJI_SIZE, emojis.EMOJI_SIZE) and out.mode == "RGBA"


def test_unit_grid_uses_icons_only_when_complete(registry):
    cells = [("Barbarian", "13/13", "✓"), ("Archer", "12/13", "")]
    registry.load([E("barbarian")])
    assert "`Barbarian" in info.chip_grid(cells)[0]  # one icon missing: keep the text grid
    registry.load([E("barbarian"), E("archer")])
    row = info.chip_grid(cells)[0]
    assert row.startswith("<:barbarian:1>`13/13✓`") and "<:archer:1>` 12/13`" in row


def test_th_spread_and_profile_icons(registry):
    registry.load([E("th18")])
    assert info.render_th_distribution([(18, 3), (17, 1)]).startswith("<:th18:1>`TH18`")
    assert "\n`TH17`" in info.render_th_distribution([(18, 3), (17, 1)])


async def test_sync_loads_existing_and_skips_present(registry, monkeypatch):
    bot = NS()

    async def fetch_application_emojis():
        return [E("lt_legend_i")]

    async def official_icons(_):
        return {"lt_legend_i": "https://example/icon.png"}

    bot.fetch_application_emojis = fetch_application_emojis
    monkeypatch.setattr(emojis, "official_icons", official_icons)
    assert await emojis.sync_official(bot) == 0
    assert registry.league("Legend I")


# ------------------------------------------------------------------ drawn: arrows and numbers


def _colours(data):
    im = Image.open(io.BytesIO(data)).convert("RGBA")
    assert im.size == (emojis.EMOJI_SIZE, emojis.EMOJI_SIZE)
    return {c[:3] for _, c in im.getcolors(maxcolors=1 << 16) if c[3] == 255}


def test_arrows_and_numbers_are_drawn_in_colour():
    assert emojis.GAIN_GREEN in _colours(emojis.arrow_image("up", emojis.GAIN_GREEN))
    down = Image.open(io.BytesIO(emojis.arrow_image("down", emojis.LOSS_RED))).convert("RGBA")
    assert down.getpixel((64, 45))[3] == 255 and down.getpixel((64, 88))[3] < 255  # wide top, point below
    assert emojis.DONATED_BLUE in _colours(emojis.number_image(119, emojis.DONATED_BLUE))
    wide = Image.open(io.BytesIO(emojis.number_image(9999, emojis.LOSS_RED))).getbbox()
    assert wide[0] >= 0 and wide[2] <= emojis.EMOJI_SIZE  # long numbers shrink to fit
    assert emojis.number_key("d", 119) == "nd_119"


class Uploader:
    def __init__(self, fail=()):
        self.made, self.fail = [], set(fail)

    async def create_application_emoji(self, *, name, image):
        if name in self.fail:
            raise RuntimeError("boom")
        self.made.append(name)
        return E(name, 900 + len(self.made))


async def test_number_emojis_are_made_once_and_within_limits(registry, monkeypatch):
    monkeypatch.setattr(emojis, "_upload", _fast_upload)
    registry.load([E("nd_80", 5)])
    bot = Uploader()
    got = await emojis.number_emojis(bot, "d", [80, 154, 154, -1, 10_000])
    assert got == {80: "<:nd_80:5>", 154: "<:nd_154:901>"} and bot.made == ["nd_154"]
    assert await emojis.number_emojis(bot, "d", [154]) == {154: "<:nd_154:901>"}  # no new upload
    assert await emojis.number_emojis(bot, "x", [1]) == {}
    monkeypatch.setattr(emojis, "MAX_NEW_NUMBERS", 2)
    many = await emojis.number_emojis(bot, "r", [1, 2, 3])
    assert sorted(many) == [1, 2]  # 3 waits for a later message
    monkeypatch.setattr(emojis, "NUMBER_BUDGET", len(registry))
    assert await emojis.number_emojis(bot, "r", [4]) == {}  # the app is full enough
    registry.loaded = False
    assert await emojis.number_emojis(bot, "r", [1]) == {}  # not loaded yet: names unknown


async def test_a_failed_number_emoji_never_breaks_the_post(registry, monkeypatch):
    monkeypatch.setattr(emojis, "_upload", _fast_upload)
    registry.load([])
    assert await emojis.number_emojis(Uploader(fail={"nr_7"}), "r", [7, 8]) == {}


async def test_drawn_arrows_upload_when_missing(registry, monkeypatch):
    monkeypatch.setattr(emojis, "_upload", _fast_upload)
    registry.load([E("ui_arrow_up", 3)])
    bot = Uploader()
    assert await emojis.sync_drawn(bot) == 2 and sorted(bot.made) == ["ui_arrow_down", "ui_arrow_up_blue"]
    assert await emojis.sync_drawn(bot) == 0


async def _fast_upload(client, name, data, *, pause=1.0):
    return await client.create_application_emoji(name=name, image=data)
