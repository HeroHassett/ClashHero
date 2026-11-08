"""Player/clan tag helpers."""

from __future__ import annotations

import re

_VALID = re.compile(r"^#[0289PYLQGRJCUV]{3,}$")


def normalize_tag(raw: str) -> str:
    """Uppercase, strip whitespace, ensure leading '#', and map the letter O to zero."""
    tag = raw.strip().upper().replace("O", "0").lstrip("#")
    return f"#{tag}"


def is_valid_tag(tag: str) -> bool:
    return bool(_VALID.match(normalize_tag(tag)))
