from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any, Callable

from praecentor.audio import (
    BeatAnalysisError,
    analyze_beats_from_audio,
    infer_beat_seconds_from_beats,
)
from praecentor.controllers.errors import ControllerError
from praecentor.io.song import SongConfigError, load_yaml_file


def _parse_duration_seconds(raw: Any) -> float:
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str):
        raise ValueError("not a number or string")

    text = raw.strip()
    if not text:
        raise ValueError("empty string")

    if ":" not in text:
        return float(text)

    parts = [part.strip() for part in text.split(":")]
    if len(parts) == 2:
        return (int(parts[0]) * 60.0) + float(parts[1])
    if len(parts) == 3:
        return (
            (int(parts[0]) * 3600.0) + (int(parts[1]) * 60.0) + float(parts[2])
        )

    raise ValueError("unsupported timecode format")


def _positive_intervals(values: list[float]) -> list[float]:
    return [b - a for a, b in zip(values, values[1:]) if b - a > 0]


def _infer_start_bpm_hint(beats_cfg: dict[str, Any]) -> float | None:
    bpm_raw = beats_cfg.get("bpm")
    if isinstance(bpm_raw, (int, float)):
        bpm = float(bpm_raw)
        return bpm if bpm > 0 else None
    return None


def diagnose_beats(
    song_file: Path,
    *,
    start_bpm: float | None,
    dynamic_tempo: str,
    dynamic_tempo_smoothing: int | None,
    dynamic_tempo_sensitivity: float | None,
    apply_audio_offset: bool,
    show_beats: int,
    all_beats: bool,
    emit: Callable[[str], None] | None = None,
) -> None:
    emit = emit or (lambda _line: None)

    try:
        song_data = load_yaml_file(song_file)
    except SongConfigError as exc:
        raise ControllerError(str(exc)) from exc

    audio_cfg = (
        song_data.get("audio")
        if isinstance(song_data.get("audio"), dict)
        else {}
    )
    audio_file = audio_cfg.get("file")
    if not isinstance(audio_file, str) or not audio_file.strip():
        raise ControllerError("audio.file is required in song config")

    audio_path = (song_file.parent / audio_file).resolve()
    if not audio_path.exists():
        raise ControllerError(f"Audio file not found: {audio_path}")

    timing = (
        song_data.get("timing")
        if isinstance(song_data.get("timing"), dict)
        else {}
    )
    beats_cfg = (
        timing.get("beats") if isinstance(timing.get("beats"), dict) else {}
    )

    cfg_dynamic = bool(beats_cfg.get("auto_dynamic_tempo", False))
    if dynamic_tempo.lower() == "auto":
        resolved_dynamic = cfg_dynamic
    else:
        resolved_dynamic = dynamic_tempo.lower() == "on"

    cfg_smoothing_raw = beats_cfg.get("auto_dynamic_tempo_smoothing", 1)
    try:
        cfg_smoothing = int(cfg_smoothing_raw)
    except (TypeError, ValueError) as exc:
        raise ControllerError(
            "timing.beats.auto_dynamic_tempo_smoothing must be an integer >= 1"
        ) from exc
    if cfg_smoothing < 1:
        raise ControllerError(
            "timing.beats.auto_dynamic_tempo_smoothing must be an integer >= 1"
        )

    cfg_sensitivity_raw = beats_cfg.get("auto_dynamic_tempo_sensitivity", 1.0)
    try:
        cfg_sensitivity = float(cfg_sensitivity_raw)
    except (TypeError, ValueError) as exc:
        raise ControllerError(
            "timing.beats.auto_dynamic_tempo_sensitivity must be numeric between 0 and 1"
        ) from exc
    if cfg_sensitivity < 0 or cfg_sensitivity > 1:
        raise ControllerError(
            "timing.beats.auto_dynamic_tempo_sensitivity must be numeric between 0 and 1"
        )

    resolved_smoothing = (
        dynamic_tempo_smoothing
        if dynamic_tempo_smoothing is not None
        else cfg_smoothing
    )
    resolved_sensitivity = (
        dynamic_tempo_sensitivity
        if dynamic_tempo_sensitivity is not None
        else cfg_sensitivity
    )

    resolved_start_bpm = start_bpm
    if resolved_start_bpm is None:
        resolved_start_bpm = _infer_start_bpm_hint(beats_cfg)

    audio_offset_raw = audio_cfg.get("start_offset_seconds", 0.0)
    try:
        audio_offset_seconds = _parse_duration_seconds(audio_offset_raw)
    except (TypeError, ValueError) as exc:
        raise ControllerError(
            "audio.start_offset_seconds must be numeric or timecode"
        ) from exc
    if audio_offset_seconds < 0:
        raise ControllerError("audio.start_offset_seconds must be >= 0")

    try:
        raw_beats = analyze_beats_from_audio(
            audio_path,
            start_bpm=resolved_start_bpm,
            dynamic_tempo=resolved_dynamic,
            dynamic_tempo_smoothing=resolved_smoothing,
            dynamic_tempo_sensitivity=resolved_sensitivity,
        )
    except BeatAnalysisError as exc:
        raise ControllerError(str(exc)) from exc

    if apply_audio_offset and audio_offset_seconds > 0:
        beats = [beat - audio_offset_seconds for beat in raw_beats]
    else:
        beats = list(raw_beats)

    try:
        inferred_beat_seconds = infer_beat_seconds_from_beats(beats)
    except BeatAnalysisError:
        inferred_beat_seconds = 0.0

    intervals = _positive_intervals(beats)

    emit(f"song_file={song_file}")
    emit(f"audio_file={audio_path}")
    emit(f"analysis_dynamic_tempo={resolved_dynamic}")
    emit(f"analysis_dynamic_tempo_smoothing={resolved_smoothing}")
    emit(f"analysis_dynamic_tempo_sensitivity={resolved_sensitivity:.6f}")
    bpm_hint = (
        f"{resolved_start_bpm:.6f}"
        if resolved_start_bpm is not None
        else "none"
    )
    emit("analysis_start_bpm_hint=" + bpm_hint)
    emit(f"audio_start_offset_seconds={audio_offset_seconds:.6f}")
    emit(f"apply_audio_offset={apply_audio_offset}")
    emit(f"detected_beat_count={len(beats)}")
    emit(f"first_beat_seconds={beats[0]:.6f}")
    emit(f"last_beat_seconds={beats[-1]:.6f}")

    if inferred_beat_seconds > 0:
        emit(f"inferred_beat_seconds={inferred_beat_seconds:.6f}")
        emit(f"inferred_bpm={(60.0 / inferred_beat_seconds):.6f}")
    else:
        emit("inferred_beat_seconds=unavailable")
        emit("inferred_bpm=unavailable")

    if intervals:
        emit(f"interval_min_seconds={min(intervals):.6f}")
        emit(f"interval_max_seconds={max(intervals):.6f}")
        emit(f"interval_median_seconds={statistics.median(intervals):.6f}")
        if len(intervals) > 1:
            emit(f"interval_stdev_seconds={statistics.pstdev(intervals):.6f}")
        else:
            emit("interval_stdev_seconds=0.000000")

    rows_to_show = len(beats) if all_beats else min(len(beats), show_beats)
    emit("beat_rows_start")
    for idx in range(rows_to_show):
        beat = beats[idx]
        if idx == 0:
            emit(f"beat[{idx:04d}]={beat:.6f} delta_from_prev=none")
        else:
            delta = beat - beats[idx - 1]
            emit(f"beat[{idx:04d}]={beat:.6f} delta_from_prev={delta:.6f}")
    if rows_to_show < len(beats):
        emit(f"beat_rows_truncated={len(beats) - rows_to_show}")
    emit("beat_rows_end")
