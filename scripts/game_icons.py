"""Fetch Clash of Clans game icons for ClashHero's emojis and image cards.

    uv run python scripts/game_icons.py              # dry run: show what would be downloaded
    uv run python scripts/game_icons.py --download   # download into game-assets/icons/
    uv run clashhero-emojis game-assets/icons        # then upload them as app emojis

For every home-village troop, spell, hero, pet, equipment piece, siege machine and super troop
(names from the official API plus coc.py's game data), plus Town Halls, a few resources and the
CWL league badges, it finds one icon:

1. the Clash of Clans wiki's square in-game icon (``Avatar <Name>.png``), or its item image for
   equipment, Town Halls and resources;
2. otherwise Supercell's official Fan Kit (https://fankit.supercell.com).

Both are Supercell artwork, used under Supercell's Fan Content Policy. Files are resized to at
most 256px by the source's own CDN and named after the emoji they become (``barbarian_king.png``,
``th18.png``). ``game-assets/`` is git-ignored: the art is used locally, not republished here.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

UA = {"User-Agent": "ClashHero icon fetcher (Clash of Clans fan project)"}
SIZE = 256
WIKI_API = "https://clashofclans.fandom.com/api.php"
FANKIT_API = "https://fankit.supercell.com/api/assets/search/338"
FANKIT_CATEGORIES = [
    "Characters", "Spells", "Hero Pets", "Hero Equipment", "Siege Machines", "Super Troops",
    "Town Hall/Builder Hall/Capital Hall", "Resources", "Icons",
]  # fmt: skip
MAX_TOWN_HALL = 18
# Units whose wiki image isn't their in-game icon (e.g. "Fireball.png" is the spell effect, not
# the Grand Warden's equipment); the Fan Kit's icon is used instead.
PREFER_FANKIT = {"Fireball"}

# emoji name -> wiki file names to try, for things that aren't units
EXTRAS = {
    "res_gold": ["Gold.png"],
    "res_elixir": ["Elixir.png"],
    "res_dark_elixir": ["Dark Elixir.png"],
    "res_gems": ["Gem.png"],
    "res_raid_medal": ["Raid Medal.png"],
    "res_trophy": ["Trophy.png"],
}
CWL_LEAGUES = ["Bronze", "Silver", "Gold", "Crystal", "Master", "Champion"]
ROMAN = {1: "i", 2: "ii", 3: "iii"}


@dataclass
class Pick:
    name: str  # emoji / file name
    source: str  # "wiki" | "fankit"
    title: str
    url: str
    width: int | None = None
    bytes_hint: int | None = None


def get_json(url: str) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return json.load(r)


def slug(name: str) -> str:
    from clashhero.services.emojis import unit_key

    return unit_key(name)


# ------------------------------------------------------------------ what we need


def unit_names(live: bool = True) -> dict[str, str]:
    """In-game name -> kind for every home-village unit."""
    import coc

    from clashhero.services.army import NEWER_IDS

    out: dict[str, str] = {}
    data = json.loads((Path(coc.__file__).parent / "static" / "static_data.json").read_text("utf-8"))
    for key, kind in (("troops", "troop"), ("spells", "spell"), ("heroes", "hero"), ("pets", "pet"),
                      ("equipment", "equipment")):  # fmt: skip
        for it in data.get(key) or []:
            if it.get("name") and it.get("village", "home") == "home":
                out.setdefault(it["name"], kind)
    for (kind, _), name in NEWER_IDS.items():
        out.setdefault(name, kind)
    if live:
        try:
            out.update({n: k for n, k in live_units().items() if n not in out})
        except Exception as e:  # noqa: BLE001
            print(f"(couldn't read live unit names: {e!r}; using coc.py's data only)")
    return out


def live_units() -> dict[str, str]:
    import asyncio

    import coc

    from clashhero.config import Config

    async def run() -> dict[str, str]:
        cfg = Config.from_env()
        c = coc.Client(key_names=cfg.coc_key_name, key_count=1, raw_attribute=True)
        if cfg.uses_api_tokens:
            await c.login_with_tokens(*cfg.coc_api_tokens)
        else:
            await c.login(cfg.coc_email, cfg.coc_password)
        try:
            top = await c.get_location_players("global", limit=1)
            raw = (await c.get_player(top[0].tag))._raw_data
        finally:
            await c.close()
        found = {}
        for key, kind in (("troops", "troop"), ("spells", "spell"), ("heroes", "hero"),
                          ("heroEquipment", "equipment")):  # fmt: skip
            for u in raw.get(key) or []:
                if u.get("village", "home") == "home":
                    found[u["name"]] = kind
        return found

    return asyncio.run(run())


# ------------------------------------------------------------------ the wiki


def wiki_lookup(titles: list[str]) -> dict[str, dict]:
    """File title (without "File:") -> {url, width, size} for those that exist."""
    found: dict[str, dict] = {}
    for i in range(0, len(titles), 50):
        chunk = titles[i : i + 50]
        q = urllib.parse.urlencode({
            "action": "query", "format": "json", "redirects": 1, "prop": "imageinfo",
            "iiprop": "url|size", "titles": "|".join(f"File:{t}" for t in chunk),
        })  # fmt: skip
        d = get_json(f"{WIKI_API}?{q}")["query"]
        norm = {n["to"]: n["from"] for n in d.get("normalized", [])}
        redir = {r["to"]: r["from"] for r in d.get("redirects", [])}
        for page in d.get("pages", {}).values():
            if "missing" in page or not page.get("imageinfo"):
                continue
            title = page["title"]
            title = redir.get(title, title)
            title = norm.get(title, title).removeprefix("File:")
            ii = page["imageinfo"][0]
            found[title] = {"url": ii["url"], "width": ii.get("width"), "size": ii.get("size")}
        time.sleep(0.3)
    return found


def wiki_scaled(url: str, width: int | None) -> str:
    """Fandom serves resized copies: .../revision/latest/scale-to-width-down/256?cb=..."""
    if width and width > SIZE and "/revision/latest" in url:
        return url.replace("/revision/latest", f"/revision/latest/scale-to-width-down/{SIZE}", 1)
    return url


def wiki_candidates(name: str, kind: str) -> list[str]:
    if kind == "equipment":
        return [f"{name}.png"]
    out = [f"Avatar {name}.png"]
    if kind == "hero":
        out.append(f"Avatar Hero {name}.png")
    return out


# ------------------------------------------------------------------ the Fan Kit


def fankit_assets() -> list[dict]:
    seen, out = set(), []
    for cat in FANKIT_CATEGORIES:
        page = 1
        while True:
            q = urllib.parse.urlencode({"limit": 100, "page": page, "order": "NEWEST", "asset-type97": cat})
            data = get_json(f"{FANKIT_API}?{q}")
            for a in data.get("data") or []:
                if a["id"] not in seen and a.get("is_image") and a.get("generic_url"):
                    seen.add(a["id"])
                    out.append(a)
            if not data.get("hasMore"):
                break
            page += 1
            time.sleep(0.2)
    return out


_FK_PREFIX = re.compile(
    r"^(icon_hv_hero_pets_|icon_hv_dark_spell_|icon_hv_spell_|icon_hv_siege_machine_|icon_hv_hero_|"
    r"icon_hv_|hero_equipe?ment_(?:bk|aq|bq|gw|rc|mp|dd)_|hero_equipe?ment_|heroequipment_mp_|"
    r"herogear_(?:bk|mp)_|hg_dd_|icon_gear_gw_)"
)


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def fankit_key(title: str) -> str:
    k = re.sub(r"[^a-z0-9]+", "_", title.strip().lower()).strip("_")
    k = _FK_PREFIX.sub("", k)
    k = re.sub(r"_(new|small|icon|2k|\d)$", "", k)
    return _compact(k.replace("_spell", ""))


FANKIT_ALIASES = {
    "Metal Pants": "ironpants",
    "Stun Blaster": "stunblast",
    "Revenge Deck": "reversecard",
    "Hog Rider Puppet": "hogriderdoll",
    "Henchmen Puppet": "henchman",
    "Stick Horse": "stickfirehorse",
    "Rocket Spear": "royalchampionrocketspearequipment01",
    "Dark Crown": "darkcrown",
}


def fankit_pick(assets: list[dict], name: str) -> dict | None:
    want = FANKIT_ALIASES.get(name) or _compact(name.replace(" Spell", ""))
    cands = [a for a in assets if fankit_key(a["title"]) == want]
    if not cands:
        return None
    # prefer actual icons, then the smallest file (icon-sized)
    return sorted(cands, key=lambda a: (not a["title"].lower().startswith("icon"), a.get("width") or 9999))[0]


def fankit_url(a: dict) -> str:
    return a["generic_url"].replace("{width}", str(min(SIZE, a.get("width") or SIZE)))


# ------------------------------------------------------------------ plan + download


def plan(live: bool) -> tuple[list[Pick], list[str]]:
    units = unit_names(live)
    picks: dict[str, Pick] = {}

    wanted: dict[str, list[str]] = {slug(n): wiki_candidates(n, k) for n, k in units.items()}
    # TH12-17 also come in weapon levels ("Town Hall17-5.png"); use the top one when needed
    wanted.update(
        {f"th{n}": [f"Town Hall{n}.png", f"Town Hall{n}-5.png"] for n in range(1, MAX_TOWN_HALL + 1)}
    )
    wanted.update(EXTRAS)
    wiki = wiki_lookup(sorted({t for ts in wanted.values() for t in ts}))
    for key, titles in wanted.items():
        for t in titles:
            if t in wiki:
                w = wiki[t]
                picks[key] = Pick(key, "wiki", t, wiki_scaled(w["url"], w["width"]), w["width"], w["size"])
                break

    assets = fankit_assets()
    for n in units:
        key = slug(n)
        if (key not in picks or n in PREFER_FANKIT) and (a := fankit_pick(assets, n)):
            picks[key] = Pick(key, "fankit", a["title"], fankit_url(a), a.get("width"))
    for a in assets:  # Town Halls the wiki lacked, and CWL league badges
        t = a["title"].strip()
        if m := re.fullmatch(r"(?i)building_hv_town_hall_level_(\d+)", t):
            key = f"th{int(m.group(1))}"
            if key not in picks:
                picks[key] = Pick(key, "fankit", t, fankit_url(a), a.get("width"))
        if m := re.fullmatch(r"(?i)icon_hv_cwl_([a-z]+)_\d*(\d)", t):
            league, n = m.group(1).title(), int(m.group(2))
            if league in CWL_LEAGUES and n in ROMAN:
                key = f"cwl_{league.lower()}_{ROMAN[n]}"
                picks.setdefault(key, Pick(key, "fankit", t, fankit_url(a), a.get("width")))
    missing = sorted(n for n in units if slug(n) not in picks)
    return sorted(picks.values(), key=lambda p: p.name), missing


def download(picks: list[Pick], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    manifest, failed = {}, []
    for p in picks:
        try:
            with urllib.request.urlopen(urllib.request.Request(p.url, headers=UA), timeout=30) as r:
                data = r.read()
            if not data.startswith((b"\x89PNG", b"\xff\xd8", b"RIFF")):
                raise ValueError("not an image")
            (out / f"{p.name}.png").write_bytes(_as_png(data))
            manifest[p.name] = {"source": p.source, "title": p.title, "url": p.url}
        except Exception as e:  # noqa: BLE001
            failed.append(f"{p.name} ({e})")
        time.sleep(0.15)
    (out / "sources.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
    total = sum(f.stat().st_size for f in out.glob("*.png"))
    print(f"Saved {len(manifest)} icons ({total / 1e6:.1f} MB) in {out}/")
    if failed:
        print(f"Failed: {', '.join(failed)}")


def _as_png(data: bytes) -> bytes:
    import io

    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGBA")
        im.thumbnail((SIZE, SIZE), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        return buf.getvalue()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--download", action="store_true", help="download (default is a dry run)")
    ap.add_argument(
        "--offline", action="store_true", help="don't ask the Clash API for the newest unit names"
    )
    ap.add_argument("--out", type=Path, default=ROOT / "game-assets" / "icons")
    args = ap.parse_args()

    picks, missing = plan(live=not args.offline)
    by_src = {s: sum(1 for p in picks if p.source == s) for s in ("wiki", "fankit")}
    est = sum(min(p.bytes_hint or 60_000, 60_000) for p in picks)
    print(f"Found {len(picks)} icons: {by_src['wiki']} from the wiki, {by_src['fankit']} from the Fan Kit "
          f"(~{est / 1e6:.0f} MB to download at <= {SIZE}px).")  # fmt: skip
    print(f"Units without an icon ({len(missing)}): {', '.join(missing) or 'none'}")
    if not args.download:
        for p in picks:
            print(f"  {p.name:28} {p.source:6} {p.title}")
        print(f"\nDry run: nothing downloaded. Re-run with --download to save them in {args.out}/")
        return
    download(picks, args.out)


if __name__ == "__main__":
    main()
