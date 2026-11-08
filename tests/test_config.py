import pytest

from clashhero.config import Config, ConfigError, parse_tokens

ENV_KEYS = ("DISCORD_TOKEN", "COC_EMAIL", "COC_PASSWORD", "COC_API_TOKENS", "COC_API_TOKEN", "DEV_GUILD_ID")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.setattr("clashhero.config.load_dotenv", lambda: None)
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DISCORD_TOKEN", "discord")


def test_parse_tokens():
    assert parse_tokens(" a, b  c,,") == ("a", "b", "c")
    assert parse_tokens("") == ()


def test_tokens_take_priority(monkeypatch):
    monkeypatch.setenv("COC_API_TOKENS", "t1,t2")
    monkeypatch.setenv("COC_EMAIL", "me@example.com")
    monkeypatch.setenv("COC_PASSWORD", "pw")
    cfg = Config.from_env()
    assert cfg.uses_api_tokens and cfg.coc_api_tokens == ("t1", "t2")


def test_email_password_mode(monkeypatch):
    monkeypatch.setenv("COC_EMAIL", "me@example.com")
    monkeypatch.setenv("COC_PASSWORD", "pw")
    cfg = Config.from_env()
    assert not cfg.uses_api_tokens and cfg.coc_email == "me@example.com"


def test_missing_clash_credentials():
    with pytest.raises(ConfigError, match="COC_API_TOKENS"):
        Config.from_env()


def test_email_without_password_is_rejected(monkeypatch):
    monkeypatch.setenv("COC_EMAIL", "me@example.com")
    with pytest.raises(ConfigError):
        Config.from_env()


async def test_bot_never_pings_everyone():
    from pathlib import Path

    import discord

    from clashhero.bot import ClashHeroBot

    bot = ClashHeroBot(Config("t", "e", "p", "k", Path(":memory:"), None, 60, 60, 120, 600, "INFO"))
    merged = bot.allowed_mentions.merge(discord.AllowedMentions(users=True)).to_dict()
    assert "everyone" not in merged["parse"]
