import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import discord
import pytest

from clashhero.cogs import flags as cog
from clashhero.db import Database
from clashhero.repos import flags as repo
from clashhero.utils.fmt import player_link

NOW = datetime(2026, 11, 7, tzinfo=UTC)


async def test_create_list_expire_delete(db):
    f1 = await repo.create_flag(db, 1, "#P1", "ban", "cheating", 9, NOW)
    await repo.create_flag(db, 1, "#P2", "strike", None, 9, NOW, expiry_days=1)
    await repo.create_flag(db, 2, "#P1", "strike", None, 9, NOW)
    assert len(await repo.list_flags(db, 1, NOW)) == 2
    later = NOW + timedelta(days=2)
    assert [r["player_tag"] for r in await repo.list_flags(db, 1, later)] == ["#P1"]
    assert len(await repo.list_flags(db, 1, later, include_expired=True)) == 2
    assert [r["flag_type"] for r in await repo.list_flags(db, 1, NOW, flag_type="strike")] == ["strike"]
    assert len(await repo.active_flags(db, 1, "#P1", NOW)) == 1
    assert not await repo.delete_flag(db, 2, f1)  # other guild's id space
    assert await repo.delete_flag(db, 1, f1)
    assert await repo.active_flags(db, 1, "#P1", NOW) == []


async def test_list_by_clan(db):
    await db.execute(
        "INSERT INTO players (player_tag, name, clan_tag, updated_at) VALUES ('#P1','A','#C','x')"
    )
    await repo.create_flag(db, 1, "#P1", "ban", None, 9, NOW)
    await repo.create_flag(db, 1, "#P2", "ban", None, 9, NOW)
    rows = await repo.list_flags(db, 1, NOW, clan_tags=["#C"])
    assert [r["player_tag"] for r in rows] == ["#P1"]
    assert await repo.list_flags(db, 1, NOW, clan_tags=[]) == []


# ------------------------------------------------------------------ weighted strikes


async def test_weight_defaults_and_validation(db):
    s = await repo.create_flag(db, 1, "#P1", "strike", None, 9, NOW, weight=4)
    d = await repo.create_flag(db, 1, "#P1", "strike", None, 9, NOW)
    b = await repo.create_flag(db, 1, "#P1", "ban", None, 9, NOW, weight=7)  # bans don't carry weight
    weights = {r["id"]: r["weight"] for r in await repo.list_flags(db, 1, NOW)}
    assert weights == {s: 4, d: 1, b: 1}
    for bad in (0, 11):
        with pytest.raises(ValueError):
            await repo.create_flag(db, 1, "#P1", "strike", None, 9, NOW, weight=bad)


async def test_list_min_strike_weight(db):
    await repo.create_flag(db, 1, "#P1", "strike", None, 9, NOW, weight=2)
    await repo.create_flag(db, 1, "#P1", "strike", None, 9, NOW, weight=3)
    await repo.create_flag(db, 1, "#P1", "ban", "x", 9, NOW)
    await repo.create_flag(db, 1, "#P2", "strike", None, 9, NOW, expiry_days=1, weight=5)
    await repo.create_flag(db, 1, "#P3", "strike", None, 9, NOW, weight=1)
    await repo.create_flag(db, 2, "#P3", "strike", None, 9, NOW, weight=10)  # other server

    def tags(rows):
        return sorted({r["player_tag"] for r in rows})

    assert tags(await repo.list_flags(db, 1, NOW, min_strike_weight=5)) == ["#P1", "#P2"]
    # P1's ban is listed too: the filter is on the player's strike total, not the flag type.
    assert len(await repo.list_flags(db, 1, NOW, min_strike_weight=5, player_tag="#P1")) == 3
    assert tags(await repo.list_flags(db, 1, NOW, min_strike_weight=6)) == []
    later = NOW + timedelta(days=2)
    assert tags(await repo.list_flags(db, 1, later, min_strike_weight=5)) == ["#P1"]
    assert tags(await repo.list_flags(db, 1, later, min_strike_weight=5, include_expired=True)) == [
        "#P1",
        "#P2",
    ]
    rows = await repo.list_flags(db, 1, NOW, min_strike_weight=2, flag_type="strike")
    assert {r["player_tag"] for r in rows} == {"#P1", "#P2"}


