from __future__ import annotations

from pathlib import Path

import click

from praecentor.controllers.errors import ControllerError
from praecentor.controllers import tapping as tapping_controller


@click.group("tap")
def tap_cmd() -> None:
    """Interactive tap capture commands."""


@tap_cmd.command("lyrics")
@click.argument(
    "song_file", type=click.Path(path_type=Path, exists=True, dir_okay=False)
)
@click.option(
    "--audio",
    "audio_override",
    type=click.Path(path_type=Path, exists=False, dir_okay=False),
    help="Reference audio file for tap capture. Overrides song audio.file.",
)
@click.option(
    "--autosave-every",
    type=click.IntRange(1),
    default=1,
    show_default=True,
    help="Autosave tap map every N captured events.",
)
@click.option(
    "--resume-preroll-seconds",
    type=click.FloatRange(0.0),
    default=0.0,
    show_default=True,
    help="Start this many seconds before resume position when continuing a session.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Validate config and show resolved paths without starting capture.",
)
def tap_lyrics_cmd(
    song_file: Path,
    audio_override: Path | None,
    autosave_every: int,
    resume_preroll_seconds: float,
    dry_run: bool,
) -> None:
    try:
        tapping_controller.tap_lyrics(
            song_file,
            audio_override=audio_override,
            autosave_every=autosave_every,
            resume_preroll_seconds=resume_preroll_seconds,
            dry_run=dry_run,
            emit=click.echo,
            read_input=input,
        )
    except ControllerError as exc:
        raise click.ClickException(str(exc)) from exc


@tap_cmd.command("beats")
@click.argument(
    "song_file", type=click.Path(path_type=Path, exists=True, dir_okay=False)
)
@click.option(
    "--audio",
    "audio_override",
    type=click.Path(path_type=Path, exists=False, dir_okay=False),
    help="Reference audio file for beat capture. Overrides song audio.file.",
)
@click.option(
    "--autosave-every",
    type=click.IntRange(1),
    default=4,
    show_default=True,
    help="Autosave beat map every N captured beats.",
)
@click.option(
    "--resume-preroll-seconds",
    type=click.FloatRange(0.0),
    default=0.0,
    show_default=True,
    help="Start this many seconds before resume position when continuing a session.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Validate config and show resolved paths without starting capture.",
)
def tap_beats_cmd(
    song_file: Path,
    audio_override: Path | None,
    autosave_every: int,
    resume_preroll_seconds: float,
    dry_run: bool,
) -> None:
    try:
        tapping_controller.tap_beats(
            song_file,
            audio_override=audio_override,
            autosave_every=autosave_every,
            resume_preroll_seconds=resume_preroll_seconds,
            dry_run=dry_run,
            emit=click.echo,
            read_input=input,
        )
    except ControllerError as exc:
        raise click.ClickException(str(exc)) from exc
