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
    saved = dict(EMOJIS._by_name)
    yield EMOJIS
    EMOJIS._by_name = saved


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
