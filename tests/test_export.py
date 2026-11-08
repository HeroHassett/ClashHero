import csv
import io
from datetime import UTC, datetime

from test_info import seed_wars

from clashhero.cogs._info_embeds import rows_from, to_csv
from clashhero.cogs.export import EXPORTERS, export_missed, export_season, export_users
from clashhero.utils.seasons import Window

WINDOW = Window("2026-11-30", datetime(2026, 11, 2, 5, tzinfo=UTC), datetime(2026, 11, 30, 5, tzinfo=UTC))


def parse(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))


def test_to_csv_handles_none_and_unicode():
    data = to_csv(["a", "b"], [[1, None], ["名前", "x,y"]])
    assert parse(data) == [["a", "b"], ["1", ""], ["名前", "x,y"]]
    assert rows_from([{"a": 1, "b": 2}], ["b"]) == [[2]]


async def test_export_season(db):
    await db.execute(
        "INSERT INTO season_stats (season_id, player_tag, clan_tag, donations) VALUES (?, '#A', '#C', 50)",
        (WINDOW.id,),
    )
    name, data = await export_season(db, ["#C"], WINDOW)
    rows = parse(data)
    assert name == f"season-{WINDOW.id}.csv"
    assert rows[0][:3] == ["season", "clan_tag", "player_tag"]
    assert rows[1][:3] == [WINDOW.id, "#C", "#A"] and rows[1][5] == "50"


async def test_export_missed(db):
    await seed_wars(db)
    _, data = await export_missed(db, ["#C"], WINDOW)
    rows = parse(data)
    assert len(rows) == 3 and rows[0][-1] == "missed"


async def test_export_users(db):
    await db.execute(
        "INSERT INTO players (player_tag, name, clan_tag, updated_at) VALUES ('#A', 'Ann', '#C', 'x')"
    )
    await db.link_player("#A", 42)
    await db.link_player("#Z", 42)  # alt outside the clan still included
    _, data = await export_users(db, ["#C"], WINDOW, lambda uid: "ann#0")
    rows = parse(data)
    assert [r[2] for r in rows[1:]] == ["#A", "#Z"] and rows[1][1] == "ann#0"


async def test_all_exporters_run_on_empty_db(db):
    for kind, fn in EXPORTERS.items():
        name, data = await fn(db, ["#C"], WINDOW)
        assert name.endswith(".csv") and parse(data)[0], kind
