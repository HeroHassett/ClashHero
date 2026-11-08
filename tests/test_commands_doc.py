from pathlib import Path

from clashhero.commands_doc import build


async def test_commands_page_is_up_to_date():
    page = Path(__file__).resolve().parents[1] / "docs" / "commands.md"
    assert page.read_text() == await build(), (
        "docs/commands.md is out of date: run `uv run python -m clashhero.commands_doc`"
    )
