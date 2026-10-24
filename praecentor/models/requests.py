from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class RenderRequest:
    song_file: Path
    layout_file: Path | None = None
    beat_seconds: float | None = None
    countdown_beats: int | None = None
    output_video: Path | None = None
    from_seconds: str | None = None
    to_seconds: str | None = None
    maestro_only: bool = False
    video_encoder: str = "libx264"
    video_preset: str = "medium"
    video_crf: int | None = None
    video_bitrate: str | None = None
    background_hwaccel: str | None = None
    frame_queue_size: int = 0
    stem_duration_tolerance: float = 0.02
    ignore_stem_length_mismatch: bool = False


@dataclass(slots=True)
class TapLyricsRequest:
    song_file: Path
    audio_override: Path | None = None
    autosave_every: int = 1
    resume_preroll_seconds: float = 0.0
    dry_run: bool = False


@dataclass(slots=True)
class TapBeatsRequest:
    song_file: Path
    audio_override: Path | None = None
    autosave_every: int = 4
    resume_preroll_seconds: float = 0.0
    dry_run: bool = False


@dataclass(slots=True)
class ConvertLyricsRequest:
    text: str
    max_line_chars: int = 32


@dataclass(slots=True)
class DiagnoseBeatsRequest:
    song_file: Path
    start_bpm: float | None = None
    dynamic_tempo: str = "auto"
    dynamic_tempo_smoothing: int | None = None
    dynamic_tempo_sensitivity: float | None = None
    apply_audio_offset: bool = True
    show_beats: int = 64
    all_beats: bool = False
