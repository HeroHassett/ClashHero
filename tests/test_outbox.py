"""Posts and DMs: one at a time per destination, and tried again while Discord rate-limits them
instead of being dropped."""

import asyncio
import io
from types import SimpleNamespace as NS

import discord
import pytest

from clashhero.cogs import legends as legends_cog
from clashhero.services import outbox


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(outbox, "RETRY_DELAYS", (0, 0, 0))


def rate_limited(headers=None):
    response = NS(status=429, reason="Too Many Requests", headers=headers or {})
    return discord.HTTPException(response, {"code": 0, "message": "You are being rate limited."})


def dms_too_fast():
    response = NS(status=400, reason="Bad Request", headers={})
    message = {"code": 40003, "message": "You are opening direct messages too fast."}
    return discord.HTTPException(response, message)


class Channel:
    """Fails with each error in ``errors`` in turn, then posts."""

    def __init__(self, *errors, pause=0.0):
        self.errors = list(errors)
        self.attempts = 0
        self.sent = []
        self.pause = pause
        self.busy = self.most_busy = 0

    async def send(self, **kw):
        self.attempts += 1
        self.busy += 1
        self.most_busy = max(self.most_busy, self.busy)
        try:
            await asyncio.sleep(self.pause)
            if self.errors:
                raise self.errors.pop(0)
            if "file" in kw:
                kw["data"] = kw["file"].fp.read()
            self.sent.append(kw)
            return NS(id=len(self.sent), **kw)
        finally:
            self.busy -= 1


def bot_with(**channels):
    return NS(get_channel=lambda cid: channels.get(str(cid)))


async def test_a_rate_limited_post_is_sent_on_a_later_try():
    ch = Channel(rate_limited(), rate_limited())
    msg = await outbox.send_to_channel(bot_with(**{"5": ch}), 5, content="hi")
    assert msg is not None and msg.content == "hi"
    assert ch.attempts == 3 and len(ch.sent) == 1


async def test_a_post_still_rate_limited_after_every_retry_is_given_up():
    ch = Channel(*(rate_limited() for _ in range(10)))
    assert await outbox.send_to_channel(bot_with(**{"5": ch}), 5, content="hi") is None
    assert ch.attempts == len(outbox.RETRY_DELAYS) + 1


async def test_other_errors_are_not_retried():
    ch = Channel(discord.Forbidden(NS(status=403, reason="Forbidden"), "Missing Access"))
    assert await outbox.send_to_channel(bot_with(**{"5": ch}), 5, content="hi") is None
    assert ch.attempts == 1


async def test_a_file_is_rewound_before_a_retry():
    class ReadsThenFails(Channel):
        async def send(self, **kw):
            if not self.attempts:
                kw["file"].fp.read()  # the failed upload consumed it
            return await super().send(**kw)

    ch = ReadsThenFails(rate_limited())
    image = discord.File(io.BytesIO(b"png"), filename="card.png")
    await outbox.send_to_channel(bot_with(**{"5": ch}), 5, file=image)
    assert ch.sent[0]["data"] == b"png"


async def test_posts_to_a_channel_go_one_at_a_time():
    busy, quiet = Channel(rate_limited(), pause=0.01), Channel(pause=0.01)
    bot = bot_with(**{"5": busy, "6": quiet})
    await asyncio.gather(
        *(outbox.send_to_channel(bot, 5, content=str(n)) for n in range(4)),
        *(outbox.send_to_channel(bot, 6, content=str(n)) for n in range(2)),
    )
    assert busy.most_busy == 1 and len(busy.sent) == 4
    assert [m["content"] for m in quiet.sent] == ["0", "1"]


def test_retry_after_is_read_from_discord():
    assert outbox._retry_after(rate_limited({"Retry-After": "3.5"})) == 3.5
    assert outbox._retry_after(rate_limited()) == 0.0
    assert outbox._retry_after(dms_too_fast()) == 0.0
    forbidden = discord.Forbidden(NS(status=403, reason="Forbidden"), "Missing Access")
    assert outbox._retry_after(forbidden) is None


# ------------------------------------------------------------------ DMs


class User(Channel):
    pass


class DMBot:
    def __init__(self, users):
        self.users = users

    def get_user(self, uid):
        return self.users.get(uid)

    async def fetch_user(self, uid):
        raise discord.NotFound(NS(status=404, reason="Not Found"), "Unknown User")


async def test_dms_opened_too_fast_are_sent_on_a_later_try():
    user = User(dms_too_fast())
    embed = discord.Embed(title="alert")
    assert await outbox.send_dm(DMBot({1: user}), 1, embed=embed)
    assert user.attempts == 2 and user.sent[0]["embed"] is embed


async def test_dms_that_cannot_be_delivered_report_false():
    closed = User(discord.Forbidden(NS(status=403, reason="Forbidden"), "Cannot send messages to this user"))
    bot = DMBot({1: closed})
    assert not await outbox.send_dm(bot, 1, embed=discord.Embed())
    assert not await outbox.send_dm(bot, 2, embed=discord.Embed())  # unknown user
    assert closed.attempts == 1


async def test_a_burst_of_dms_to_one_user_all_arrive():
    user = User(dms_too_fast(), dms_too_fast(), pause=0.01)
    bot = DMBot({1: user})
    alerts = [discord.Embed(title=str(n)) for n in range(3)]
    results = await asyncio.gather(*(outbox.send_dm(bot, 1, embed=e) for e in alerts))
    assert results == [True] * 3 and user.most_busy == 1
    assert sorted(m["embed"].title for m in user.sent) == ["0", "1", "2"]


async def test_legend_alert_dms_go_through_the_retrying_sender(monkeypatch):
    sent = []

    async def fake_send_dm(bot, user_id, *, embed, view=None, what="message"):
        sent.append((user_id, what))
        return True

    monkeypatch.setattr(legends_cog, "send_dm", fake_send_dm)
    cog = legends_cog.Legends(NS(db=None))
    event = NS(kind="defense", event_id=None, opponent_tag=None, army_share_code=None)
    await cog._deliver({"id": 3, "user_id": 10, "channel_id": 0}, discord.Embed(), event)
    assert sent == [(10, "legend alert")]
