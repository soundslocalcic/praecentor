"""Command-line entry point for Praecentor.

This module exposes the project CLI via Click, providing validation, tapping,
inspection, and rendering commands for working with song YAML files.
"""

import click

from praecentor import __version__
from praecentor.commands.convert import convert_cmd
from praecentor.commands.diagnose import diagnose_cmd
from praecentor.commands.render import render_cmd
from praecentor.commands.tap import tap_cmd
from praecentor.commands.timeline import timeline_cmd
from praecentor.commands.validate import validate_cmd


class DefaultToRenderGroup(click.Group):
    """Routes bare invocations to the render command."""

    def resolve_command(self, ctx: click.Context, args: list[str]):
        if not args:
            args = ["render"]
        elif args[0] not in self.commands and not args[0].startswith("-"):
            args = ["render", *args]

        return super().resolve_command(ctx, args)


@click.group(
    cls=DefaultToRenderGroup,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(version=__version__, prog_name="praecentor")
def main() -> None:
    """Praecentor command line interface."""


main.add_command(validate_cmd, name="validate")
main.add_command(tap_cmd, name="tap")
main.add_command(timeline_cmd, name="timeline")
main.add_command(render_cmd, name="render")
main.add_command(diagnose_cmd, name="diagnose")
main.add_command(convert_cmd, name="convert")
