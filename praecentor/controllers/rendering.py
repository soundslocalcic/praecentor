from __future__ import annotations

from bisect import bisect_left
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import platform
import queue
import re
import shutil
import statistics
import subprocess
import tempfile
import threading
from time import perf_counter
from pathlib import Path
import json
from typing import Any, Callable
import zipfile

from PIL import Image

from praecentor.audio import (
    BeatAnalysisError,
    analyze_beats_from_audio,
    analyze_click_track_grid_from_audio,
    infer_beat_seconds_from_beats,
)
from praecentor.io.layout import load_layout_config, resolve_layout_path
from praecentor.io.song import SongConfigError, load_yaml_file
from praecentor.io.tap_map import load_beat_tap_map, load_tap_map
from praecentor.render.background_layers import build_background_layer_renderer
from praecentor.render.cue_frames import render_cue_frames
from praecentor.render.title_card import render_title_countdown_frames
from praecentor.controllers.errors import ControllerError

MAX_RENDER_FPS = 24
DEFAULT_VIDEO_HOLD_PAD_SECONDS = 3600
DEFAULT_VIDEO_ENCODER = "libx264"
DEFAULT_VIDEO_PRESET = "medium"
DEFAULT_FRAME_QUEUE_SIZE = 0
DEFAULT_STEM_DURATION_TOLERANCE_SECONDS = 0.02
DEFAULT_IGNORE_STEM_LENGTH_MISMATCH = False
DEFAULT_MUX_MAX_WORKERS = 8
DEFAULT_APPLE_SILICON_OPTIMIZE = False
_LYRIC_PART_RE = re.compile(r"^\s*\[([abcABC])\]\s?(.*)$")


class _NoOpProgress:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def update(self, _amount: int) -> None:
        return


def _noop_progressbar(*, length: int, label: str):
    return _NoOpProgress()


def _visible_lyric_lines(raw_lyric: str) -> list[str]:
    out: list[str] = []
    for raw_line in raw_lyric.splitlines():
        match = _LYRIC_PART_RE.match(raw_line)
        if match is not None:
            out.append(match.group(2))
        else:
            out.append(raw_line)
    return out


def _lyric_line_lengths_for_validation(
    raw_lyric: str,
) -> list[tuple[str, int]]:
    """Return display lines with effective lengths for max-line validation.

    Continuation lines aligned under a preceding [a]/[b]/[c] marker are measured
    after subtracting that marker's consumed prefix width.
    """
    out: list[tuple[str, int]] = []
    continuation_indent: int | None = None
    for raw_line in raw_lyric.splitlines():
        match = _LYRIC_PART_RE.match(raw_line)
        if match is not None:
            display_line = match.group(2)
            out.append((display_line, len(display_line.rstrip())))
            continuation_indent = match.start(2)
            continue

        display_line = raw_line
        display_line_rstrip = display_line.rstrip()
        line_length = len(display_line_rstrip)
        if continuation_indent is not None:
            leading_spaces = len(raw_line) - len(raw_line.lstrip(" "))
            if leading_spaces >= continuation_indent:
                line_length = max(0, line_length - continuation_indent)
            else:
                continuation_indent = None
        out.append((display_line, line_length))
    return out


def _parse_duration_seconds(raw: Any) -> float:
    if isinstance(raw, (int, float)):
        return float(raw)

    if not isinstance(raw, str):
        raise ValueError("not a number or string")

    text = raw.strip()
    if not text:
        raise ValueError("empty string")

    unit_match = re.fullmatch(
        r"([0-9]+(?:\.[0-9]+)?)\s*(us|µs|ms|s)?", text, re.IGNORECASE
    )
    if unit_match:
        magnitude = float(unit_match.group(1))
        unit = (unit_match.group(2) or "s").lower()
        if unit in {"us", "µs"}:
            return magnitude / 1_000_000.0
        if unit == "ms":
            return magnitude / 1_000.0
        return magnitude

    if ":" not in text:
        return float(text)

    parts = [p.strip() for p in text.split(":")]
    if len(parts) == 2:
        minutes = int(parts[0])
        seconds = float(parts[1])
        return (minutes * 60.0) + seconds

    if len(parts) == 3:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])
        return (hours * 3600.0) + (minutes * 60.0) + seconds

    raise ValueError("unsupported timecode format")


def _beats_per_bar_from_timing(timing: dict[str, Any]) -> int:
    beats_per_bar_raw = (
        timing.get("beats_per_bar") if isinstance(timing, dict) else None
    )
    if beats_per_bar_raw is not None:
        try:
            beats_per_bar = int(beats_per_bar_raw)
        except (TypeError, ValueError):
            beats_per_bar = 0
        if beats_per_bar > 0:
            return beats_per_bar

    raw = timing.get("time_signature") if isinstance(timing, dict) else None
    if isinstance(raw, str) and "/" in raw:
        left = raw.split("/", 1)[0].strip()
        try:
            beats = int(left)
            if beats > 0:
                return beats
        except ValueError:
            pass
    return 4


def _default_beats_per_phrase_from_timing(
    beats_cfg: dict[str, Any],
    *,
    beats_per_bar: int,
    click_track_enabled: bool,
) -> int:
    raw = beats_cfg.get("beats_per_phrase")
    if raw is not None:
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            return max(1, int(beats_per_bar)) if click_track_enabled else 4
        if parsed > 0:
            return parsed
    return max(1, int(beats_per_bar)) if click_track_enabled else 4


def _median_positive_delta(values: list[float]) -> float | None:
    deltas = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    positive = [delta for delta in deltas if delta > 0]
    if not positive:
        return None
    return statistics.median(positive)


def _smooth_beat_times(beat_times: list[float], *, window: int) -> list[float]:
    if window <= 1 or len(beat_times) < 3:
        return beat_times

    interval_window = int(window)
    if interval_window % 2 == 0:
        interval_window += 1

    intervals = [
        beat_times[i + 1] - beat_times[i] for i in range(len(beat_times) - 1)
    ]
    if not intervals:
        return beat_times

    half = interval_window // 2
    smoothed_intervals: list[float] = []
    for idx in range(len(intervals)):
        left = max(0, idx - half)
        right = min(len(intervals), idx + half + 1)
        smoothed_intervals.append(sum(intervals[left:right]) / (right - left))

    smoothed = [float(beat_times[0])]
    for interval in smoothed_intervals:
        smoothed.append(smoothed[-1] + interval)
    return smoothed


def _normalize_click_track_beats_to_audio_start(
    beat_times: list[float],
    *,
    max_shift_beats: int = 8,
    boundary_tolerance_ratio: float = 0.35,
) -> tuple[list[float], int]:
    """Shift auto-detected click beats so audio start aligns with a beat boundary.

    librosa can detect the first beat a few beats late on percussive click tracks.
    When the first detected beat is close to an integer number of beat intervals
    away from t=0, we shift the whole beat series earlier by that many beats.
    """
    if len(beat_times) < 2:
        return beat_times, 0

    first = float(beat_times[0])
    if first <= 0:
        return beat_times, 0

    median_interval = _median_positive_delta(beat_times)
    if median_interval is None or median_interval <= 0:
        return beat_times, 0

    tolerance = max(0.001, median_interval * float(boundary_tolerance_ratio))

    # Click tracks are authored to start on beat 1. If analysis reports a tiny
    # positive first beat (common with MP3 encoder lead-in), snap to t=0.
    if first <= tolerance:
        shifted = [float(t - first) for t in beat_times]
        return shifted, 0

    capped_window = float(max(1, int(max_shift_beats))) * median_interval
    if first > capped_window:
        return beat_times, 0

    missing_beats = int(round(first / median_interval))
    if missing_beats <= 0:
        return beat_times, 0

    shift_seconds = missing_beats * median_interval
    aligned_first = first - shift_seconds
    if aligned_first < 0 or aligned_first > tolerance:
        return beat_times, 0

    # Snap the accepted click-track boundary exactly to t=0 so beat HUD
    # visuals start at the beat boundary instead of a small residual offset.
    shift_seconds += aligned_first
    shifted = [float(t - shift_seconds) for t in beat_times]
    return shifted, missing_beats


