from __future__ import annotations

from pathlib import Path

import click

from praecentor.controllers.errors import ControllerError
from praecentor.controllers.validation import validate_song


@click.command("validate")
@click.argument(
    "song_file", type=click.Path(path_type=Path, exists=True, dir_okay=False)
)
def validate_cmd(song_file: Path) -> None:
    try:
        validate_song(song_file, emit=click.echo)
    except ControllerError as exc:
        raise click.ClickException(str(exc)) from exc
