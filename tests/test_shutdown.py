import asyncio
import os
import signal
from pathlib import Path

from discord.ext import commands

import clashhero.__main__ as entry
from clashhero.bot import ClashHeroBot
from clashhero.config import Config

CONFIG = Config("t", "e", "p", "k", Path(":memory:"), None, 60, 60, 120, 600, "INFO")


class FakeBot:
    closed = False

    def __init__(self, config):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        FakeBot.closed = True

    async def start(self, token):
        await asyncio.Event().wait()  # connected until stopped


async def test_stop_signal_closes_the_bot(monkeypatch):
    # Docker stops a container with SIGTERM, which its first process ignores unless it handles it.
    monkeypatch.setattr(entry, "ClashHeroBot", FakeBot)
    FakeBot.closed = False
    unhandled = []
    previous = signal.signal(signal.SIGTERM, lambda *_: unhandled.append(True))  # never kill pytest
    try:
        asyncio.get_running_loop().call_later(0.05, os.kill, os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(entry.run(CONFIG), 2)  # returns instead of raising
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert FakeBot.closed and not unhandled


async def test_close_stops_loops_before_closing_the_database(monkeypatch):
    bot = ClashHeroBot(CONFIG)
    order = []
    monkeypatch.setattr(bot.collector, "stop", lambda: order.append("collector"))

    async def discord_close(self):  # unloads the cogs (cancelling their loops), then disconnects
        order.append("cogs and Discord")

    async def db_close():
        order.append("database")

    monkeypatch.setattr(commands.Bot, "close", discord_close)
    monkeypatch.setattr(bot.db, "close", db_close)
    await bot.close()
    assert order == ["collector", "cogs and Discord", "database"]
