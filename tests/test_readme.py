"""The README is the operating manual; its load-bearing commands must exist."""

import subprocess
from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"


def _text() -> str:
    return README.read_text(encoding="utf-8")


def test_readme_sections():
    t = _text()
    for h in (
        "## Install",
        "## Rollback",
        "## Recovery",
        "## Shadow mode and the ship gate",
        "## Self-dependencies",
        "## Built by Claude Code",
    ):
        assert h in t
    assert "uv sync --frozen" in t
    assert "brew upgrade --" in t


def test_readme_load_bearing_commands_and_paths():
    t = _text()
    for needle in (
        "~/.config/morning-brew/config.toml",
        "local_digest",
        "fnox set",
        "morning-brew eval",
        "morning-brew install --dry-run",
        "pytest -m integration",
        "~/.local/state/morning-brew/",
        "~/Library/Logs/morning-brew/",
        "This repo was built entirely by Claude Code.",
    ):
        assert needle in t, needle


def test_readme_lists_every_config_key():
    import dataclasses

    from morning_brew.config import Config

    t = _text()
    for f in dataclasses.fields(Config):
        if f.name != "state_dir":  # never settable from the file
            assert f"`{f.name}`" in t, f.name


def test_no_print_outside_cli():
    root = Path(__file__).resolve().parent.parent
    res = subprocess.run(
        ["uv", "run", "ruff", "check", "--select", "T201", "src"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stdout + res.stderr
