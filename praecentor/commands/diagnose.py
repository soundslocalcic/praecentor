from __future__ import annotations

from pathlib import Path

import click

from praecentor.audio import (
    analyze_beats_from_audio,
    infer_beat_seconds_from_beats,
)
from praecentor.controllers.errors import ControllerError
from praecentor.controllers import diagnostics as diagnostics_controller


@click.group("diagnose")
def diagnose_cmd() -> None:
    """Diagnostic tooling for timing and rendering internals."""


@diagnose_cmd.command("beats")
@click.argument(
    "song_file", type=click.Path(path_type=Path, exists=True, dir_okay=False)
)
@click.option(
    "--start-bpm",
    type=click.FloatRange(min=0.001),
    default=None,
    help="Override librosa start BPM hint (defaults to timing.beats.bpm when numeric).",
)
@click.option(
    "--dynamic-tempo",
    type=click.Choice(["auto", "on", "off"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="Use dynamic tempo tracking: auto follows song config, on/off forces mode.",
)
@click.option(
    "--dynamic-tempo-smoothing",
    type=click.IntRange(min=1),
    default=None,
    help="Median smoothing window for dynamic BPM curve (1 disables smoothing).",
)
@click.option(
    "--dynamic-tempo-sensitivity",
    type=click.FloatRange(min=0.0, max=1.0),
    default=None,
    help="Blend factor for dynamic BPM curve: 1.0 full dynamic, 0.0 fixed reference.",
)
@click.option(
    "--apply-audio-offset/--raw-audio-time",
    default=True,
    show_default=True,
    help="Apply audio.start_offset_seconds shift like render does.",
)
@click.option(
    "--show-beats",
    type=click.IntRange(min=1),
    default=64,
    show_default=True,
    help="How many beat rows to print before truncating.",
)
@click.option(
    "--all-beats",
    is_flag=True,
    default=False,
    help="Print every detected beat row.",
)
def diagnose_beats_cmd(
    song_file: Path,
    start_bpm: float | None,
    dynamic_tempo: str,
    dynamic_tempo_smoothing: int | None,
    dynamic_tempo_sensitivity: float | None,
    apply_audio_offset: bool,
    show_beats: int,
    all_beats: bool,
) -> None:
    diagnostics_controller.analyze_beats_from_audio = analyze_beats_from_audio
    diagnostics_controller.infer_beat_seconds_from_beats = (
        infer_beat_seconds_from_beats
    )

    try:
        diagnostics_controller.diagnose_beats(
            song_file,
            start_bpm=start_bpm,
            dynamic_tempo=dynamic_tempo,
            dynamic_tempo_smoothing=dynamic_tempo_smoothing,
            dynamic_tempo_sensitivity=dynamic_tempo_sensitivity,
            apply_audio_offset=apply_audio_offset,
            show_beats=show_beats,
            all_beats=all_beats,
            emit=click.echo,
        )
    except ControllerError as exc:
        raise click.ClickException(str(exc)) from exc