async def test_strike_totals(db):
    await db.execute(
        "INSERT INTO players (player_tag, name, clan_tag, updated_at) VALUES "
        "('#P1','One','#C','x'), ('#P2','Two','#C','x'), ('#P3','Three','#D','x')"
    )
    await repo.create_flag(db, 1, "#P1", "strike", None, 9, NOW, weight=2)
    await repo.create_flag(db, 1, "#P1", "strike", None, 9, NOW + timedelta(hours=1), weight=1)
    await repo.create_flag(db, 1, "#P2", "strike", None, 9, NOW, weight=6)
    await repo.create_flag(db, 1, "#P2", "ban", None, 9, NOW)
    await repo.create_flag(db, 1, "#P3", "strike", None, 9, NOW, expiry_days=1, weight=9)
    await repo.create_flag(db, 1, "#P4", "ban", None, 9, NOW)  # bans alone don't appear

    rows = await repo.strike_totals(db, 1, NOW)
    assert [(r["player_tag"], r["total"], r["strikes"]) for r in rows] == [
        ("#P3", 9, 1),
        ("#P2", 6, 1),
        ("#P1", 3, 2),
    ]
    assert [bool(r["banned"]) for r in rows] == [False, True, False]
    assert rows[2]["name"] == "One"
    later = NOW + timedelta(days=2)
    assert [r["player_tag"] for r in await repo.strike_totals(db, 1, later)] == ["#P2", "#P1"]
    assert [r["player_tag"] for r in await repo.strike_totals(db, 1, NOW, clan_tags=["#C"])] == ["#P2", "#P1"]
    assert [r["player_tag"] for r in await repo.strike_totals(db, 1, NOW, min_weight=4)] == ["#P3", "#P2"]
    assert await repo.strike_totals(db, 2, NOW) == []
    assert await repo.strike_total(db, 1, "#P1", NOW) == 3
    assert await repo.strike_total(db, 1, "#P3", later) == 0