def _beat_state_at_time(
    current_time: float,
    *,
    beat_seconds: float,
    beat_times_seconds: list[float] | None,
    beat_anchor_index: int,
) -> tuple[int, float]:
    fallback_interval = max(0.001, beat_seconds)
    if beat_times_seconds:
        median_interval = _median_positive_delta(beat_times_seconds)
        interval = max(
            fallback_interval,
            (
                median_interval
                if median_interval is not None
                else fallback_interval
            ),
        )

        idx = 0
        while (
            idx < len(beat_times_seconds)
            and beat_times_seconds[idx] <= current_time
        ):
            idx += 1
        idx -= 1

        if idx < 0:
            prev_t = beat_times_seconds[0] - fallback_interval
            next_t = beat_times_seconds[0]
            raw_beat_index = -1
        elif idx >= len(beat_times_seconds) - 1:
            offset = max(0.0, current_time - beat_times_seconds[-1])
            extra_beats = int(offset // interval)
            raw_beat_index = (len(beat_times_seconds) - 1) + extra_beats
            prev_t = beat_times_seconds[-1] + (extra_beats * fallback_interval)
            next_t = prev_t + fallback_interval
        else:
            prev_t = beat_times_seconds[idx]
            next_t = beat_times_seconds[idx + 1]
            raw_beat_index = idx
    else:
        raw_beat_index = int(current_time // fallback_interval)
        prev_t = raw_beat_index * fallback_interval
        next_t = prev_t + fallback_interval

    beat_index = raw_beat_index - beat_anchor_index
    interval = max(0.001, next_t - prev_t)
    seconds_to_next_beat = max(0.0, next_t - current_time)
    remaining_ratio = max(0.0, min(1.0, seconds_to_next_beat / interval))
    return beat_index, remaining_ratio


def _time_at_raw_beat_index(
    raw_beat_index: int,
    *,
    beat_seconds: float,
    beat_times_seconds: list[float] | None,
) -> float:
    fallback_interval = max(0.001, beat_seconds)
    idx = int(raw_beat_index)
    if beat_times_seconds:
        median_interval = _median_positive_delta(beat_times_seconds)
        if median_interval is not None:
            fallback_interval = max(0.001, median_interval)

        if idx < 0:
            return beat_times_seconds[0] + (idx * fallback_interval)
        if idx < len(beat_times_seconds):
            return beat_times_seconds[idx]

        extra_beats = idx - (len(beat_times_seconds) - 1)
        return beat_times_seconds[-1] + (extra_beats * fallback_interval)

    return idx * fallback_interval


def _beat_position_at_time(
    current_time: float,
    *,
    beat_seconds: float,
    beat_times_seconds: list[float] | None,
) -> float:
    beat_index, remaining_ratio = _beat_state_at_time(
        current_time,
        beat_seconds=beat_seconds,
        beat_times_seconds=beat_times_seconds,
        beat_anchor_index=0,
    )
    return float(beat_index) + (1.0 - remaining_ratio)


def _infer_event_beats_per_bar_from_click(
    *,
    event_starts_seconds: list[float],
    beat_seconds: float,
    beat_times_seconds: list[float] | None,
    bar_start_times_seconds: list[float],
    fallback_beats_per_bar: int,
) -> list[int]:
    if not event_starts_seconds:
        return []

    fallback = max(1, int(fallback_beats_per_bar))
    bars = sorted(float(t) for t in bar_start_times_seconds)
    if len(bars) < 2:
        return [fallback] * len(event_starts_seconds)

    intervals: list[tuple[float, float, int]] = []
    for idx in range(len(bars) - 1):
        start = bars[idx]
        end = bars[idx + 1]
        if end <= start:
            continue
        start_pos = _beat_position_at_time(
            start,
            beat_seconds=beat_seconds,
            beat_times_seconds=beat_times_seconds,
        )
        end_pos = _beat_position_at_time(
            end,
            beat_seconds=beat_seconds,
            beat_times_seconds=beat_times_seconds,
        )
        beats_in_bar = int(round(max(1.0, end_pos - start_pos)))
        if beats_in_bar < 1 or beats_in_bar > 16:
            beats_in_bar = fallback
        intervals.append((start, end, beats_in_bar))

    if not intervals:
        return [fallback] * len(event_starts_seconds)

    def _canonicalize_detected_beats_per_bar(raw_beats_per_bar: int) -> int:
        beats_in_bar = max(1, int(raw_beats_per_bar))
        while beats_in_bar > 4 and beats_in_bar % 2 == 0:
            beats_in_bar //= 2
        if beats_in_bar in {3, 4}:
            return beats_in_bar
        return fallback

    resolved: list[int] = []
    for event_start in event_starts_seconds:
        value = intervals[0][2]
        for start, end, beats_in_bar in intervals:
            value = beats_in_bar
            if event_start < end:
                break
        resolved.append(_canonicalize_detected_beats_per_bar(value))
    return resolved


def _event_beats_per_bar_with_overrides(
    *,
    song_events: list[dict[str, Any]],
    inferred_event_beats_per_bar: list[int],
    fallback_beats_per_bar: int,
) -> list[int]:
    """Resolve per-event meter, allowing event-level overrides when provided."""
    fallback = max(1, int(fallback_beats_per_bar))
    if inferred_event_beats_per_bar:
        resolved = [
            max(1, int(value)) for value in inferred_event_beats_per_bar
        ]
    else:
        resolved = [fallback] * len(song_events)

    if len(resolved) < len(song_events):
        resolved.extend([fallback] * (len(song_events) - len(resolved)))
    elif len(resolved) > len(song_events):
        resolved = resolved[: len(song_events)]

    current = fallback
    for idx, event in enumerate(song_events):
        override_value: int | None = None

        raw_beats_per_bar = event.get("beats_per_bar")
        if raw_beats_per_bar not in {None, ""}:
            try:
                parsed = int(raw_beats_per_bar)
            except (TypeError, ValueError):
                parsed = 0
            if parsed > 0:
                override_value = parsed

        if override_value is None:
            raw_signature = event.get("time_signature")
            if isinstance(raw_signature, str) and "/" in raw_signature:
                numerator_text = raw_signature.split("/", 1)[0].strip()
                try:
                    numerator = int(numerator_text)
                except ValueError:
                    numerator = 0
                if numerator > 0:
                    override_value = numerator

        if override_value is not None:
            current = max(1, override_value)
            resolved[idx] = current
        elif idx > 0:
            # Carry prior value so a meter change persists after its declaration event.
            resolved[idx] = resolved[idx - 1]

    return resolved


def _build_loop_bar_timeline(
    *,
    event_starts_seconds: list[float],
    beat_seconds: float,
    beat_times_seconds: list[float] | None,
    event_beats_per_bar: list[int],
    fallback_beats_per_bar: int,
    bar_start_times_seconds: list[float] | None = None,
) -> list[dict[str, Any]]:
    """Build bar anchors for Maestro looping across tempo and meter changes."""
    if not beat_times_seconds:
        return []

    beat_times = [float(t) for t in beat_times_seconds]
    if not beat_times:
        return []

    fallback = max(1, int(fallback_beats_per_bar))

    if bar_start_times_seconds:
        bar_starts = sorted({float(t) for t in bar_start_times_seconds})
        if bar_starts:
            if bar_starts[0] > beat_times[0]:
                bar_starts.insert(0, beat_times[0])

            inferred_for_starts = _infer_event_beats_per_bar_from_click(
                event_starts_seconds=bar_starts,
                beat_seconds=beat_seconds,
                beat_times_seconds=beat_times,
                bar_start_times_seconds=bar_starts,
                fallback_beats_per_bar=fallback,
            )

            timeline: list[dict[str, Any]] = []
            cumulative_beats = 0
            for idx, start in enumerate(bar_starts):
                beats_in_bar = (
                    inferred_for_starts[idx]
                    if idx < len(inferred_for_starts)
                    else (
                        inferred_for_starts[-1]
                        if inferred_for_starts
                        else fallback
                    )
                )
                beats_in_bar = max(1, int(beats_in_bar))
                timeline.append(
                    {
                        "bar": idx + 1,
                        "time_seconds": max(0.0, float(start)),
                        "beat_index": cumulative_beats,
                        "beats_per_bar": beats_in_bar,
                    }
                )
                cumulative_beats += beats_in_bar
            return timeline

    effective_event_beats = [fallback] * max(1, len(event_starts_seconds))
    if event_beats_per_bar:
        for idx in range(
            min(len(effective_event_beats), len(event_beats_per_bar))
        ):
            effective_event_beats[idx] = max(1, int(event_beats_per_bar[idx]))

    meter_changes: list[tuple[int, int]] = [(0, effective_event_beats[0])]
    prior = effective_event_beats[0]
    for idx in range(1, len(event_starts_seconds)):
        candidate = effective_event_beats[idx]
        if candidate == prior:
            continue
        beat_pos = _beat_position_at_time(
            float(event_starts_seconds[idx]),
            beat_seconds=beat_seconds,
            beat_times_seconds=beat_times,
        )
        beat_idx = max(0, int(round(beat_pos)))
        if beat_idx <= meter_changes[-1][0]:
            prior = candidate
            continue
        meter_changes.append((beat_idx, candidate))
        prior = candidate

    timeline: list[dict[str, Any]] = []
    bar_number = 1
    beat_idx = 0
    change_ptr = 0
    current_beats_per_bar = meter_changes[0][1]
    max_beat_index = len(beat_times)

    while beat_idx < max_beat_index:
        while (change_ptr + 1) < len(meter_changes) and meter_changes[
            change_ptr + 1
        ][0] <= beat_idx:
            change_ptr += 1
            current_beats_per_bar = meter_changes[change_ptr][1]

        timeline.append(
            {
                "bar": bar_number,
                "time_seconds": max(0.0, float(beat_times[beat_idx])),
                "beat_index": int(beat_idx),
                "beats_per_bar": int(current_beats_per_bar),
            }
        )

        next_idx = beat_idx + current_beats_per_bar
        if (change_ptr + 1) < len(meter_changes):
            next_change_idx = meter_changes[change_ptr + 1][0]
            if beat_idx < next_change_idx < next_idx:
                next_idx = next_change_idx

        if next_idx <= beat_idx:
            next_idx = beat_idx + 1

        beat_idx = next_idx
        bar_number += 1

    return timeline


def _cue_tail_to_phrase_boundary_seconds(
    *,
    last_event_start_seconds: float,
    beat_seconds: float,
    beat_times_seconds: list[float] | None,
    beat_anchor_index: int,
    beats_per_phrase: int,
) -> float:
    if beats_per_phrase <= 0:
        return 0.0
    beat_index, beat_remaining_ratio = _beat_state_at_time(
        last_event_start_seconds,
        beat_seconds=beat_seconds,
        beat_times_seconds=beat_times_seconds,
        beat_anchor_index=beat_anchor_index,
    )
    beat_fraction_elapsed = 1.0 - beat_remaining_ratio
    phrase_progress_beats = (
        beat_index % beats_per_phrase
    ) + beat_fraction_elapsed
    remainder = phrase_progress_beats % beats_per_phrase
    if remainder <= 0.000001:
        return 0.0
    return (beats_per_phrase - remainder) * beat_seconds


def _probe_audio_duration_seconds(
    audio_path: Path, ffmpeg_path: str
) -> float | None:
    ffprobe_bin = os.getenv("PRAECENTOR_FFPROBE_BIN")
    ffprobe_candidates: list[str] = []
    if ffprobe_bin:
        ffprobe_candidates.append(ffprobe_bin)
    ffmpeg_parent = Path(ffmpeg_path).parent
    ffprobe_candidates.append(str(ffmpeg_parent / "ffprobe"))
    ffprobe_candidates.append("ffprobe")

    for candidate in ffprobe_candidates:
        resolved = (
            shutil.which(candidate)
            if not os.path.isabs(candidate)
            else candidate
        )
        if not resolved:
            continue
        try:
            result = subprocess.run(
                [
                    str(resolved),
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(audio_path),
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                continue
            value = float(result.stdout.strip())
            if value > 0:
                return value
        except (OSError, ValueError):
            continue
    return None


def _format_ffmpeg_seconds(value: float) -> str:
    return f"{value:.6f}"


def _peak_normalization_gain_db(
    *,
    ffmpeg_path: str,
    input_sources: list[str],
    filter_graph: str,
) -> float | None:
    probe_cmd = [str(ffmpeg_path), "-hide_banner", "-y"]
    for source in input_sources:
        probe_cmd.extend(["-i", str(source)])
    probe_cmd.extend(
        [
            "-filter_complex",
            f"{filter_graph};[m]volumedetect[probe]",
            "-map",
            "[probe]",
            "-f",
            "null",
            "-",
        ]
    )

    probe_result = subprocess.run(
        probe_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if probe_result.returncode != 0:
        return None

    # `volumedetect` reports `max_volume`; parse that first for peak normalization.
    for line in probe_result.stdout.splitlines():
        max_volume_match = re.search(
            r"max_volume:\s*([-+]?\d+(?:\.\d+)?)\s*dB",
            line,
            re.IGNORECASE,
        )
        if max_volume_match:
            peak_db = float(max_volume_match.group(1))
            return -0.1 - peak_db

    # Backward-compatible fallback for historical output styles.
    for line in probe_result.stdout.splitlines():
        match = re.search(
            r"peak_(?:i|s):\s*([-+]?\d+(?:\.\d+)?)\s*dB", line, re.IGNORECASE
        )
        if match:
            peak_db = float(match.group(1))
            return -0.1 - peak_db
    return None


def _build_probe_filter_graph_for_normalization(
    *,
    audio_delay_ms: int,
    selected_tracks: list[dict[str, Any]] | None,
) -> str:
    if selected_tracks is None:
        return f"[0:a]adelay={audio_delay_ms}:all=1[m]"

    gain_labels: list[str] = []
    filter_parts: list[str] = []
    for input_idx, track in enumerate(selected_tracks):
        gain = float(track["gain"])
        label = f"g{input_idx}"
        filter_parts.append(f"[{input_idx}:a]volume={gain:.6f}[{label}]")
        gain_labels.append(f"[{label}]")

    if len(gain_labels) == 1:
        filter_parts.append(
            f"{gain_labels[0]}adelay={audio_delay_ms}:all=1[m]"
        )
    else:
        mixed_labels = "".join(gain_labels)
        filter_parts.append(
            f"{mixed_labels}amix=inputs={len(gain_labels)}:normalize=0[m]"
        )
        filter_parts.append(f"[m]adelay={audio_delay_ms}:all=1[m]")

    return ";".join(filter_parts)


def _infer_beat_seconds_from_beats_config(
    beats: dict[str, Any],
) -> tuple[float | None, str]:
    def _first_bpm_from_config() -> tuple[float | None, str | None]:
        bpm_raw = beats.get("bpm")
        if isinstance(bpm_raw, (int, float)):
            bpm_value = float(bpm_raw)
            if bpm_value <= 0:
                return None, "timing.beats.bpm must be greater than zero"
            return bpm_value, None
        if isinstance(bpm_raw, dict):
            beats_per_bar = _beats_per_bar_from_timing(beats)
            changes, error = _parse_bpm_changes(
                bpm_raw, beats_per_bar=beats_per_bar
            )
            if error is not None:
                return None, error
            if not changes:
                return None, "timing.beats.bpm map must not be empty"
            return changes[0][1], None
        return None, "timing.beats.bpm is missing or invalid"

    beat_seconds_raw = beats.get("beat_seconds")
    if beat_seconds_raw is not None:
        if isinstance(beat_seconds_raw, str):
            beat_seconds_mode = beat_seconds_raw.strip().lower()
            if beat_seconds_mode == "auto":
                return None, "auto beat analysis requested"
            if beat_seconds_mode in {"bpm", "from_bpm"}:
                bpm_value, bpm_error = _first_bpm_from_config()
                if bpm_error is not None or bpm_value is None:
                    return None, "timing.beats.beat_seconds=bpm but " + (
                        bpm_error or "timing.beats.bpm is invalid"
                    )
                return 60.0 / bpm_value, "derived from timing.beats.bpm"
        try:
            beat_seconds = _parse_duration_seconds(beat_seconds_raw)
        except (TypeError, ValueError):
            return (
                None,
                "timing.beats.beat_seconds is present but invalid "
                "(use number or MM:SS.xx)",
            )
        if beat_seconds <= 0:
            return None, "timing.beats.beat_seconds must be greater than zero"
        return beat_seconds, "from timing.beats.beat_seconds"

    beats_per_second_raw = beats.get("beats_per_second")
    if beats_per_second_raw is not None:
        try:
            beats_per_second = float(beats_per_second_raw)
        except (TypeError, ValueError):
            return None, "timing.beats.beats_per_second is present but invalid"
        if beats_per_second <= 0:
            return (
                None,
                "timing.beats.beats_per_second must be greater than zero",
            )
        return 1.0 / beats_per_second, "from timing.beats_per_second"

    source = beats.get("source")
    if source == "manual_grid":
        bpm_value, bpm_error = _first_bpm_from_config()
        if bpm_error is not None or bpm_value is None:
            return None, "timing.beats.source=manual_grid but " + (
                bpm_error or "timing.beats.bpm is invalid"
            )

        return 60.0 / bpm_value, "derived from timing.beats.bpm"

    return None, f"no direct beat inference for timing.beats.source={source!r}"


def _apply_auto_tempo_factor(
    beat_times: list[float], factor: float
) -> list[float]:
    if factor <= 0:
        raise BeatAnalysisError(
            "timing.beats.auto_tempo_factor must be greater than zero"
        )
    if len(beat_times) < 2:
        raise BeatAnalysisError(
            "Auto beat analysis found too few beats in the audio"
        )

    if abs(factor - 1.0) < 1e-9:
        return beat_times

    if factor < 1.0:
        stride_float = 1.0 / factor
        stride = int(round(stride_float))
        if stride < 2 or abs(stride_float - stride) > 1e-6:
            raise BeatAnalysisError(
                "timing.beats.auto_tempo_factor < 1 must be an exact "
                "reciprocal of an integer (eg 0.5, 0.333333)"
            )
        reduced = beat_times[::stride]
        if len(reduced) < 2:
            raise BeatAnalysisError(
                "auto_tempo_factor reduced beat map to fewer than two beats"
            )
        return reduced

    subdivisions = int(round(factor))
    if subdivisions < 2 or abs(factor - subdivisions) > 1e-6:
        raise BeatAnalysisError(
            "timing.beats.auto_tempo_factor > 1 must be an integer (eg 2, 3)"
        )

    expanded: list[float] = []
    for i in range(len(beat_times) - 1):
        start = beat_times[i]
        end = beat_times[i + 1]
        step = (end - start) / subdivisions
        if i == 0:
            expanded.append(start)
        for k in range(1, subdivisions + 1):
            expanded.append(start + (step * k))

    return expanded


def _parse_bar_beat_key(
    raw_key: Any, *, beats_per_bar: int
) -> tuple[int | None, str | None]:
    if not isinstance(raw_key, str):
        return None, "timing.beats.bpm map keys must be strings like '12.1'"
    key = raw_key.strip()
    match = re.fullmatch(r"(\d+)\.(\d+)", key)
    if not match:
        return None, f"invalid bpm map key {raw_key!r}; expected 'bar.beat'"

    bar = int(match.group(1))
    beat = int(match.group(2))
    if bar < 1:
        return None, f"invalid bpm map key {raw_key!r}; bar must be >= 1"
    if beat < 1 or beat > beats_per_bar:
        return None, (
            f"invalid bpm map key {raw_key!r}; "
            f"beat must be between 1 and beats_per_bar ({beats_per_bar})"
        )

    beat_index = ((bar - 1) * beats_per_bar) + (beat - 1)
    return beat_index, None


def _parse_bpm_changes(
    bpm_map: dict[Any, Any],
    *,
    beats_per_bar: int,
) -> tuple[list[tuple[int, float]], str | None]:
    changes: list[tuple[int, float]] = []
    seen_indices: set[int] = set()
    for key, raw_bpm in bpm_map.items():
        beat_index, key_error = _parse_bar_beat_key(
            key, beats_per_bar=beats_per_bar
        )
        if key_error is not None or beat_index is None:
            return [], "timing.beats.bpm map contains " + (
                key_error or "an invalid key"
            )

        try:
            bpm_value = float(raw_bpm)
        except (TypeError, ValueError):
            return [], f"timing.beats.bpm[{key!r}] must be numeric"
        if bpm_value <= 0:
            return [], f"timing.beats.bpm[{key!r}] must be greater than zero"

        if beat_index in seen_indices:
            return (
                [],
                f"timing.beats.bpm map has duplicate beat index at key {key!r}",
            )
        seen_indices.add(beat_index)
        changes.append((beat_index, bpm_value))

    changes.sort(key=lambda item: item[0])
    if changes and changes[0][0] > 0:
        # If first change begins later, assume same tempo from start.
        changes.insert(0, (0, changes[0][1]))
    return changes, None


def _build_manual_grid_beat_times(
    *,
    bpm_changes: list[tuple[int, float]],
    duration_seconds: float,
) -> list[float]:
    if not bpm_changes:
        return []

    beat_times: list[float] = []
    beat_index = 0
    current_time = 0.0
    change_ptr = 0

    def _interpolated_bpm(
        at_beat_index: int, start_ptr: int
    ) -> tuple[float, int]:
        ptr = start_ptr
        while (ptr + 1) < len(bpm_changes) and bpm_changes[ptr + 1][
            0
        ] <= at_beat_index:
            ptr += 1

        start_index, start_bpm = bpm_changes[ptr]
        if (ptr + 1) >= len(bpm_changes):
            return start_bpm, ptr

        end_index, end_bpm = bpm_changes[ptr + 1]
        beat_span = end_index - start_index
        if beat_span <= 0:
            return end_bpm, ptr + 1

        progress = (at_beat_index - start_index) / beat_span
        progress = max(0.0, min(1.0, progress))
        interpolated = start_bpm + ((end_bpm - start_bpm) * progress)
        return interpolated, ptr

    max_interval = max(0.001, 60.0 / min(change[1] for change in bpm_changes))
    stop_time = max(0.0, duration_seconds) + (max_interval * 4.0)

    while current_time <= stop_time:
        current_bpm, change_ptr = _interpolated_bpm(beat_index, change_ptr)
        beat_times.append(current_time)
        beat_interval = 60.0 / current_bpm
        current_time += beat_interval
        beat_index += 1

    return beat_times


def _build_uniform_beat_times(
    *,
    beat_seconds: float,
    duration_seconds: float,
) -> list[float]:
    interval = max(0.001, float(beat_seconds))
    stop_time = max(0.0, float(duration_seconds)) + (interval * 4.0)
    beat_times: list[float] = []
    current = 0.0
    while current <= stop_time:
        beat_times.append(current)
        current += interval
    return beat_times


def _estimate_title_frame_count(
    countdown_beats: int, beat_seconds: float, fps: int
) -> int:
    if countdown_beats <= 0:
        return 0
    return max(0, int(round(countdown_beats * beat_seconds * fps)))


def _quantized_countdown_duration_seconds(
    countdown_beats: int, beat_seconds: float, fps: int
) -> float:
    frames = _estimate_title_frame_count(countdown_beats, beat_seconds, fps)
    return frames / max(1, fps)


def _estimate_cue_frame_count(
    event_starts_seconds: list[float], tail_seconds: float, fps: int
) -> int:
    if not event_starts_seconds:
        return 0

    total = 0
    for idx, start in enumerate(event_starts_seconds):
        if idx + 1 < len(event_starts_seconds):
            end = event_starts_seconds[idx + 1]
        else:
            end = start + tail_seconds
        duration = max(1.0 / fps, end - start)
        total += max(1, int(round(duration * fps)))
    return total


def _slice_events_to_time_range(
    *,
    song_events: list[dict[str, Any]],
    event_starts_seconds: list[float],
    tail_seconds: float,
    from_seconds: float,
    to_seconds: float | None,
) -> tuple[list[dict[str, Any]], list[float], float, float, list[int]]:
    if not song_events or not event_starts_seconds:
        return [], [], 0.0, 0.0, []

    full_ends: list[float] = []
    for idx, start in enumerate(event_starts_seconds):
        if idx + 1 < len(event_starts_seconds):
            end = event_starts_seconds[idx + 1]
        else:
            end = start + max(0.0, tail_seconds)
        full_ends.append(end)

    section_start = max(0.0, from_seconds)
    section_end = to_seconds
    if section_end is not None and section_end <= section_start:
        return [], [], 0.0, 0.0, []

    sliced_events: list[dict[str, Any]] = []
    sliced_starts: list[float] = []
    sliced_indices: list[int] = []
    section_last_end_rel = 0.0

    for idx, (event, start, end) in enumerate(
        zip(song_events, event_starts_seconds, full_ends)
    ):
        overlap_start = max(start, section_start)
        overlap_end = end if section_end is None else min(end, section_end)
        if overlap_end <= overlap_start:
            continue

        sliced_events.append(event)
        sliced_starts.append(overlap_start - section_start)
        sliced_indices.append(idx)
        section_last_end_rel = overlap_end - section_start

    if not sliced_events:
        return [], [], 0.0, 0.0, []

    sliced_tail = max(0.0, section_last_end_rel - sliced_starts[-1])
    section_duration = section_last_end_rel
    return (
        sliced_events,
        sliced_starts,
        sliced_tail,
        section_duration,
        sliced_indices,
    )


def _parse_cli_timecode_mmss(
    raw: str | None, *, flag_name: str
) -> float | None:
    if raw is None:
        return None

    text = str(raw).strip()
    if not text:
        return None

    if not re.fullmatch(r"\d{1,3}:\d{2}(?:\.\d+)?", text):
        raise ControllerError(
            f"{flag_name} must use MM:SS format (example: 01:30 or 01:30.5)"
        )

    try:
        minutes_str, seconds_str = text.split(":", 1)
        minutes = int(minutes_str)
        seconds = float(seconds_str)
    except ValueError as exc:
        raise ControllerError(
            f"{flag_name} must use MM:SS format (example: 01:30 or 01:30.5)"
        ) from exc

    if minutes < 0 or seconds < 0 or seconds >= 60:
        raise ControllerError(f"{flag_name} has invalid MM:SS value: {text!r}")

    return _parse_duration_seconds(text)


def _nearest_beat_time_and_interval(
    at_seconds: float,
    *,
    beat_seconds: float,
    beat_times_seconds: list[float] | None,
) -> tuple[float, float]:
    # Quantization must react to the local tempo around the current event, not
    # an average across the whole track. That keeps the snap threshold stable even
    # when the click track speeds up or slows down mid-song.
    fallback_interval = max(0.001, beat_seconds)
    if not beat_times_seconds:
        nearest_beat_index = int(round(at_seconds / fallback_interval))
        nearest_beat_time = nearest_beat_index * fallback_interval
        return nearest_beat_time, fallback_interval

    idx = bisect_left(beat_times_seconds, at_seconds)
    candidate_indices: list[int] = []
    if idx < len(beat_times_seconds):
        candidate_indices.append(idx)
    if idx > 0:
        candidate_indices.append(idx - 1)

    nearest_index = min(
        candidate_indices,
        key=lambda i: abs(beat_times_seconds[i] - at_seconds),
    )
    nearest_beat_time = beat_times_seconds[nearest_index]

    local_intervals: list[float] = []
    if nearest_index > 0:
        local_intervals.append(
            nearest_beat_time - beat_times_seconds[nearest_index - 1]
        )
    if nearest_index + 1 < len(beat_times_seconds):
        local_intervals.append(
            beat_times_seconds[nearest_index + 1] - nearest_beat_time
        )

    positive_local = [interval for interval in local_intervals if interval > 0]
    local_interval = (
        statistics.median(positive_local)
        if positive_local
        else fallback_interval
    )
    return nearest_beat_time, max(0.001, local_interval)


def _quantize_event_starts_to_beats(
    event_starts_seconds: list[float],
    *,
    beat_seconds: float,
    beat_times_seconds: list[float] | None,
    default_quantize_factor: float,
    event_quantize_factors: list[float | None],
) -> tuple[list[float], int]:
    if len(event_starts_seconds) != len(event_quantize_factors):
        raise ValueError(
            "event_quantize_factors length must match event_starts_seconds"
        )

    quantized: list[float] = list(event_starts_seconds)
    quantized_count = 0

    for i, current_time in enumerate(event_starts_seconds):
        factor = event_quantize_factors[i]
        if factor is None:
            factor = default_quantize_factor
        if factor <= 0:
            continue

        nearest_beat_time, local_interval = _nearest_beat_time_and_interval(
            current_time,
            beat_seconds=beat_seconds,
            beat_times_seconds=beat_times_seconds,
        )
        threshold = local_interval * factor
        if abs(current_time - nearest_beat_time) <= threshold:
            if abs(quantized[i] - nearest_beat_time) > 1e-9:
                quantized_count += 1
            quantized[i] = nearest_beat_time

    return quantized, quantized_count


def _first_event_chord_preview(song_events: list[dict[str, Any]]) -> str:
    if not song_events:
        return ""
    first_event = song_events[0]
    raw_chords = first_event.get("chords")
    if not isinstance(raw_chords, str):
        return ""
    tokenized = [
        part.strip()
        for part in re.split(r"\s*=>\s*|\s*,\s*|\s+", raw_chords.strip())
        if part.strip()
    ]
    for token in tokenized:
        if token:
            cleaned = token.lstrip("([{")
            if cleaned != token:
                cleaned = cleaned.rstrip(")]}")
            if cleaned:
                return cleaned
    return ""


def _layout_path_from_song_config(
    song_data: dict[str, Any], *, song_file: Path
) -> Path | None:
    video_cfg = song_data.get("video")
    if video_cfg is None:
        return None
    if not isinstance(video_cfg, dict):
        raise SongConfigError("song.video must be a mapping when provided")

    layout_cfg = video_cfg.get("layout")
    if layout_cfg is None:
        return None
    if not isinstance(layout_cfg, dict):
        raise SongConfigError(
            "song.video.layout must be a mapping when provided"
        )

    raw_layout_file = layout_cfg.get("file")
    if raw_layout_file is None:
        return None
    if not isinstance(raw_layout_file, str) or not raw_layout_file.strip():
        raise SongConfigError(
            "song.video.layout.file must be a non-empty string when provided"
        )

    candidate = Path(raw_layout_file.strip())
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (song_file.parent / candidate).resolve()

    if not resolved.exists() or not resolved.is_file():
        raise SongConfigError(f"song.video.layout.file not found: {resolved}")
    return resolved


def _output_video_path_from_song_config(
    song_data: dict[str, Any], *, song_file: Path
) -> Path | None:
    video_cfg = song_data.get("video")
    if video_cfg is None:
        return None
    if not isinstance(video_cfg, dict):
        raise SongConfigError("song.video must be a mapping when provided")

    output_cfg = video_cfg.get("output")
    if output_cfg is None:
        return None
    if not isinstance(output_cfg, dict):
        raise SongConfigError(
            "song.video.output must be a mapping when provided"
        )

    raw_output_file = output_cfg.get("file")
    if raw_output_file is None:
        return None
    if not isinstance(raw_output_file, str) or not raw_output_file.strip():
        raise SongConfigError(
            "song.video.output.file must be a non-empty string when provided"
        )

    candidate = Path(raw_output_file.strip())
    if candidate.is_absolute():
        return candidate.resolve()
    return (song_file.parent / candidate).resolve()


def _video_output_specs_from_song_config(
    song_data: dict[str, Any],
    *,
    song_file: Path,
) -> list[dict[str, Any]]:
    video_cfg = song_data.get("video")
    if video_cfg is None:
        return []
    if not isinstance(video_cfg, dict):
        raise SongConfigError("song.video must be a mapping when provided")

    output_cfg = video_cfg.get("output")
    if output_cfg is None:
        return []

    def _resolve_output_file(raw_output_file: str) -> Path:
        candidate = Path(raw_output_file.strip())
        if candidate.is_absolute():
            return candidate.resolve()
        return (song_file.parent / candidate).resolve()

    specs: list[dict[str, Any]] = []
    if isinstance(output_cfg, dict):
        raw_output_file = output_cfg.get("file")
        if raw_output_file is None:
            return []
        if not isinstance(raw_output_file, str) or not raw_output_file.strip():
            raise SongConfigError(
                "song.video.output.file must be a non-empty string when provided"
            )
        raw_tracks = output_cfg.get("tracks")
        if raw_tracks is not None and not isinstance(raw_tracks, list):
            raise SongConfigError(
                "song.video.output.tracks must be a list when provided"
            )
        specs.append(
            {
                "file": _resolve_output_file(raw_output_file),
                "tracks": raw_tracks,
            }
        )
        return specs

    if isinstance(output_cfg, list):
        for idx, item in enumerate(output_cfg):
            if not isinstance(item, dict):
                raise SongConfigError(
                    f"song.video.output[{idx}] must be a mapping"
                )
            raw_output_file = item.get("file")
            if (
                not isinstance(raw_output_file, str)
                or not raw_output_file.strip()
            ):
                raise SongConfigError(
                    f"song.video.output[{idx}].file must be a non-empty string"
                )
            raw_tracks = item.get("tracks")
            if raw_tracks is not None and not isinstance(raw_tracks, list):
                raise SongConfigError(
                    f"song.video.output[{idx}].tracks must be a list when provided"
                )
            specs.append(
                {
                    "file": _resolve_output_file(raw_output_file),
                    "tracks": raw_tracks,
                }
            )
        return specs

    raise SongConfigError(
        "song.video.output must be a mapping or a list of mappings"
    )


def _resolve_audio_file(raw_file: str, *, song_file: Path) -> Path:
    candidate = Path(raw_file.strip())
    if candidate.is_absolute():
        return candidate.resolve()
    return (song_file.parent / candidate).resolve()


def _resolve_beat_analysis_audio_path(
    *,
    beats_cfg: dict[str, Any],
    song_file: Path,
    fallback_audio_path: Path,
) -> Path:
    click_track_cfg = beats_cfg.get("click_track")
    if click_track_cfg is None:
        return fallback_audio_path

    if not isinstance(click_track_cfg, dict):
        raise ControllerError(
            "timing.beats.click_track must be a mapping when provided"
        )

    raw_file = click_track_cfg.get("file")
    if not isinstance(raw_file, str) or not raw_file.strip():
        raise ControllerError(
            "timing.beats.click_track.file must be a non-empty string"
        )

    click_track_path = _resolve_audio_file(raw_file, song_file=song_file)
    if not click_track_path.exists() or not click_track_path.is_file():
        raise ControllerError(
            f"Beat analysis click track file not found: {click_track_path}"
        )

    return click_track_path


def _canonicalize_detected_beats_per_bar(
    raw_beats_per_bar: int,
    *,
    fallback_beats_per_bar: int,
) -> int:
    beats_in_bar = max(1, int(raw_beats_per_bar))
    fallback = max(1, int(fallback_beats_per_bar))
    if beats_in_bar < 1 or beats_in_bar > 16:
        return fallback
    return beats_in_bar


def _normalize_stem_audio(
    audio_cfg: dict[str, Any], *, song_file: Path
) -> dict[str, Any] | None:
    stems_raw = audio_cfg.get("stems")
    if stems_raw is None:
        return None
    if not isinstance(stems_raw, list):
        raise ControllerError("audio.stems must be a list")

    groups_manifest: dict[str, dict[str, Any]] = {}
    layers_manifest: list[dict[str, Any]] = []
    track_index: dict[str, dict[str, Any]] = {}

    for group_idx, stem_group in enumerate(stems_raw):
        if not isinstance(stem_group, dict):
            raise ControllerError(
                f"audio.stems[{group_idx}] must be a mapping"
            )

        group_name = stem_group.get("group")
        if not isinstance(group_name, str) or not group_name.strip():
            raise ControllerError(
                f"audio.stems[{group_idx}].group must be a non-empty string"
            )

        group_label = stem_group.get("label")
        group_modal = bool(stem_group.get("modal", False))
        group_volume_raw = stem_group.get("volume", 1.0)
        try:
            group_volume = float(group_volume_raw)
        except (TypeError, ValueError) as exc:
            raise ControllerError(
                f"audio.stems[{group_idx}].volume must be numeric"
            ) from exc

        group_payload: dict[str, Any] = {}
        if isinstance(group_label, str) and group_label.strip():
            group_payload["label"] = group_label
        if group_modal:
            group_payload["modal"] = True
        if abs(group_volume - 1.0) > 1e-9:
            group_payload["volume"] = group_volume
        groups_manifest[group_name] = group_payload

        tracks_raw = stem_group.get("tracks")
        if not isinstance(tracks_raw, list) or not tracks_raw:
            raise ControllerError(
                f"audio.stems[{group_idx}].tracks must be a non-empty list"
            )

        for track_idx, track in enumerate(tracks_raw):
            if not isinstance(track, dict):
                raise ControllerError(
                    f"audio.stems[{group_idx}].tracks[{track_idx}] must be a mapping"
                )

            track_name = track.get("name")
            if not isinstance(track_name, str) or not track_name.strip():
                raise ControllerError(
                    f"audio.stems[{group_idx}].tracks[{track_idx}].name "
                    "must be a non-empty string"
                )

            key = f"{group_name}.{track_name}"
            if key in track_index:
                raise ControllerError(f"Duplicate track selector key: {key}")

            raw_file = track.get("file")
            if not isinstance(raw_file, str) or not raw_file.strip():
                raise ControllerError(
                    f"audio.stems[{group_idx}].tracks[{track_idx}].file "
                    "must be a non-empty string"
                )

            src_path = _resolve_audio_file(raw_file, song_file=song_file)
            if not src_path.exists() or not src_path.is_file():
                raise ControllerError(f"Audio stem file not found: {src_path}")

            label = track.get("label")
            layer_file = f"{track_name}{src_path.suffix.lower() or '.bin'}"

            layers_manifest.append(
                {
                    "name": track_name,
                    "label": (
                        label
                        if isinstance(label, str) and label.strip()
                        else track_name
                    ),
                    "file": layer_file,
                    "group": group_name,
                }
            )

            track_volume_raw = track.get("volume", track.get("gain", 1.0))
            try:
                track_volume = float(track_volume_raw)
            except (TypeError, ValueError) as exc:
                raise ControllerError(
                    f"audio.stems[{group_idx}].tracks[{track_idx}].volume "
                    "must be numeric"
                ) from exc

            track_index[key] = {
                "group": group_name,
                "name": track_name,
                "file_path": src_path,
                "zip_file": layer_file,
                "gain": track_volume * group_volume,
            }

    return {
        "groups_manifest": groups_manifest,
        "layers_manifest": layers_manifest,
        "track_index": track_index,
    }


def _resolve_selected_tracks(
    *,
    track_index: dict[str, dict[str, Any]],
    selectors: list[Any] | None,
) -> list[dict[str, Any]]:
    if not selectors:
        return list(track_index.values())

    selected_keys: list[str] = []
    seen: set[str] = set()

    for idx, raw_selector in enumerate(selectors):
        if not isinstance(raw_selector, str) or not raw_selector.strip():
            raise ControllerError(
                f"video.output.tracks[{idx}] must be a non-empty selector string"
            )
        selector = raw_selector.strip()

        if selector.endswith(".*"):
            prefix = selector[:-2]
            matches = [
                key for key in track_index if key.startswith(prefix + ".")
            ]
            if not matches:
                raise ControllerError(
                    f"Track selector matched no tracks: {selector}"
                )
            for key in sorted(matches):
                if key not in seen:
                    seen.add(key)
                    selected_keys.append(key)
            continue

        if selector in track_index:
            if selector not in seen:
                seen.add(selector)
                selected_keys.append(selector)
            continue

        suffix_matches = [
            key for key in track_index if key.split(".", 1)[1] == selector
        ]
        if len(suffix_matches) == 1:
            key = suffix_matches[0]
            if key not in seen:
                seen.add(key)
                selected_keys.append(key)
            continue
        if len(suffix_matches) > 1:
            raise ControllerError(
                f"Ambiguous track selector {selector!r}; use group.track name"
            )

        raise ControllerError(f"Unknown track selector: {selector}")

    return [track_index[key] for key in selected_keys]


def _prevalidate_output_targets(
    *,
    output_targets: list[dict[str, Any]],
    stem_audio: dict[str, Any] | None,
) -> None:
    """Resolve and validate per-output track selectors before expensive rendering."""
    for target in output_targets:
        target_tracks_raw = target.get("tracks")

        if stem_audio is None:
            if target_tracks_raw:
                raise ControllerError(
                    "video.output.tracks requires audio.stems; "
                    "remove tracks or add stems"
                )
            target["selected_tracks"] = None
            continue

        selectors = (
            target_tracks_raw if isinstance(target_tracks_raw, list) else None
        )
        selected_tracks = _resolve_selected_tracks(
            track_index=stem_audio["track_index"],
            selectors=selectors,
        )
        if not selected_tracks:
            raise ControllerError(
                f"No stem tracks selected for output: {target['file']}"
            )
        target["selected_tracks"] = selected_tracks


def _build_marker_entries(
    song_events: list[dict[str, Any]],
    event_starts_seconds: list[float],
) -> list[dict[str, str]]:
    markers: list[dict[str, str]] = []
    for idx, event in enumerate(song_events):
        label = event.get("marker")
        if isinstance(label, str) and label.strip():
            markers.append(
                {
                    "time": _seconds_to_marker_time(event_starts_seconds[idx]),
                    "label": label.strip(),
                }
            )

    if markers:
        return markers

    for idx, event in enumerate(song_events):
        instruction = event.get("instruction")
        if isinstance(instruction, str) and instruction.strip():
            markers.append(
                {
                    "time": _seconds_to_marker_time(event_starts_seconds[idx]),
                    "label": instruction.strip(),
                }
            )
    return markers


def _seconds_to_marker_time(value: float) -> str:
    total = max(0.0, float(value))
    minutes = int(total // 60)
    seconds = total - (minutes * 60)
    return f"{minutes:02d}:{seconds:05.2f}"


def _write_maestro_zip(
    *,
    maestro_cfg: dict[str, Any],
    title_text: str,
    groups_manifest: dict[str, dict[str, Any]],
    layers_manifest: list[dict[str, Any]],
    markers: list[dict[str, str]],
    loop_timeline: list[dict[str, Any]],
    track_index: dict[str, dict[str, Any]],
    song_file: Path,
) -> Path:
    output_cfg = maestro_cfg.get("output")
    if not isinstance(output_cfg, dict):
        raise ControllerError("maestro.output must be a mapping")

    raw_file = output_cfg.get("file")
    if not isinstance(raw_file, str) or not raw_file.strip():
        raise ControllerError("maestro.output.file must be a non-empty string")

    start_offset_raw = output_cfg.get("start_offset")
    start_offset: float | None = None
    if start_offset_raw is not None:
        try:
            start_offset = float(start_offset_raw)
        except (TypeError, ValueError) as exc:
            raise ControllerError(
                "maestro.output.start_offset must be numeric seconds"
            ) from exc
        if start_offset < 0:
            raise ControllerError("maestro.output.start_offset must be >= 0")

    output_path = _resolve_audio_file(raw_file, song_file=song_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    manifest = {
        "title": title_text or "Untitled",
        "groups": groups_manifest,
        "layers": layers_manifest,
        "markers": markers,
    }
    if loop_timeline:
        manifest["loop_timeline"] = {
            "version": 1,
            "bars": loop_timeline,
        }
    if start_offset is not None:
        manifest["start_offset"] = start_offset

    with zipfile.ZipFile(
        output_path, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        archive.writestr(
            "manifest.json", json.dumps(manifest, indent=4) + "\n"
        )
        for track in track_index.values():
            archive.write(track["file_path"], arcname=track["zip_file"])

    return output_path


def _validate_uniform_track_durations(
    *,
    tracks: list[dict[str, Any]],
    ffmpeg_path: str,
    tolerance_seconds: float = 0.02,
) -> float:
    if not tracks:
        raise ControllerError("No audio tracks available for render")

    durations: list[tuple[str, float]] = []
    for track in tracks:
        track_path = track.get("file_path")
        key = f"{track.get('group')}.{track.get('name')}"
        if not isinstance(track_path, Path):
            raise ControllerError(f"Track path missing for {key}")

        duration = _probe_audio_duration_seconds(track_path, ffmpeg_path)
        if duration is None or duration <= 0:
            raise ControllerError(
                f"Unable to probe track duration for {key} ({track_path})"
            )
        durations.append((key, duration))

    reference_key, reference_duration = durations[0]
    mismatches = [
        (key, duration)
        for key, duration in durations[1:]
        if abs(duration - reference_duration) > tolerance_seconds
    ]
    if mismatches:
        details = [f"{reference_key}={reference_duration:.3f}s"]
        details.extend(
            [f"{key}={duration:.3f}s" for key, duration in mismatches]
        )
        raise ControllerError(
            "Audio stem lengths do not match. "
            "All audio.stems.tracks files must be exported at identical length. "
            f"{'; '.join(details)}"
        )

    return reference_duration


class _DirectFrameEncodeSession:
    def __init__(self, *, ffmpeg_cmd: list[str], frame_queue_size: int) -> None:
        self._ffmpeg_stderr = tempfile.TemporaryFile()
        self._proc = subprocess.Popen(
            ffmpeg_cmd,
            stdin=subprocess.PIPE,
            stderr=self._ffmpeg_stderr,
        )
        self._frame_queue: queue.Queue[bytes | None] | None = None
        self._writer_error: list[BaseException] = []
        self._writer_thread: threading.Thread | None = None
        self._writer_failure: BaseException | None = None
        self._finished_writing = False

        if frame_queue_size > 0:
            self._frame_queue = queue.Queue(maxsize=frame_queue_size)
            self._writer_thread = threading.Thread(
                target=self._frame_writer_worker,
                name="frame-writer",
            )
            self._writer_thread.daemon = True
            self._writer_thread.start()

    def _frame_writer_worker(self) -> None:
        if self._proc is None or self._proc.stdin is None:
            self._writer_error.append(
                ControllerError(
                    "ffmpeg stdin is not available for frame streaming"
                )
            )
            return
        try:
            while True:
                payload = self._frame_queue.get() if self._frame_queue else None
                if payload is None:
                    break
                self._proc.stdin.write(payload)
        except BaseException as exc:  # pragma: no cover - defensive path
            self._writer_error.append(exc)

    def _queue_payload(self, payload: bytes | None) -> None:
        if self._frame_queue is None:
            return
        while True:
            if self._writer_error:
                raise ControllerError(
                    f"ffmpeg frame writer failed: {self._writer_error[0]}"
                )
            try:
                self._frame_queue.put(payload, timeout=0.1)
                return
            except queue.Full:
                continue

    def frame_sink(self, image: Image.Image, _frame_index: int) -> None:
        if self._writer_error:
            raise ControllerError(
                f"ffmpeg frame writer failed: {self._writer_error[0]}"
            )
        payload = image.tobytes()
        if self._frame_queue is None:
            if self._proc is None or self._proc.stdin is None:
                raise ControllerError(
                    "ffmpeg stdin is not available for frame streaming"
                )
            self._proc.stdin.write(payload)
            return
        self._queue_payload(payload)

    def finish_writing(self) -> None:
        if self._finished_writing:
            return
        if self._frame_queue is not None:
            self._queue_payload(None)
        if self._writer_thread is not None:
            self._writer_thread.join()
        if self._writer_error:
            self._writer_failure = self._writer_error[0]
        if self._proc is not None and self._proc.stdin is not None:
            self._proc.stdin.close()
        self._finished_writing = True

    def wait_and_validate(self, *, emit: Callable[[str], None]) -> None:
        ffmpeg_encode_wait_started_at = perf_counter()
        self._proc.wait()
        ffmpeg_encode_wait_elapsed = perf_counter() - ffmpeg_encode_wait_started_at
        emit(f"timing_ffmpeg_encode_wait_seconds={ffmpeg_encode_wait_elapsed:.6f}")

        self._ffmpeg_stderr.seek(0)
        stderr_data = self._ffmpeg_stderr.read()
        self._ffmpeg_stderr.close()
        if self._proc.returncode != 0:
            ffmpeg_error = stderr_data.decode("utf-8", errors="replace").strip()
            raise ControllerError(
                "ffmpeg encode failed:\n" + (ffmpeg_error or "unknown error")
            )
        if self._writer_failure is not None:
            raise ControllerError(f"ffmpeg frame writer failed: {self._writer_failure}")


def _run_mux_for_target(
    *,
    target_idx: int,
    target: dict[str, Any],
    mux_video_source_path: Path,
    ffmpeg_path: str,
    ffmpeg_audio_seek_seconds: float,
    audio_path: Path,
    stem_audio: dict[str, Any] | None,
    audio_delay_ms: int,
    target_output_duration: float | None,
) -> tuple[int, Path, bool, str, float]:
    target_path: Path = target["file"]
    mux_started_at = perf_counter()

    mux_cmd = [
        str(ffmpeg_path),
        "-y",
        "-i",
        str(mux_video_source_path),
    ]

    if stem_audio is None:
        if ffmpeg_audio_seek_seconds > 0:
            mux_cmd.extend(
                [
                    "-ss",
                    _format_ffmpeg_seconds(ffmpeg_audio_seek_seconds),
                ]
            )
        mux_cmd.extend(["-i", str(audio_path)])
        filter_graph = f"[1:a]adelay={audio_delay_ms}:all=1[m]"
    else:
        selected_tracks = target.get("selected_tracks")
        if not isinstance(selected_tracks, list) or not selected_tracks:
            raise ControllerError(
                f"No stem tracks selected for output: {target_path}"
            )

        for track in selected_tracks:
            if ffmpeg_audio_seek_seconds > 0:
                mux_cmd.extend(
                    [
                        "-ss",
                        _format_ffmpeg_seconds(ffmpeg_audio_seek_seconds),
                    ]
                )
            mux_cmd.extend(["-i", str(track["file_path"])])

        gain_labels: list[str] = []
        filter_parts: list[str] = []
        for input_idx, track in enumerate(selected_tracks, start=1):
            gain = float(track["gain"])
            label = f"g{input_idx}"
            filter_parts.append(f"[{input_idx}:a]volume={gain:.6f}[{label}]")
            gain_labels.append(f"[{label}]")

        if len(gain_labels) == 1:
            filter_parts.append(f"{gain_labels[0]}adelay={audio_delay_ms}:all=1[m]")
        else:
            mixed_labels = "".join(gain_labels)
            filter_parts.append(
                f"{mixed_labels}amix=inputs={len(gain_labels)}:normalize=0[m]"
            )
            filter_parts.append(f"[m]adelay={audio_delay_ms}:all=1[m]")

        filter_graph = ";".join(filter_parts)

    input_sources: list[str]
    probe_filter_graph: str
    if stem_audio is None:
        input_sources = [str(audio_path)]
        probe_filter_graph = _build_probe_filter_graph_for_normalization(
            audio_delay_ms=audio_delay_ms,
            selected_tracks=None,
        )
    else:
        selected_tracks = target.get("selected_tracks", [])
        input_sources = [str(track["file_path"]) for track in selected_tracks]
        probe_filter_graph = _build_probe_filter_graph_for_normalization(
            audio_delay_ms=audio_delay_ms,
            selected_tracks=selected_tracks,
        )

    gain_db = _peak_normalization_gain_db(
        ffmpeg_path=str(ffmpeg_path),
        input_sources=input_sources,
        filter_graph=probe_filter_graph,
    )

    if gain_db is not None and abs(gain_db) > 1e-6:
        if target_output_duration is not None:
            trim_end = max(0.001, target_output_duration)
            filter_graph = (
                f"{filter_graph};"
                f"[m]volume={gain_db:.6f}dB[n];"
                f"[n]atrim=end={trim_end:.6f},asetpts=N/SR/TB[a]"
            )
        else:
            filter_graph = (
                f"{filter_graph};"
                f"[m]volume={gain_db:.6f}dB[n];"
                f"[n]asetpts=N/SR/TB[a]"
            )
    elif target_output_duration is not None:
        trim_end = max(0.001, target_output_duration)
        filter_graph = (
            f"{filter_graph};[m]atrim=end={trim_end:.6f},asetpts=N/SR/TB[a]"
        )
    else:
        filter_graph = f"{filter_graph};[m]asetpts=N/SR/TB[a]"

    mux_cmd.extend(
        [
            "-filter_complex",
            filter_graph,
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
        ]
    )
    if target_output_duration is not None:
        mux_cmd.extend(["-t", f"{target_output_duration:.6f}"])
    mux_cmd.append("-shortest")
    mux_cmd.append(str(target_path))

    mux_stderr = tempfile.TemporaryFile()
    mux_proc = subprocess.run(mux_cmd, stderr=mux_stderr)
    mux_stderr.seek(0)
    mux_error_raw = mux_stderr.read()
    mux_stderr.close()

    mux_elapsed = perf_counter() - mux_started_at
    if mux_proc.returncode != 0:
        mux_error = mux_error_raw.decode("utf-8", errors="replace").strip()
        return (
            target_idx,
            target_path,
            False,
            mux_error or "unknown error",
            mux_elapsed,
        )

    return (target_idx, target_path, True, "", mux_elapsed)


def render(
    song_file: Path,
    layout_file: Path | None = None,
    beat_seconds: float | None = None,
    countdown_beats: int | None = None,
    output_video: Path | None = None,
    from_seconds: str | None = None,
    to_seconds: str | None = None,
    maestro_only: bool = False,
    video_encoder: str = DEFAULT_VIDEO_ENCODER,
    video_preset: str = DEFAULT_VIDEO_PRESET,
    video_crf: int | None = None,
    video_bitrate: str | None = None,
    background_hwaccel: str | None = None,
    frame_queue_size: int = DEFAULT_FRAME_QUEUE_SIZE,
    stem_duration_tolerance: float = DEFAULT_STEM_DURATION_TOLERANCE_SECONDS,
    ignore_stem_length_mismatch: bool = DEFAULT_IGNORE_STEM_LENGTH_MISMATCH,
    apple_silicon_optimize: bool = DEFAULT_APPLE_SILICON_OPTIMIZE,
    *,
    emit: Callable[[str], None] | None = None,
    progressbar: Callable[..., Any] | None = None,
) -> None:
    """Render title + cue frames and encode final video."""
    emit = emit or (lambda _line: None)
    progressbar = progressbar or _noop_progressbar
    render_started_at = perf_counter()

    is_apple_silicon = (platform.system() == "Darwin") and (
        platform.machine().lower() in {"arm64", "aarch64"}
    )
    if apple_silicon_optimize:
        if not is_apple_silicon:
            emit("apple_silicon_optimize=ignored (not running on macOS ARM)")
        else:
            if background_hwaccel is None:
                background_hwaccel = "videotoolbox"
                emit("apple_silicon_optimize: background_hwaccel=videotoolbox")
            if video_encoder.lower() == "libx264":
                emit(
                    "apple_silicon_optimize: keeping libx264 for better compression"
                )
    try:
        song_data = load_yaml_file(song_file)
        song_layout_file = _layout_path_from_song_config(
            song_data, song_file=song_file
        )
        song_output_specs = _video_output_specs_from_song_config(
            song_data, song_file=song_file
        )
        resolved_layout_path = resolve_layout_path(
            layout_file if layout_file is not None else song_layout_file
        )
        layout_data = load_layout_config(resolved_layout_path)
    except SongConfigError as exc:
        raise ControllerError(str(exc)) from exc

    meta = (
        song_data.get("meta")
        if isinstance(song_data.get("meta"), dict)
        else {}
    )
    title_text = (
        meta.get("title") if isinstance(meta.get("title"), str) else ""
    )
    artist_text = (
        meta.get("artist") if isinstance(meta.get("artist"), str) else ""
    )
    key_text = meta.get("key") if isinstance(meta.get("key"), str) else ""

    hot_start_raw = song_data.get("hot_start", False)
    if not isinstance(hot_start_raw, bool):
        raise ControllerError("song.hot_start must be a boolean when provided")
    hot_start = hot_start_raw

    timing = song_data.get("timing")
    if not isinstance(timing, dict):
        raise ControllerError("Song file is missing timing mapping")

    title_cfg = layout_data.get("title", {})
    title_enabled = True
    if isinstance(title_cfg, dict):
        enabled_raw = title_cfg.get("enabled", True)
        if not isinstance(enabled_raw, bool):
            raise ControllerError(
                "layout.title.enabled must be a boolean when provided"
            )
        title_enabled = enabled_raw

    layout_countdown_beats = None
    if isinstance(title_cfg, dict):
        cfg_value = title_cfg.get("countdown_beats")
        if cfg_value is not None:
            if not isinstance(cfg_value, int) or cfg_value < 0:
                raise ControllerError(
                    "layout.title.countdown_beats must be an integer >= 0"
                )
            layout_countdown_beats = cfg_value

    song_countdown_beats = None
    song_countdown_raw = timing.get("countdown_beats")
    if song_countdown_raw is None:
        song_countdown_raw = song_data.get("countdown_beats")
    if song_countdown_raw is not None:
        if not isinstance(song_countdown_raw, int) or song_countdown_raw < 0:
            raise ControllerError(
                "timing.countdown_beats (or song.countdown_beats) "
                "must be an integer >= 0"
            )
        song_countdown_beats = song_countdown_raw

    effective_countdown_beats = (
        countdown_beats
        if countdown_beats is not None
        else (
            song_countdown_beats
            if song_countdown_beats is not None
            else (
                layout_countdown_beats
                if layout_countdown_beats is not None
                else 8
            )
        )
    )

    lyrics_cfg = timing.get("lyrics")
    if not isinstance(lyrics_cfg, dict):
        raise ControllerError("timing.lyrics mapping is required")
    if lyrics_cfg.get("source") != "tap":
        raise ControllerError(
            "Cue-frame rendering currently supports timing.lyrics.source=tap only."
        )

    max_line_chars_raw = lyrics_cfg.get("max_line_chars", 32)
    try:
        max_line_chars = int(max_line_chars_raw)
    except (TypeError, ValueError) as exc:
        raise ControllerError(
            "timing.lyrics.max_line_chars must be an integer >= 1"
        ) from exc
    if max_line_chars < 1:
        raise ControllerError(
            "timing.lyrics.max_line_chars must be an integer >= 1"
        )

    lyric_lead_in_beats_raw = lyrics_cfg.get("lead_in_beats", 4)
    try:
        lyric_lead_in_beats = float(lyric_lead_in_beats_raw)
    except (TypeError, ValueError) as exc:
        raise ControllerError(
            "timing.lyrics.lead_in_beats must be numeric and >= 0"
        ) from exc
    if lyric_lead_in_beats < 0:
        raise ControllerError(
            "timing.lyrics.lead_in_beats must be numeric and >= 0"
        )

    beats_cfg = timing.get("beats")
    if not isinstance(beats_cfg, dict):
        raise ControllerError("timing.beats mapping is required")
    beats_source = beats_cfg.get("source")
    if beats_source not in {"tap", "auto", "manual_grid"}:
        raise ControllerError(
            "timing.beats.source must be one of: tap, auto, manual_grid"
        )
    beats_per_bar = _beats_per_bar_from_timing(beats_cfg)
    click_track_cfg = beats_cfg.get("click_track")
    click_track_enabled = isinstance(click_track_cfg, dict)

    timing_source = "tap"
    song_events = song_data.get("events")
    if not isinstance(song_events, list):
        raise ControllerError("Song file must contain an events list")
    first_event_chord_preview = _first_event_chord_preview(song_events)

    default_beats_per_phrase = _default_beats_per_phrase_from_timing(
        beats_cfg,
        beats_per_bar=beats_per_bar,
        click_track_enabled=click_track_enabled,
    )

    beat_anchor_raw = beats_cfg.get("beat_offset", 0)
    try:
        beat_anchor_index = int(beat_anchor_raw)
    except (TypeError, ValueError) as exc:
        raise ControllerError("timing.beat_offset must be an integer") from exc

    beat_phase_offset_raw = beats_cfg.get("beat_phase_offset", 0)
    try:
        beat_phase_offset = int(beat_phase_offset_raw)
    except (TypeError, ValueError) as exc:
        raise ControllerError(
            "timing.beat_phase_offset must be an integer"
        ) from exc
    cue_beat_anchor_index = beat_anchor_index
    if beat_phase_offset != 0:
        emit(
            "timing.beats.beat_phase_offset is deprecated and ignored; "
            "beats now follow a single click-track anchor."
        )

    hold_reset_until_next_beat_raw = beats_cfg.get(
        "hold_reset_until_next_beat", False
    )
    if not isinstance(hold_reset_until_next_beat_raw, bool):
        raise ControllerError(
            "timing.beats.hold_reset_until_next_beat must be a boolean"
        )
    hold_reset_until_next_beat = hold_reset_until_next_beat_raw

    intro_lookahead_lead_beats_raw = lyrics_cfg.get(
        "intro_lookahead_lead_beats"
    )
    intro_lookahead_lead_bars_raw = lyrics_cfg.get("intro_lookahead_lead_bars")
    if (
        intro_lookahead_lead_beats_raw is not None
        and intro_lookahead_lead_bars_raw is not None
    ):
        raise ControllerError(
            "timing.lyrics.intro_lookahead_lead_beats and "
            "intro_lookahead_lead_bars are mutually exclusive"
        )

    intro_lookahead_lead_beats: float | None = None
    if intro_lookahead_lead_beats_raw is not None:
        try:
            intro_lookahead_lead_beats = float(intro_lookahead_lead_beats_raw)
        except (TypeError, ValueError) as exc:
            raise ControllerError(
                "timing.lyrics.intro_lookahead_lead_beats must be numeric"
            ) from exc
        if intro_lookahead_lead_beats <= 0:
            raise ControllerError(
                "timing.lyrics.intro_lookahead_lead_beats must be > 0"
            )
    elif intro_lookahead_lead_bars_raw is not None:
        try:
            intro_lookahead_lead_bars = float(intro_lookahead_lead_bars_raw)
        except (TypeError, ValueError) as exc:
            raise ControllerError(
                "timing.lyrics.intro_lookahead_lead_bars must be numeric"
            ) from exc
        if intro_lookahead_lead_bars <= 0:
            raise ControllerError(
                "timing.lyrics.intro_lookahead_lead_bars must be > 0"
            )
        intro_lookahead_lead_beats = intro_lookahead_lead_bars * beats_per_bar

    keep_postlude_beat_clock_raw = beats_cfg.get("keep_postlude_beat_clock")
    if keep_postlude_beat_clock_raw is not None and not isinstance(
        keep_postlude_beat_clock_raw, bool
    ):
        raise ControllerError(
            "timing.beats.keep_postlude_beat_clock must be a boolean"
        )
    keep_postlude_beat_clock = keep_postlude_beat_clock_raw

    # Validate event-level phrase overrides early so failures are explicit.
    event_quantize_factors: list[float | None] = []
    for event_idx, event in enumerate(song_events):
        raw_lyric = event.get("lyric")
        if isinstance(raw_lyric, str) and raw_lyric:
            for line_idx, (lyric_line, line_length) in enumerate(
                _lyric_line_lengths_for_validation(raw_lyric),
                start=1,
            ):
                if line_length > max_line_chars:
                    display_line = lyric_line.rstrip()
                    raise ControllerError(
                        f"events[{event_idx}].lyric line {line_idx} has "
                        f"{line_length} chars "
                        f"(max {max_line_chars}): {display_line!r}"
                    )

        raw_phrase = event.get("beats_per_phrase")
        if raw_phrase is None or raw_phrase == "":
            pass
        else:
            try:
                phrase_value = int(raw_phrase)
            except (TypeError, ValueError) as exc:
                raise ControllerError(
                    f"events[{event_idx}].beats_per_phrase must be an integer, "
                    "empty string, or omitted"
                ) from exc
            if phrase_value < 1:
                raise ControllerError(
                    f"events[{event_idx}].beats_per_phrase must be >= 1"
                )

        raw_phrase_offset = event.get("beats_per_phrase_offset_seconds")
        if raw_phrase_offset is not None:
            try:
                float(raw_phrase_offset)
            except (TypeError, ValueError) as exc:
                raise ControllerError(
                    f"events[{event_idx}].beats_per_phrase_offset_seconds "
                    "must be numeric"
                ) from exc

        has_extra_beats = "extra_beats" in event
        has_legacy_phrase_extra = "beats_per_phrase_extra" in event
        raw_phrase_extra = event.get("extra_beats")
        if raw_phrase_extra is None and has_legacy_phrase_extra:
            raw_phrase_extra = event.get("beats_per_phrase_extra")
            emit(
                f"Warning: events[{event_idx}].beats_per_phrase_extra is deprecated; "
                "use events[].extra_beats instead."
            )

        if has_extra_beats and has_legacy_phrase_extra:
            raise ControllerError(
                f"events[{event_idx}] must not define both extra_beats and "
                "beats_per_phrase_extra"
            )

        if raw_phrase_extra is None or raw_phrase_extra == "":
            pass
        else:
            if isinstance(raw_phrase_extra, bool):
                raise ControllerError(
                    f"events[{event_idx}].extra_beats must be an integer >= 0, "
                    "empty string, or omitted"
                )
            try:
                phrase_extra_value = int(raw_phrase_extra)
            except (TypeError, ValueError) as exc:
                raise ControllerError(
                    f"events[{event_idx}].extra_beats must be an integer >= 0, "
                    "empty string, or omitted"
                ) from exc
            if phrase_extra_value < 0:
                raise ControllerError(
                    f"events[{event_idx}].extra_beats must be >= 0"
                )

        raw_reset_beat = event.get("reset_beat")
        if raw_reset_beat is not None and not isinstance(raw_reset_beat, bool):
            raise ControllerError(
                f"events[{event_idx}].reset_beat must be a boolean"
            )

        raw_event_quantize = event.get("quantize_factor")
        if raw_event_quantize is None or raw_event_quantize == "":
            event_quantize_factors.append(None)
        else:
            try:
                event_quantize = float(raw_event_quantize)
            except (TypeError, ValueError) as exc:
                raise ControllerError(
                    f"events[{event_idx}].quantize_factor must be numeric, "
                    "empty string, or omitted"
                ) from exc
            if event_quantize < 0 or event_quantize > 1:
                raise ControllerError(
                    f"events[{event_idx}].quantize_factor must be between 0 and 1"
                )
            event_quantize_factors.append(event_quantize)

    if len(event_quantize_factors) != len(song_events):
        raise ControllerError(
            "Internal error: event quantize factors are misaligned"
        )

    quantize_factor_raw = beats_cfg.get("quantize_factor", 0.0)
    try:
        default_quantize_factor = float(quantize_factor_raw)
    except (TypeError, ValueError) as exc:
        raise ControllerError(
            "timing.beats.quantize_factor must be numeric"
        ) from exc
    if default_quantize_factor < 0 or default_quantize_factor > 1:
        raise ControllerError(
            "timing.beats.quantize_factor must be between 0 and 1"
        )

    audio = (
        song_data.get("audio")
        if isinstance(song_data.get("audio"), dict)
        else {}
    )
    stem_audio = _normalize_stem_audio(audio, song_file=song_file)

    audio_file = audio.get("file")
    audio_path: Path | None = None
    if isinstance(audio_file, str) and audio_file.strip():
        audio_path = _resolve_audio_file(audio_file, song_file=song_file)
        if not audio_path.exists():
            raise ControllerError(f"Audio file not found: {audio_path}")
    elif stem_audio is not None:
        track_index = stem_audio.get("track_index", {})
        if not isinstance(track_index, dict) or not track_index:
            raise ControllerError(
                "audio.stems must include at least one track"
            )
        first_track = next(iter(track_index.values()))
        first_track_path = first_track.get("file_path")
        if not isinstance(first_track_path, Path):
            raise ControllerError("audio.stems track path is invalid")
        audio_path = first_track_path
    else:
        raise ControllerError(
            "audio.file is required unless audio.stems is provided"
        )

    beat_analysis_audio_path = _resolve_beat_analysis_audio_path(
        beats_cfg=beats_cfg,
        song_file=song_file,
        fallback_audio_path=audio_path,
    )

    audio_start_offset_raw = audio.get("start_offset_seconds", 0.0)
    try:
        audio_start_offset_seconds = _parse_duration_seconds(
            audio_start_offset_raw
        )
    except (TypeError, ValueError) as exc:
        raise ControllerError(
            "audio.start_offset_seconds must be a number or duration string"
        ) from exc
    if audio_start_offset_seconds < 0:
        raise ControllerError("audio.start_offset_seconds must be >= 0")

    section_from_parsed = _parse_cli_timecode_mmss(
        from_seconds, flag_name="--from-seconds"
    )
    section_to_parsed = _parse_cli_timecode_mmss(
        to_seconds, flag_name="--to-seconds"
    )
    section_from_seconds = (
        0.0 if section_from_parsed is None else section_from_parsed
    )
    section_to_seconds = section_to_parsed
    if (
        section_to_seconds is not None
        and section_to_seconds <= section_from_seconds
    ):
        raise ControllerError(
            "--to-seconds must be greater than --from-seconds"
        )

    inferred_beat_seconds, beat_reason = _infer_beat_seconds_from_beats_config(
        beats_cfg
    )
    auto_dynamic_tempo_raw = beats_cfg.get("auto_dynamic_tempo", False)
    if not isinstance(auto_dynamic_tempo_raw, bool):
        raise ControllerError(
            "timing.beats.auto_dynamic_tempo must be a boolean"
        )
    auto_dynamic_tempo = auto_dynamic_tempo_raw

    auto_dynamic_tempo_smoothing_raw = beats_cfg.get(
        "auto_dynamic_tempo_smoothing", 1
    )
    try:
        auto_dynamic_tempo_smoothing = int(auto_dynamic_tempo_smoothing_raw)
    except (TypeError, ValueError) as exc:
        raise ControllerError(
            "timing.beats.auto_dynamic_tempo_smoothing must be an integer >= 1"
        ) from exc
    if auto_dynamic_tempo_smoothing < 1:
        raise ControllerError(
            "timing.beats.auto_dynamic_tempo_smoothing must be an integer >= 1"
        )

    auto_dynamic_tempo_sensitivity_raw = beats_cfg.get(
        "auto_dynamic_tempo_sensitivity", 1.0
    )
    try:
        auto_dynamic_tempo_sensitivity = float(
            auto_dynamic_tempo_sensitivity_raw
        )
    except (TypeError, ValueError) as exc:
        raise ControllerError(
            "timing.beats.auto_dynamic_tempo_sensitivity must be numeric "
            "between 0 and 1"
        ) from exc
    if (
        auto_dynamic_tempo_sensitivity < 0
        or auto_dynamic_tempo_sensitivity > 1
    ):
        raise ControllerError(
            "timing.beats.auto_dynamic_tempo_sensitivity must be numeric "
            "between 0 and 1"
        )

    auto_start_bpm: float | None = None
    if beats_source == "auto":
        auto_start_bpm_raw = beats_cfg.get("bpm")
        if auto_start_bpm_raw is not None:
            try:
                auto_start_bpm = float(auto_start_bpm_raw)
            except (TypeError, ValueError) as exc:
                raise ControllerError(
                    "timing.beats.bpm must be numeric when used as auto "
                    "beat-analysis hint"
                ) from exc
            if auto_start_bpm <= 0:
                raise ControllerError(
                    "timing.beats.bpm must be greater than zero when provided"
                )

    auto_tempo_factor_raw = beats_cfg.get("auto_tempo_factor", 1.0)
    try:
        auto_tempo_factor = float(auto_tempo_factor_raw)
    except (TypeError, ValueError) as exc:
        raise ControllerError(
            "timing.beats.auto_tempo_factor must be numeric"
        ) from exc

    beat_times_seconds: list[float] | None = None
    click_bar_start_times_seconds: list[float] = []
    beat_seconds_mode = beats_cfg.get("beat_seconds", "")
    beat_seconds_is_auto = (
        isinstance(beat_seconds_mode, str)
        and beat_seconds_mode.strip().lower() == "auto"
    )
    auto_beats_requested = beats_source == "auto"
    auto_beats_requested = auto_beats_requested and (
        inferred_beat_seconds is None
        or beat_seconds_is_auto
        or click_track_enabled
    )
    if beats_source == "tap":
        map_file = beats_cfg.get("map_file")
        if not isinstance(map_file, str) or not map_file.strip():
            raise ControllerError(
                "timing.beats.map_file is required for timing.beats.source=tap"
            )
        beat_map_path = (song_file.parent / map_file).resolve()
        try:
            beat_tap_map = load_beat_tap_map(beat_map_path)
        except SongConfigError as exc:
            raise ControllerError(str(exc)) from exc

        beat_event_map = beat_tap_map.get("event_map", {})
        if not isinstance(beat_event_map, dict):
            raise ControllerError("Beat tap map is missing event_map")

        beat_indices = sorted(beat_event_map)
        beat_times_seconds = [float(beat_event_map[i]) for i in beat_indices]
        if len(beat_times_seconds) < 2:
            raise ControllerError(
                "Beat tap map must contain at least two tapped beats"
            )

        if audio_start_offset_seconds > 0:
            beat_times_seconds = [
                t - audio_start_offset_seconds for t in beat_times_seconds
            ]

        try:
            inferred_beat_seconds = infer_beat_seconds_from_beats(
                beat_times_seconds
            )
            beat_reason = "from tapped beat map"
        except BeatAnalysisError as exc:
            raise ControllerError(str(exc)) from exc
    elif auto_beats_requested:
        try:
            if click_track_enabled:
                try:
                    click_grid = analyze_click_track_grid_from_audio(
                        beat_analysis_audio_path,
                    )
                    beat_times_seconds = list(click_grid.get("beat_times", []))
                    click_bar_start_times_seconds = list(
                        click_grid.get("bar_start_times", [])
                    )
                except BeatAnalysisError as exc:
                    emit(
                        "Warning: click marker analysis unavailable; "
                        f"falling back to legacy beat tracker ({exc})."
                    )
                    beat_times_seconds = analyze_beats_from_audio(
                        beat_analysis_audio_path,
                        start_bpm=auto_start_bpm,
                        dynamic_tempo=auto_dynamic_tempo,
                        dynamic_tempo_smoothing=auto_dynamic_tempo_smoothing,
                        dynamic_tempo_sensitivity=auto_dynamic_tempo_sensitivity,
                    )
                    click_bar_start_times_seconds = []
            else:
                beat_times_seconds = analyze_beats_from_audio(
                    beat_analysis_audio_path,
                    start_bpm=auto_start_bpm,
                    dynamic_tempo=auto_dynamic_tempo,
                    dynamic_tempo_smoothing=auto_dynamic_tempo_smoothing,
                    dynamic_tempo_sensitivity=auto_dynamic_tempo_sensitivity,
                )

            if audio_start_offset_seconds > 0:
                beat_times_seconds = [
                    t - audio_start_offset_seconds for t in beat_times_seconds
                ]
                if click_bar_start_times_seconds:
                    click_bar_start_times_seconds = [
                        t - audio_start_offset_seconds
                        for t in click_bar_start_times_seconds
                    ]

            click_marker_mode = click_track_enabled and bool(
                click_bar_start_times_seconds
            )

            if click_marker_mode:
                if abs(auto_tempo_factor - 1.0) >= 1e-9:
                    emit(
                        "Warning: timing.beats.auto_tempo_factor is ignored in "
                        "click-track marker mode."
                    )
                beat_times_seconds, click_shift_beats = (
                    _normalize_click_track_beats_to_audio_start(
                        beat_times_seconds
                    )
                )
                if beat_times_seconds and click_bar_start_times_seconds:
                    shift_seconds = beat_times_seconds[0]
                    click_bar_start_times_seconds = [
                        t - shift_seconds
                        for t in click_bar_start_times_seconds
                    ]
            else:
                beat_times_seconds = _apply_auto_tempo_factor(
                    beat_times_seconds, auto_tempo_factor
                )
                if auto_dynamic_tempo_smoothing > 1:
                    beat_times_seconds = _smooth_beat_times(
                        beat_times_seconds,
                        window=auto_dynamic_tempo_smoothing,
                    )
                click_shift_beats = 0

            inferred_beat_seconds = infer_beat_seconds_from_beats(
                beat_times_seconds
            )
            auto_details: list[str] = []

            if auto_start_bpm is not None and not click_marker_mode:
                auto_details.append(f"start bpm {auto_start_bpm:g}")
            if auto_dynamic_tempo and not click_marker_mode:
                auto_details.append("dynamic tempo")
                if auto_dynamic_tempo_smoothing > 1:
                    auto_details.append(
                        f"smoothing {auto_dynamic_tempo_smoothing}"
                    )
                if abs(auto_dynamic_tempo_sensitivity - 1.0) >= 1e-9:
                    auto_details.append(
                        f"sensitivity {auto_dynamic_tempo_sensitivity:g}"
                    )
            if abs(auto_tempo_factor - 1.0) >= 1e-9 and not click_marker_mode:
                auto_details.append(f"tempo factor {auto_tempo_factor:g}")
            if click_shift_beats > 0:
                auto_details.append(
                    f"click start aligned by {click_shift_beats} beats"
                )
            if click_marker_mode:
                auto_details.append("marker grid (kick bars + hat beats)")

            if auto_details:
                beat_reason = (
                    "from automatic beat analysis ("
                    + ", ".join(auto_details)
                    + ")"
                )
            else:
                beat_reason = "from automatic beat analysis"
        except BeatAnalysisError as exc:
            raise ControllerError(str(exc)) from exc

    emit(f"beat_analysis_audio={beat_analysis_audio_path}")
    if click_track_enabled and click_bar_start_times_seconds:
        emit(f"click_bar_markers={len(click_bar_start_times_seconds)}")

    tap_event_map: dict[int, float] = {}
    base_offset_seconds = 0.0
    raw_event_starts_seconds: list[float] = []

    if timing_source == "tap":
        map_file = lyrics_cfg.get("map_file")
        if not isinstance(map_file, str) or not map_file.strip():
            raise ControllerError(
                "timing.lyrics.map_file is required for timing.lyrics.source=tap"
            )

        tap_path = (song_file.parent / map_file).resolve()
        try:
            tap_map = load_tap_map(
                tap_path, event_count=len(song_data.get("events", []))
            )
        except SongConfigError as exc:
            raise ControllerError(str(exc)) from exc

        tap_event_map = tap_map.get("event_map", {})
        if not isinstance(tap_event_map, dict):
            raise ControllerError("tap map is missing event_map")

        raw_offset = lyrics_cfg.get("base_offset_seconds", 0.0)
        try:
            base_offset_seconds = float(raw_offset)
        except (TypeError, ValueError) as exc:
            raise ControllerError(
                "timing.lyrics.base_offset_seconds must be numeric"
            ) from exc

    final_beat_seconds = (
        beat_seconds if beat_seconds is not None else inferred_beat_seconds
    )
    if final_beat_seconds is None:
        raise ControllerError(
            "Unable to infer beat length from current data. Provide --beat-seconds "
            "explicitly."
        )

    if beats_source == "manual_grid" and isinstance(
        beats_cfg.get("bpm"), dict
    ):
        beat_reason = "from manual bpm map"

    video_cfg = (
        layout_data.get("video")
        if isinstance(layout_data.get("video"), dict)
        else {}
    )
    width = int(video_cfg.get("width", 1920))
    height = int(video_cfg.get("height", 1080))
    requested_fps = int(video_cfg.get("fps", MAX_RENDER_FPS))
    fps = requested_fps if requested_fps <= MAX_RENDER_FPS else MAX_RENDER_FPS
    if requested_fps > MAX_RENDER_FPS:
        emit(
            f"Requested FPS {requested_fps} capped to {MAX_RENDER_FPS} "
            "for practical output."
        )
    emit(f"render_fps={fps}")

    covered_beats_by_track_start = max(0, -beat_anchor_index)
    rendered_countdown_beats = effective_countdown_beats
    audio_delay_countdown_beats = max(
        0, effective_countdown_beats - covered_beats_by_track_start
    )
    full_countdown_duration_seconds = _quantized_countdown_duration_seconds(
        rendered_countdown_beats,
        final_beat_seconds,
        fps,
    )
    countdown_duration = _quantized_countdown_duration_seconds(
        audio_delay_countdown_beats,
        final_beat_seconds,
        fps,
    )
    cue_audio_lead_seconds = max(
        0.0, full_countdown_duration_seconds - countdown_duration
    )
    cue_beat_time_origin_seconds = cue_audio_lead_seconds
    if beat_anchor_index < 0 and beat_times_seconds:
        cue_beat_time_origin_seconds = _time_at_raw_beat_index(
            covered_beats_by_track_start,
            beat_seconds=final_beat_seconds,
            beat_times_seconds=beat_times_seconds,
        )

    emit(f"song_file={song_file}")
    emit(f"layout_file={resolved_layout_path}")
    emit(f"timing_source={timing_source}")
    emit(f"title_enabled={title_enabled}")
    emit(f"title_text={title_text}")
    emit(f"artist_text={artist_text}")
    emit(f"key_text={key_text}")
    emit(f"hot_start={hot_start}")
    emit(f"countdown_beats={effective_countdown_beats}")
    emit(f"rendered_countdown_beats={rendered_countdown_beats}")
    emit(f"audio_delay_countdown_beats={audio_delay_countdown_beats}")
    emit(f"cue_audio_lead_seconds={cue_audio_lead_seconds:.6f}")
    emit(f"cue_beat_time_origin_seconds={cue_beat_time_origin_seconds:.6f}")
    emit(f"beat_seconds={final_beat_seconds:.6f}")
    emit(f"beat_seconds_source={beat_reason}")
    emit(f"beat_anchor_beat_index={beat_anchor_index}")
    emit(f"beat_phase_offset={beat_phase_offset}")
    emit(f"cue_beat_anchor_beat_index={cue_beat_anchor_index}")
    emit(f"hold_reset_until_next_beat={hold_reset_until_next_beat}")
    emit(f"lyric_lead_in_beats={lyric_lead_in_beats:.6f}")
    emit(f"keep_postlude_beat_clock={keep_postlude_beat_clock}")
    if intro_lookahead_lead_beats is not None:
        emit(f"intro_lookahead_lead_beats={intro_lookahead_lead_beats:.6f}")
    else:
        emit("intro_lookahead_lead_beats=default_one_bar")
    title_first_chord_preview_lead_beats = (
        float(intro_lookahead_lead_beats)
        if intro_lookahead_lead_beats is not None
        else float(beats_per_bar)
    )
    emit(f"audio_start_offset_seconds={audio_start_offset_seconds:.6f}")
    emit(f"base_offset_seconds={base_offset_seconds:.6f}")
    emit(f"from_seconds={section_from_seconds:.6f}")
    if section_to_seconds is not None:
        emit(f"to_seconds={section_to_seconds:.6f}")
    else:
        emit("to_seconds=none")
    emit(f"countdown_duration_seconds={countdown_duration:.6f}")
    emit(f"backing_track_starts_at_video_time={countdown_duration:.6f}")

    background = (
        video_cfg.get("background")
        if isinstance(video_cfg.get("background"), dict)
        else {}
    )
    background_color = str(background.get("color", "#000000"))

    missing = [
        idx for idx in range(len(song_events)) if idx not in tap_event_map
    ]
    if missing:
        raise ControllerError(
            "Tap map is missing cue timings for indices: "
            + ", ".join(str(i) for i in missing[:10])
        )

    # Keep event 0 anchored at cue-sequence start; apply base offset to later
    # cue transitions so sync adjustments are visible in rendered output.
    raw_event_starts_seconds = [
        tap_event_map[i] for i in range(len(song_events))
    ]
    event_starts_seconds = [
        (
            raw_event_starts_seconds[i]
            if i == 0
            else raw_event_starts_seconds[i] + base_offset_seconds
        )
        for i in range(len(song_events))
    ]
    full_event_beats_per_bar = [beats_per_bar] * len(song_events)
    if (
        click_track_enabled
        and beat_times_seconds
        and click_bar_start_times_seconds
    ):
        full_event_beats_per_bar = _infer_event_beats_per_bar_from_click(
            event_starts_seconds=event_starts_seconds,
            beat_seconds=final_beat_seconds,
            beat_times_seconds=beat_times_seconds,
            bar_start_times_seconds=click_bar_start_times_seconds,
            fallback_beats_per_bar=beats_per_bar,
        )
    full_event_beats_per_bar = _event_beats_per_bar_with_overrides(
        song_events=song_events,
        inferred_event_beats_per_bar=full_event_beats_per_bar,
        fallback_beats_per_bar=beats_per_bar,
    )
    manual_grid_bpm_changes: list[tuple[int, float]] | None = None
    if beats_source == "manual_grid" and isinstance(
        beats_cfg.get("bpm"), dict
    ):
        bpm_changes, bpm_changes_error = _parse_bpm_changes(
            beats_cfg.get("bpm", {}),
            beats_per_bar=_beats_per_bar_from_timing(beats_cfg),
        )
        if bpm_changes_error is not None:
            raise ControllerError(bpm_changes_error)
        manual_grid_bpm_changes = bpm_changes

        if manual_grid_bpm_changes:
            manual_grid_duration = max(0.0, event_starts_seconds[-1]) + max(
                final_beat_seconds * 4.0, 1.0
            )
            beat_times_seconds = _build_manual_grid_beat_times(
                bpm_changes=manual_grid_bpm_changes,
                duration_seconds=manual_grid_duration,
            )
            beat_reason = "from manual bpm map"

    quantize_enabled = default_quantize_factor > 0 or any(
        factor is not None and factor > 0 for factor in event_quantize_factors
    )
    quantized_event_starts = 0
    if quantize_enabled:
        event_starts_seconds, quantized_event_starts = (
            _quantize_event_starts_to_beats(
                event_starts_seconds,
                beat_seconds=final_beat_seconds,
                beat_times_seconds=beat_times_seconds,
                default_quantize_factor=default_quantize_factor,
                event_quantize_factors=event_quantize_factors,
            )
        )

    # Keep starts monotonic for frame math even with negative offsets.
    for i in range(1, len(event_starts_seconds)):
        if event_starts_seconds[i] < event_starts_seconds[i - 1]:
            event_starts_seconds[i] = event_starts_seconds[i - 1]

    if click_track_enabled and beat_times_seconds:
        full_event_beats_per_bar = _infer_event_beats_per_bar_from_click(
            event_starts_seconds=event_starts_seconds,
            beat_seconds=final_beat_seconds,
            beat_times_seconds=beat_times_seconds,
            bar_start_times_seconds=click_bar_start_times_seconds,
            fallback_beats_per_bar=beats_per_bar,
        )

    if manual_grid_bpm_changes:
        manual_grid_duration = max(0.0, event_starts_seconds[-1]) + max(
            final_beat_seconds * 4.0, 1.0
        )
        beat_times_seconds = _build_manual_grid_beat_times(
            bpm_changes=manual_grid_bpm_changes,
            duration_seconds=manual_grid_duration,
        )

    if beat_times_seconds is None:
        uniform_grid_duration = max(0.0, event_starts_seconds[-1]) + max(
            final_beat_seconds * 4.0, 1.0
        )
        beat_times_seconds = _build_uniform_beat_times(
            beat_seconds=final_beat_seconds,
            duration_seconds=uniform_grid_duration,
        )

    loop_timeline = _build_loop_bar_timeline(
        event_starts_seconds=event_starts_seconds,
        beat_seconds=final_beat_seconds,
        beat_times_seconds=beat_times_seconds,
        event_beats_per_bar=full_event_beats_per_bar,
        fallback_beats_per_bar=beats_per_bar,
        bar_start_times_seconds=click_bar_start_times_seconds,
    )

    if intro_lookahead_lead_beats is None and full_event_beats_per_bar:
        title_first_chord_preview_lead_beats = float(
            full_event_beats_per_bar[0]
        )

    base_cue_tail_seconds = max(final_beat_seconds * 4.0, 1.0)
    phrase_tail_seconds = _cue_tail_to_phrase_boundary_seconds(
        last_event_start_seconds=event_starts_seconds[-1],
        beat_seconds=final_beat_seconds,
        beat_times_seconds=beat_times_seconds,
        beat_anchor_index=cue_beat_anchor_index,
        beats_per_phrase=default_beats_per_phrase,
    )
    cue_tail_seconds = max(base_cue_tail_seconds, phrase_tail_seconds)

    cue_section_from_seconds = section_from_seconds + cue_audio_lead_seconds
    cue_section_to_seconds = (
        section_to_seconds + cue_audio_lead_seconds
        if section_to_seconds is not None
        else None
    )

    render_song_events = song_events
    render_event_starts_seconds = event_starts_seconds
    render_cue_tail_seconds = cue_tail_seconds
    render_event_beats_per_bar = full_event_beats_per_bar
    if cue_section_from_seconds > 0.0 or cue_section_to_seconds is not None:
        (
            render_song_events,
            render_event_starts_seconds,
            render_cue_tail_seconds,
            clipped_cue_duration,
            render_event_indices,
        ) = _slice_events_to_time_range(
            song_events=song_events,
            event_starts_seconds=event_starts_seconds,
            tail_seconds=cue_tail_seconds,
            from_seconds=cue_section_from_seconds,
            to_seconds=cue_section_to_seconds,
        )
        if not render_song_events:
            raise ControllerError(
                "Requested time range does not overlap any cue events after "
                "timing adjustments"
            )
        render_event_beats_per_bar = [
            full_event_beats_per_bar[i] for i in render_event_indices
        ]
        emit(f"clipped_cue_duration_seconds={clipped_cue_duration:.6f}")

    emit(f"cue_tail_seconds={cue_tail_seconds:.6f}")
    emit(f"quantize_factor={default_quantize_factor:.6f}")
    emit(f"quantized_event_starts={quantized_event_starts}")
    ffmpeg_bin = os.getenv("PRAECENTOR_FFMPEG_BIN", "ffmpeg")
    ffmpeg_path = (
        shutil.which(ffmpeg_bin)
        if not os.path.isabs(ffmpeg_bin)
        else ffmpeg_bin
    )
    if not ffmpeg_path:
        raise ControllerError(
            "ffmpeg executable not found. Install ffmpeg or set PRAECENTOR_FFMPEG_BIN."
        )

    stem_duration: float | None = None
    if stem_audio is not None and not ignore_stem_length_mismatch:
        all_stem_tracks = list(stem_audio["track_index"].values())
        stem_duration = _validate_uniform_track_durations(
            tracks=all_stem_tracks,
            ffmpeg_path=str(ffmpeg_path),
            tolerance_seconds=stem_duration_tolerance,
        )
        emit(f"stem_duration_seconds={stem_duration:.6f}")

    if maestro_only:
        if stem_audio is None:
            raise ControllerError(
                "--maestro-only requires audio.stems in the song config"
            )

        maestro_cfg = song_data.get("maestro")
        if not isinstance(maestro_cfg, dict):
            raise ControllerError(
                "--maestro-only requires a maestro mapping with output.file"
            )

        markers = _build_marker_entries(song_events, event_starts_seconds)
        maestro_zip_path = _write_maestro_zip(
            maestro_cfg=maestro_cfg,
            title_text=title_text,
            groups_manifest=stem_audio["groups_manifest"],
            layers_manifest=stem_audio["layers_manifest"],
            markers=markers,
            loop_timeline=loop_timeline,
            track_index=stem_audio["track_index"],
            song_file=song_file,
        )
        emit(f"Wrote Maestro zip: {maestro_zip_path}")
        emit("Render skipped (--maestro-only).")
        return

    cue_program_duration = 0.0
    if render_event_starts_seconds:
        cue_program_duration = (
            render_event_starts_seconds[-1] + render_cue_tail_seconds
        )

    probed_audio_duration = _probe_audio_duration_seconds(
        audio_path, str(ffmpeg_path)
    )
    if probed_audio_duration is None and stem_audio is not None:
        probed_audio_duration = stem_duration

    ffmpeg_audio_seek_seconds = (
        audio_start_offset_seconds + section_from_seconds
    )
    selected_audio_duration: float | None = None
    if probed_audio_duration is not None:
        usable_audio_duration = max(
            0.0, probed_audio_duration - audio_start_offset_seconds
        )
        section_audio_start = min(
            max(0.0, section_from_seconds), usable_audio_duration
        )
        section_audio_end_default = usable_audio_duration
        section_audio_end = (
            min(
                max(section_audio_start, section_to_seconds),
                usable_audio_duration,
            )
            if section_to_seconds is not None
            else section_audio_end_default
        )
        selected_audio_duration = max(
            0.0, section_audio_end - section_audio_start
        )
        target_output_duration = countdown_duration + selected_audio_duration
        emit(f"audio_duration_seconds={probed_audio_duration:.6f}")
        emit(f"usable_audio_duration_seconds={usable_audio_duration:.6f}")
        emit(f"selected_audio_duration_seconds={selected_audio_duration:.6f}")
        emit(f"target_output_duration_seconds={target_output_duration:.6f}")
    else:
        target_output_duration = countdown_duration + cue_program_duration
        emit(
            "audio_duration_seconds=unavailable (ffprobe not found or probe failed)"
        )
        emit(
            "target_output_duration_seconds="
            f"{target_output_duration:.6f} (fallback from cue timeline)"
        )

    cue_postlude_seconds = 0.0
    if selected_audio_duration is not None:
        cue_runtime_seconds = max(
            0.0, selected_audio_duration - cue_audio_lead_seconds
        )
        cue_postlude_seconds = max(
            0.0, cue_runtime_seconds - cue_program_duration
        )
    if keep_postlude_beat_clock is None:
        keep_postlude_beat_clock = cue_postlude_seconds > 0.0
    emit(f"cue_postlude_seconds={cue_postlude_seconds:.6f}")

    if output_video is not None:
        output_targets = [{"file": output_video.resolve(), "tracks": None}]
    elif song_output_specs:
        output_targets = [
            {
                "file": spec["file"],
                "tracks": spec.get("tracks"),
            }
            for spec in song_output_specs
        ]
    else:
        output_targets = [
            {"file": Path.home() / f"{song_file.stem}.mp4", "tracks": None}
        ]

    for target in output_targets:
        target["file"].parent.mkdir(parents=True, exist_ok=True)

    _prevalidate_output_targets(
        output_targets=output_targets, stem_audio=stem_audio
    )

    emit(f"video_output_count={len(output_targets)}")

    expected_title_frames = (
        _estimate_title_frame_count(
            rendered_countdown_beats, final_beat_seconds, fps
        )
        if title_enabled
        else 0
    )
    expected_cue_frames = _estimate_cue_frame_count(
        render_event_starts_seconds,
        render_cue_tail_seconds,
        fps,
    )
    if cue_postlude_seconds > 0:
        expected_cue_frames += max(1, int(round(cue_postlude_seconds * fps)))
    total_expected_frames = expected_title_frames + expected_cue_frames
    emit(f"expected_total_frame_count={total_expected_frames}")

    title_result: dict[str, Any]
    cue_result: dict[str, Any]

    frame_progress = progressbar(
        length=max(1, total_expected_frames),
        label="Rendering frames",
    )

    def _tick_frame() -> None:
        frame_progress.update(1)

    try:
        background_layer_renderer = build_background_layer_renderer(
            background_config=background,
            layout_path=resolved_layout_path,
            width=width,
            height=height,
        )
    except SongConfigError as exc:
        raise ControllerError(str(exc)) from exc

    background_frame_provider = (
        background_layer_renderer.next_frame
        if background_layer_renderer is not None
        else None
    )

    silent_video_path: Path | None = None
    padded_video_path: Path | None = None

    try:
        with frame_progress:
            silent_fd, silent_path_raw = tempfile.mkstemp(suffix=".silent.mp4")
            os.close(silent_fd)
            silent_video_path = Path(silent_path_raw)

            direct_session: _DirectFrameEncodeSession | None = None

            ffmpeg_cmd = [
                str(ffmpeg_path),
                "-y",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-s",
                f"{width}x{height}",
                "-r",
                str(fps),
                "-i",
                "-",
            ]
            ffmpeg_cmd.extend(
                [
                    "-c:v",
                    str(video_encoder),
                    "-r",
                    str(fps),
                    "-fps_mode",
                    "cfr",
                    "-g",
                    str(max(1, fps * 2)),
                    "-keyint_min",
                    str(max(1, fps * 2)),
                    "-sc_threshold",
                    "0",
                    "-an",
                ]
            )
            if video_encoder.lower() == "libx264":
                ffmpeg_cmd.extend(["-preset", str(video_preset)])
                if video_crf is not None:
                    ffmpeg_cmd.extend(["-crf", str(video_crf)])
            if video_bitrate is not None and str(video_bitrate).strip():
                ffmpeg_cmd.extend(["-b:v", str(video_bitrate).strip()])
            ffmpeg_cmd.extend(["-pix_fmt", "yuv420p"])
            ffmpeg_cmd.append(str(silent_video_path))
            direct_session = _DirectFrameEncodeSession(
                ffmpeg_cmd=ffmpeg_cmd,
                frame_queue_size=frame_queue_size,
            )

            frame_sink = direct_session.frame_sink

            scratch_output_dir = output_targets[0]["file"].parent
            frame_generation_started_at = perf_counter()
            try:
                if title_enabled:
                    title_result = render_title_countdown_frames(
                        output_dir=scratch_output_dir,
                        width=width,
                        height=height,
                        fps=fps,
                        background_color=background_color,
                        title_text=title_text,
                        artist_text=artist_text,
                        key_text=key_text,
                        hot_start=hot_start,
                        countdown_beats=rendered_countdown_beats,
                        beat_seconds=final_beat_seconds,
                        title_layout=(
                            title_cfg if isinstance(title_cfg, dict) else {}
                        ),
                        countdown_start_number=effective_countdown_beats,
                        first_chord_preview_text=first_event_chord_preview,
                        first_chord_preview_lead_beats=title_first_chord_preview_lead_beats,  # NOQA E501
                        background_frame_provider=background_frame_provider,
                        background_is_transparent=False,
                        frame_sink=frame_sink,
                        frame_progress_callback=_tick_frame,
                    )
                else:
                    emit(
                        "Title card is disabled in layout. No title frames rendered."
                    )
                    title_result = {"frame_count": 0, "frames_per_beat": 0}

                cue_result = render_cue_frames(
                    output_dir=scratch_output_dir,
                    width=width,
                    height=height,
                    fps=fps,
                    background_color=background_color,
                    song_events=render_song_events,
                    event_starts_seconds=render_event_starts_seconds,
                    tail_seconds=render_cue_tail_seconds,
                    layout_data=layout_data,
                    beat_seconds=final_beat_seconds,
                    beats_per_bar=beats_per_bar,
                    event_beats_per_bar=render_event_beats_per_bar,
                    default_beats_per_phrase=default_beats_per_phrase,
                    beat_anchor_index=cue_beat_anchor_index,
                    phrase_anchor_index=cue_beat_anchor_index,
                    beat_times_seconds=beat_times_seconds,
                    bar_start_times_seconds=click_bar_start_times_seconds,
                    beat_time_offsets_seconds=None,
                    hold_reset_until_next_beat=hold_reset_until_next_beat,
                    keep_postlude_beat_clock=keep_postlude_beat_clock,
                    lyric_lead_in_beats=lyric_lead_in_beats,
                    intro_lookahead_lead_beats=intro_lookahead_lead_beats,
                    time_origin_seconds=section_from_seconds
                    + cue_beat_time_origin_seconds,
                    postlude_seconds=cue_postlude_seconds,
                    riff_key_text=key_text,
                    background_frame_provider=background_frame_provider,
                    background_is_transparent=False,
                    frame_sink=frame_sink,
                    frame_progress_callback=_tick_frame,
                )
                frame_generation_elapsed = (
                    perf_counter() - frame_generation_started_at
                )
                emit(
                    f"timing_frame_generation_seconds={frame_generation_elapsed:.6f}"
                )
            finally:
                if direct_session is not None:
                    direct_session.finish_writing()

            assert direct_session is not None
            direct_session.wait_and_validate(emit=emit)

            emit(f"Rendered silent video to: {silent_video_path}")

            mux_video_source_path = silent_video_path
            if target_output_duration is not None:
                padded_fd, padded_path_raw = tempfile.mkstemp(
                    suffix=".padded.mp4"
                )
                os.close(padded_fd)
                padded_video_path = Path(padded_path_raw)

                pad_started_at = perf_counter()
                pad_duration = max(0.001, float(target_output_duration))
                pad_cmd = [
                    str(ffmpeg_path),
                    "-y",
                    "-i",
                    str(silent_video_path),
                    "-vf",
                    f"tpad=stop_mode=clone:stop_duration={pad_duration:.6f}",
                    "-c:v",
                    str(video_encoder),
                ]
                if video_encoder.lower() == "libx264":
                    pad_cmd.extend(["-preset", str(video_preset)])
                    if video_crf is not None:
                        pad_cmd.extend(["-crf", str(video_crf)])
                if video_bitrate is not None and str(video_bitrate).strip():
                    pad_cmd.extend(["-b:v", str(video_bitrate).strip()])
                pad_cmd.extend(
                    [
                        "-pix_fmt",
                        "yuv420p",
                        "-t",
                        f"{target_output_duration:.6f}",
                        str(padded_video_path),
                    ]
                )

                pad_proc = subprocess.run(pad_cmd, capture_output=True)
                pad_elapsed = perf_counter() - pad_started_at
                emit(f"timing_video_padding_seconds={pad_elapsed:.6f}")
                if pad_proc.returncode != 0:
                    pad_err = pad_proc.stderr.decode(
                        "utf-8", errors="replace"
                    ).strip()
                    raise ControllerError(
                        "ffmpeg video padding failed:\n"
                        + (pad_err or "unknown error")
                    )
                mux_video_source_path = padded_video_path
                emit(f"Prepared padded mux video: {mux_video_source_path}")

            audio_delay_ms = int(round(countdown_duration * 1000.0))
            rendered_video_paths: list[Path] = []
            mux_failures: list[tuple[Path, str]] = []

            cpu_count = os.cpu_count() or 1
            max_mux_workers = max(
                1, min(len(output_targets), cpu_count, DEFAULT_MUX_MAX_WORKERS)
            )
            emit(f"mux_parallel_workers={max_mux_workers}")

            for target_idx, target in enumerate(output_targets, start=1):
                target_path: Path = target["file"]
                emit(
                    f"Muxing output {target_idx}/{len(output_targets)}: {target_path}"
                )

            mux_wall_started_at = perf_counter()
            mux_results: list[tuple[int, Path, bool, str, float]] = []
            if max_mux_workers == 1:
                for target_idx, target in enumerate(output_targets, start=1):
                    mux_results.append(
                        _run_mux_for_target(
                            target_idx=target_idx,
                            target=target,
                            mux_video_source_path=mux_video_source_path,
                            ffmpeg_path=str(ffmpeg_path),
                            ffmpeg_audio_seek_seconds=ffmpeg_audio_seek_seconds,
                            audio_path=audio_path,
                            stem_audio=stem_audio,
                            audio_delay_ms=audio_delay_ms,
                            target_output_duration=target_output_duration,
                        )
                    )
            else:
                with ThreadPoolExecutor(
                    max_workers=max_mux_workers, thread_name_prefix="mux"
                ) as executor:
                    futures = {
                        executor.submit(
                            _run_mux_for_target,
                            target_idx=target_idx,
                            target=target,
                            mux_video_source_path=mux_video_source_path,
                            ffmpeg_path=str(ffmpeg_path),
                            ffmpeg_audio_seek_seconds=ffmpeg_audio_seek_seconds,
                            audio_path=audio_path,
                            stem_audio=stem_audio,
                            audio_delay_ms=audio_delay_ms,
                            target_output_duration=target_output_duration,
                        ): target_idx
                        for target_idx, target in enumerate(
                            output_targets, start=1
                        )
                    }
                    for future in as_completed(futures):
                        mux_results.append(future.result())

            mux_results.sort(key=lambda row: row[0])

            mux_work_sum_seconds = 0.0
            for (
                target_idx,
                target_path,
                success,
                error,
                mux_elapsed,
            ) in mux_results:
                mux_work_sum_seconds += mux_elapsed
                emit(
                    f"timing_mux_output_seconds[{target_idx}]={mux_elapsed:.6f}"
                )
                if success:
                    rendered_video_paths.append(target_path)
                else:
                    mux_failures.append((target_path, error))

            mux_wall_elapsed = perf_counter() - mux_wall_started_at
            emit(f"timing_mux_total_seconds={mux_wall_elapsed:.6f}")
            emit(f"timing_mux_work_sum_seconds={mux_work_sum_seconds:.6f}")

            if stem_audio is not None:
                maestro_cfg = song_data.get("maestro")
                if maestro_cfg is not None:
                    if not isinstance(maestro_cfg, dict):
                        raise ControllerError(
                            "maestro must be a mapping when provided"
                        )
                    markers = _build_marker_entries(
                        song_events, event_starts_seconds
                    )
                    maestro_zip_path = _write_maestro_zip(
                        maestro_cfg=maestro_cfg,
                        title_text=title_text,
                        groups_manifest=stem_audio["groups_manifest"],
                        layers_manifest=stem_audio["layers_manifest"],
                        markers=markers,
                        loop_timeline=loop_timeline,
                        track_index=stem_audio["track_index"],
                        song_file=song_file,
                    )
                    emit(f"Wrote Maestro zip: {maestro_zip_path}")

            emit("Rendered video outputs:")
            for path in rendered_video_paths:
                emit(f"Rendered video to: {path}")
                emit(f"- {path}")

            if mux_failures:
                emit("Mux failures:")
                for path, error in mux_failures:
                    first_line = (
                        error.splitlines()[0] if error else "unknown error"
                    )
                    emit(f"- {path}: {first_line}")
                if not rendered_video_paths:
                    raise ControllerError(
                        "No output videos were generated. See mux failures above."
                    )

    finally:
        if background_layer_renderer is not None:
            background_layer_renderer.close()
        for temp_path in (
            silent_video_path,
            padded_video_path,
        ):
            if temp_path is None:
                continue
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    emit(f"rendered_title_frame_count={title_result['frame_count']}")
    emit(f"rendered_title_frames_per_beat={title_result['frames_per_beat']}")
    emit(f"rendered_cue_frame_count={cue_result['frame_count']}")
    emit(
        f"timing_render_total_seconds={perf_counter() - render_started_at:.6f}"
    )

    emit("Render completed.")
