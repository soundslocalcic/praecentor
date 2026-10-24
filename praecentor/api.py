"""Public API helpers for Praecentor workflows.

These wrappers provide a small, importable interface for the main tasks the
project performs: rendering a song, tapping lyric/beat timing, massaging lyric
text into event YAML, and diagnosing timing issues.
"""

from __future__ import annotations

from typing import Any

from praecentor.controllers import conversion, diagnostics, rendering, tapping
from praecentor.models import (
    ConvertLyricsRequest,
    DiagnoseBeatsRequest,
    RenderRequest,
    TapBeatsRequest,
    TapLyricsRequest,
)


def render_video(request: RenderRequest) -> Any:
    """Render a song definition to the configured output videos.

    Args:
        request: Structured render settings for the song, output file, layout, and
            ffmpeg options.

    Returns:
        The controller result returned by the render pipeline.
    """
    return rendering.render(
        request.song_file,
        request.layout_file,
        request.beat_seconds,
        request.countdown_beats,
        request.output_video,
        request.from_seconds,
        request.to_seconds,
        request.maestro_only,
        request.video_encoder,
        request.video_preset,
        request.video_crf,
        request.video_bitrate,
        request.background_hwaccel,
        request.frame_queue_size,
        request.stem_duration_tolerance,
        request.ignore_stem_length_mismatch,
    )


def tap_lyrics(request: TapLyricsRequest) -> Any:
    """Open the interactive lyric-tapping workflow for a song.

    This is the same flow exposed by the CLI command ``praecentor tap lyrics``.
    """
    return tapping.tap_lyrics(
        request.song_file,
        audio_override=request.audio_override,
        autosave_every=request.autosave_every,
        resume_preroll_seconds=request.resume_preroll_seconds,
        dry_run=request.dry_run,
    )


def tap_beats(request: TapBeatsRequest) -> Any:
    """Open the interactive beat-tapping workflow for a song."""
    return tapping.tap_beats(
        request.song_file,
        audio_override=request.audio_override,
        autosave_every=request.autosave_every,
        resume_preroll_seconds=request.resume_preroll_seconds,
        dry_run=request.dry_run,
    )


def convert_lyrics(request: ConvertLyricsRequest) -> list[dict[str, Any]]:
    """Convert plain text lyrics into the event structure used by Praecentor."""
    return conversion.convert_lyrics_text_to_events(
        request.text, max_line_chars=request.max_line_chars
    )


def diagnose_beats(request: DiagnoseBeatsRequest) -> Any:
    """Inspect detected beat timing and audio alignment for a song."""
    return diagnostics.diagnose_beats(
        request.song_file,
        start_bpm=request.start_bpm,
        dynamic_tempo=request.dynamic_tempo,
        dynamic_tempo_smoothing=request.dynamic_tempo_smoothing,
        dynamic_tempo_sensitivity=request.dynamic_tempo_sensitivity,
        apply_audio_offset=request.apply_audio_offset,
        show_beats=request.show_beats,
        all_beats=request.all_beats,
    )
