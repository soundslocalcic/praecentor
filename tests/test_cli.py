from __future__ import annotations
from praecentor import __version__
import click
from click.testing import CliRunner

from praecentor.cli import main


def test_default_group_routes_empty_args_to_render() -> None:
    ctx = click.Context(main)

    name, _cmd, _args = main.resolve_command(ctx, [])

    assert name == "render"


def test_default_group_routes_unknown_first_token_to_render() -> None:
    ctx = click.Context(main)

    name, _cmd, args = main.resolve_command(ctx, ["song.yaml", "--help"])

    assert name == "render"
    assert args == ["song.yaml", "--help"]


def test_cli_exposes_version_flag() -> None:
    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output
