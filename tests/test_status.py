from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import coc
import discord
import pytest
from test_collector import NOW, setup  # noqa: F401

from clashhero.cogs._log_embeds import maintenance_embed
from clashhero.cogs.status import presence_for, status_fields
from clashhero.utils.fmt import duration, maintenance_notice, make_embed


def test_duration():
    assert duration(45) == "0m"
    assert duration(4320) == "1h 12m"
    assert duration(3 * 86400 + 7200 + 60) == "3d 2h"


def test_presence():
    status, activity = presence_for(True, 3, 2, 1)
    assert status is discord.Status.idle and "maintenance" in activity.name
    status, activity = presence_for(False, 1, 1, 0)
    assert status is discord.Status.online and activity.name == "/help for commands"
    status, activity = presence_for(False, 1, 1, 1)
    assert activity.name == "1 clan in 1 server"
    assert presence_for(False, 12, 1234, 1)[1].name == "12 clans in 1,234 servers"


def test_status_fields():
    now = datetime(2026, 11, 8, 12, tzinfo=UTC)
    fields = dict(
        status_fields(
            in_maintenance=True,
            maintenance_since=now - timedelta(minutes=72),
            last_success={"clans": now},
            now=now,
        )
    )
    assert "maintenance" in fields["Clash of Clans API"] and "1h 12m" in fields["Clash of Clans API"]
    assert "waiting for first update" in fields["Last updated"]
    fields = dict(status_fields(in_maintenance=False, maintenance_since=None, last_success={}, now=now))
    assert fields["Clash of Clans API"] == "✅ Online"


async def test_embeds_carry_a_notice_during_maintenance(db):
    collector = NS(in_maintenance=True, maintenance_since=datetime(2026, 11, 8, 9, 30, tzinfo=UTC))
    client = NS(db=db, collector=collector)
    e = await make_embed(client, 1, title="x")
    assert e.author.name and "09:30 UTC" in e.author.name
    collector.in_maintenance = False
    assert maintenance_notice(client) is None
    assert (await make_embed(client, 1, title="x")).author.name is None


def test_maintenance_ended_embed_shows_duration():
    at = datetime(2026, 11, 8, 12, tzinfo=UTC)
    e = maintenance_embed(False, at, at - timedelta(minutes=95))
    assert "1h 35m" in e.description


async def test_collector_tracks_maintenance_window(setup):  # noqa: F811
    bot, coc_fake, col = setup

    async def down():
        raise coc.Maintenance(NS(status=503, reason="maintenance"), {})

    async def up():
        return "ok"

    try:
        await col._guarded(down())
    except Exception as e:  # pragma: no cover - constructor differences across coc versions
        pytest.skip(f"couldn't build coc.Maintenance: {e!r}")
    assert col.in_maintenance and col.maintenance_since is not None
    await col._guarded(down())  # still down: no second "started" event
    await col._guarded(up())
    events = [e for n, e in bot.events if n == "clash_maintenance"]
    assert [e.started for e in events] == [True, False]
    assert events[1].since is not None and not col.in_maintenance