async def test_weight_column_added_to_existing_table(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE flags (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, "
        "player_tag TEXT NOT NULL, flag_type TEXT NOT NULL, reason TEXT, created_by INTEGER NOT NULL, "
        "created_at TEXT NOT NULL, expires_at TEXT)"
    )
    con.execute(
        "INSERT INTO flags (guild_id, player_tag, flag_type, created_by, created_at) "
        "VALUES (1, '#P1', 'strike', 9, ?)",
        (NOW.isoformat(),),
    )
    con.commit()
    con.close()
    d = Database(path)
    await d.connect()
    try:
        assert await d.fetchval("SELECT weight FROM flags") == 1
        assert await repo.strike_total(d, 1, "#P1", NOW) == 1
    finally:
        await d.close()


def test_flag_line_shows_weight():
    base = {
        "id": 3,
        "flag_type": "strike",
        "weight": 3,
        "expires_at": None,
        "reason": "missed war",
        "name": "A",
        "player_tag": "#P1",
        "created_by": 9,
        "created_at": NOW.isoformat(),
    }
    head, reason, meta = cog.flag_line(base).split("\n")
    assert head == f"`#3` ⚠️ **Strike ×3** · [A]({player_link('#P1')}) `#P1`"
    assert reason == "> missed war" and meta.startswith("-# by <@9> <t:")
    assert "×" not in cog.flag_line({**base, "weight": 1})
    ban = cog.flag_line({**base, "flag_type": "ban", "weight": 1, "name": None})
    assert ban.startswith(f"`#3` ⛔ **Ban** · [#P1]({player_link('#P1')})\n")
    assert cog.flag_line(base, player=False).split("\n")[0] == "`#3` ⚠️ **Strike ×3**"
    expiring = cog.flag_line({**base, "reason": None, "expires_at": NOW.isoformat()})
    assert "missed war" not in expiring and expiring.endswith(":R>") and " · expires <t:" in expiring
    assert len(expiring.split("\n")) == 2


def test_flags_summary_counts_bans_and_strikes():
    rows = [{"flag_type": "ban"}, {"flag_type": "strike"}, {"flag_type": "strike"}]
    assert cog.flags_summary(rows) == "⛔ **1** ban · ⚠️ **2** strikes"
    assert cog.flags_summary([]) == "⛔ **0** bans · ⚠️ **0** strikes"


def test_summary_line_pads_total():
    r = {"total": 7, "name": "A", "player_tag": "#P1", "strikes": 3, "last_strike": NOW.isoformat()}
    line = cog.summary_line(1, {**r, "banned": 0}, width=2)
    assert line.startswith(f"` 1.` ` 7` [A]({player_link('#P1')}) `#P1` · 3 strikes, latest <t:")
    assert "banned" not in line
    assert cog.summary_line(2, {**r, "strikes": 1, "banned": 1}).endswith(" · ⛔ banned")
    assert "1 strike, latest" in cog.summary_line(2, {**r, "strikes": 1, "banned": 0})


def test_fit_blocks_notes_what_is_left_out():
    blocks = [f"line {i:03}" for i in range(1000)]
    kept = cog.fit_blocks(blocks, 600)
    assert len("\n".join(kept)) <= 600
    assert kept[-1].startswith("-# … ") and kept[-1].endswith("Narrow it down with the filters.")
    assert int(kept[-1].split()[2]) == 1000 - (len(kept) - 1)
    assert cog.fit_blocks(["a", "b"]) == ["a", "b"]
    assert cog.fit_blocks(blocks, 600, hint="See `/flag list`.")[-1].endswith(
        "more not shown. See `/flag list`."
    )


def test_long_reasons_are_cut():
    r = {
        "id": 1,
        "flag_type": "ban",
        "weight": 1,
        "expires_at": None,
        "reason": "x" * 1000,
        "name": "A",
        "player_tag": "#P1",
        "created_by": 9,
        "created_at": NOW.isoformat(),
    }
    reason = cog.flag_line(r).split("\n")[1]
    assert len(reason) == 302 and reason.endswith("…")


def test_dm_embed():
    e = cog.dm_embed(
        guild_name="Family",
        player_name="A",
        player_tag="#P1",
        flag_type="strike",
        reason=None,
        weight=2,
        expires_at=None,
        total=5,
    )
    fields = {f.name: f.value for f in e.fields}
    assert "strike" in e.title and fields["Weight"] == "**2**" and fields["Active strike total"] == "**5**"
    assert e.description == f"Account: [A]({player_link('#P1')}) `#P1`"
    assert fields["Expires"] == "Never" and fields["Reason"] == "No reason given."
    ban = cog.dm_embed(
        guild_name="Family",
        player_name="A",
        player_tag="#P1",
        flag_type="ban",
        reason="cheating",
        weight=1,
        expires_at=None,
    )
    assert "banned" in ban.title and "Weight" not in {f.name for f in ban.fields}


class _User:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.sent = []

    async def send(self, **kw):
        if self.fail:
            raise discord.Forbidden(NS(status=403, reason="Forbidden"), "Cannot send messages to this user")
        self.sent.append(kw)


class _DMBot:
    def __init__(self, users):
        self.users = users

    def get_user(self, uid):
        return self.users.get(uid)

    async def fetch_user(self, uid):
        raise discord.NotFound(NS(status=404, reason="Not Found"), "Unknown User")


async def test_dm_users_reports_failures():
    ok, closed = _User(), _User(fail=True)
    bot = _DMBot({1: ok, 2: closed})
    sent, failed = await cog.dm_users(bot, [1, 2, 3], discord.Embed(title="x"))
    assert sent == [1] and failed == [2, 3]
    assert len(ok.sent) == 1


class _Channel:
    def __init__(self):
        self.sent = []

    async def send(self, **kw):
        self.sent.append(kw)


async def test_join_alert_still_fires_with_strike_total(db):
    await db.add_guild_clan(1, "#C")
    await db.execute(
        "INSERT INTO clans (clan_tag, name, badge_url, updated_at) "
        "VALUES ('#C', 'Clan', 'https://b/c.png', 'x')"
    )
    await db.set_server_log(1, "flag_alert", 50)
    await repo.create_flag(db, 1, "#P1", "strike", "late", 9, datetime.now(UTC), weight=4)
    await repo.create_flag(db, 1, "#P1", "ban", None, 9, datetime.now(UTC))
    channel = _Channel()
    bot = NS(db=db, get_channel=lambda cid: channel if cid == 50 else None)
    flags = cog.Flags(bot)
    member = NS(tag="#P1", name="A", town_hall=16)
    await flags.on_clash_member_join(NS(clan_tag="#C", clan_name="Clan", member=member, member_count=10))
    assert len(channel.sent) == 1
    embed = channel.sent[0]["embed"]
    assert embed.title == "A (#P1)" and embed.url == player_link("#P1")
    assert embed.author.name == "Clan" and embed.author.icon_url == "https://b/c.png"
    assert embed.description.split("\n") == ["🚩 Joined **Clan** with **2** active flags", "TH **16**"]
    fields = {f.name: f.value for f in embed.fields}
    assert "**Strike ×4**" in fields["Flags"] and "⛔ **Ban**" in fields["Flags"]
    assert "#P1" not in fields["Flags"]  # the title already says who
    assert fields["Active strike total"] == "⚠️ **4**"
    # Not flagged: no alert.
    await flags.on_clash_member_join(
        NS(clan_tag="#C", clan_name="Clan", member=NS(tag="#P2", name="B", town_hall=15), member_count=11)
    )
    assert len(channel.sent) == 1
