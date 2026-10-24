from __future__ import annotations

from pathlib import Path

import click

from praecentor.controllers.timeline import inspect_timeline


@click.command("timeline")
@click.argument(
    "song_file", type=click.Path(path_type=Path, exists=True, dir_okay=False)
)
def timeline_cmd(song_file: Path) -> None:
    inspect_timeline(song_file, emit=click.echo)
