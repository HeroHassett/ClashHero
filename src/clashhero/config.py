"""Environment-driven configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    pass


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable: {name} (see .env.example)")
    return value


def parse_tokens(raw: str) -> tuple[str, ...]:
    """Comma/whitespace separated API tokens -> tuple, ignoring blanks."""
    return tuple(t for t in raw.replace(",", " ").split() if t)


@dataclass(frozen=True)
class Config:
    discord_token: str
    coc_email: str
    coc_password: str
    coc_key_name: str
    database_path: Path
    dev_guild_id: int | None
    clan_poll_seconds: int
    player_poll_seconds: int
    war_poll_seconds: int
    raid_poll_seconds: int
    log_level: str
    coc_api_tokens: tuple[str, ...] = ()
    terms_url: str = ""
    privacy_url: str = ""
    support_url: str = ""

    @property
    def uses_api_tokens(self) -> bool:
        return bool(self.coc_api_tokens)

    @classmethod
    def from_env(cls) -> Config:
        load_dotenv()
        dev_guild = os.getenv("DEV_GUILD_ID", "").strip()
        tokens = parse_tokens(os.getenv("COC_API_TOKENS", "") or os.getenv("COC_API_TOKEN", ""))
        email = os.getenv("COC_EMAIL", "").strip()
        password = os.getenv("COC_PASSWORD", "").strip()
        if not tokens and not (email and password):
            raise ConfigError(
                "Set COC_API_TOKENS to your Clash of Clans API key (recommended), or set both "
                "COC_EMAIL and COC_PASSWORD for your developer.clashofclans.com account. "
                "See .env.example."
            )
        return cls(
            discord_token=_required("DISCORD_TOKEN"),
            coc_email=email,
            coc_password=password,
            coc_key_name=os.getenv("COC_KEY_NAME", "clashhero"),
            database_path=Path(os.getenv("DATABASE_PATH", "data/clashhero.db")),
            dev_guild_id=int(dev_guild) if dev_guild else None,
            clan_poll_seconds=int(os.getenv("CLAN_POLL_SECONDS", "60")),
            player_poll_seconds=int(os.getenv("PLAYER_POLL_SECONDS", "60")),
            war_poll_seconds=int(os.getenv("WAR_POLL_SECONDS", "120")),
            raid_poll_seconds=int(os.getenv("RAID_POLL_SECONDS", "600")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            coc_api_tokens=tokens,
            terms_url=os.getenv("TERMS_URL", "").strip(),
            privacy_url=os.getenv("PRIVACY_URL", "").strip(),
            support_url=os.getenv("SUPPORT_URL", "").strip(),
        )
