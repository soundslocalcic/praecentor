from __future__ import annotations

from bisect import bisect_left
from pathlib import Path
import statistics
from typing import Any


class BeatAnalysisError(RuntimeError):
    pass


def median_positive_delta(values: list[float]) -> float | None:
    deltas = [b - a for a, b in zip(values, values[1:]) if b - a > 0]
    if not deltas:
        return None
    ordered = sorted(deltas)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def analyze_beats_from_audio(
    audio_path: Path,
    *,
    start_bpm: float | None = None,
    dynamic_tempo: bool = False,
    dynamic_tempo_smoothing: int = 1,
    dynamic_tempo_sensitivity: float = 1.0,
    hop_length: int = 128,
) -> list[float]:
    hop = int(hop_length)
    if hop < 1:
        raise BeatAnalysisError("hop_length must be >= 1")

    try:
        import librosa
    except (
        Exception
    ) as exc:  # pragma: no cover - depends on local optional package
        raise BeatAnalysisError(
            "Auto beat analysis requires librosa. Install it with: pip install librosa"
        ) from exc

    try:
        y, sr = librosa.load(str(audio_path), sr=None, mono=True)
    except Exception as exc:
        raise BeatAnalysisError(
            f"Failed to load audio for beat analysis: {audio_path}"
        ) from exc

    try:
        if dynamic_tempo:
            smoothing = int(dynamic_tempo_smoothing)
            if smoothing < 1:
                raise BeatAnalysisError("dynamic_tempo_smoothing must be >= 1")
            sensitivity = float(dynamic_tempo_sensitivity)
            if sensitivity < 0 or sensitivity > 1:
                raise BeatAnalysisError(
                    "dynamic_tempo_sensitivity must be between 0 and 1"
                )

            onset_envelope = librosa.onset.onset_strength(
                y=y, sr=sr, hop_length=hop
            )
            tempo_kwargs: dict[str, object] = {
                "onset_envelope": onset_envelope,
                "sr": sr,
                "aggregate": None,
                "hop_length": hop,
            }
            if start_bpm is not None:
                tempo_kwargs["start_bpm"] = float(start_bpm)
            dynamic_bpm = librosa.feature.tempo(**tempo_kwargs)
            if getattr(dynamic_bpm, "ndim", 1) > 1:
                dynamic_bpm = dynamic_bpm[0]

            dynamic_bpm = _smooth_dynamic_bpm_curve(
                dynamic_bpm, window=smoothing
            )
            dynamic_bpm = _apply_dynamic_bpm_sensitivity(
                dynamic_bpm,
                sensitivity=sensitivity,
                reference_bpm=start_bpm,
            )

            _, beat_frames = librosa.beat.beat_track(
                onset_envelope=onset_envelope,
                sr=sr,
                bpm=dynamic_bpm,
                units="frames",
                hop_length=hop,
            )
        else:
            beat_track_kwargs: dict[str, object] = {
                "y": y,
                "sr": sr,
                "units": "frames",
                "hop_length": hop,
            }
            if start_bpm is not None:
                beat_track_kwargs["start_bpm"] = float(start_bpm)
            _, beat_frames = librosa.beat.beat_track(**beat_track_kwargs)

        beat_times = librosa.frames_to_time(
            beat_frames, sr=sr, hop_length=hop
        ).tolist()
    except Exception as exc:
        raise BeatAnalysisError("librosa beat analysis failed") from exc

    beats = [float(t) for t in beat_times if t >= 0]
    if len(beats) < 2:
        raise BeatAnalysisError(
            "Auto beat analysis found too few beats in the audio"
        )
    return beats


def _smooth_dynamic_bpm_curve(dynamic_bpm: Any, *, window: int) -> Any:
    if window <= 1:
        return dynamic_bpm
    if window % 2 == 0:
        window += 1

    try:
        import numpy as np
    except (
        Exception
    ) as exc:  # pragma: no cover - numpy is expected with librosa
        raise BeatAnalysisError(
            "Dynamic tempo smoothing requires numpy"
        ) from exc

    values = np.asarray(dynamic_bpm, dtype=float)
    if values.size < 3:
        return values

    half = window // 2
    padded = np.pad(values, (half, half), mode="edge")
    smoothed = np.empty_like(values)
    for idx in range(values.size):
        segment = padded[idx: idx + window]
        smoothed[idx] = float(np.median(segment))
    return smoothed


def _apply_dynamic_bpm_sensitivity(
    dynamic_bpm: Any,
    *,
    sensitivity: float,
    reference_bpm: float | None,
) -> Any:
    if sensitivity >= 0.999999:
        return dynamic_bpm

    try:
        import numpy as np
    except (
        Exception
    ) as exc:  # pragma: no cover - numpy is expected with librosa
        raise BeatAnalysisError(
            "Dynamic tempo sensitivity requires numpy"
        ) from exc

    values = np.asarray(dynamic_bpm, dtype=float)
    if values.size == 0:
        return values

    # Keep dynamic tempo centered on the observed click-track tempo curve.
    # A configured start_bpm is only a tracking hint; using it as the blend
    # anchor can flatten real ramps (for example 140 -> 150) into a near-flat
    # line when sensitivity is below 1.0.
    reference = float(np.median(values))
    return reference + ((values - reference) * float(sensitivity))


def infer_beat_seconds_from_beats(beat_times: list[float]) -> float:
    deltas = [b - a for a, b in zip(beat_times, beat_times[1:]) if b - a > 0]
    if not deltas:
        raise BeatAnalysisError(
            "Unable to infer beat duration from analyzed beat times"
        )

    sample_size = min(len(deltas), 24)
    prefix_deltas = deltas[:sample_size]
    prefix_median = statistics.median(prefix_deltas)
    if prefix_median > 0:
        return float(prefix_median)

    median_delta = median_positive_delta(beat_times)
    if median_delta is None or median_delta <= 0:
        raise BeatAnalysisError(
            "Unable to infer beat duration from analyzed beat times"
        )
    return float(median_delta)


def _dedupe_times_with_min_gap(
    times: list[float], *, min_gap_seconds: float
) -> list[float]:
    if not times:
        return []
    out: list[float] = []
    gap = max(0.0, float(min_gap_seconds))
    for value in sorted(float(t) for t in times):
        if not out or (value - out[-1]) >= gap:
            out.append(value)
    return out


def _snap_to_nearest_grid(
    values: list[float], grid: list[float], *, max_error_seconds: float
) -> list[float]:
    if not values or not grid:
        return []

    max_error = max(0.0, float(max_error_seconds))
    snapped: list[float] = []
    for value in values:
        idx = bisect_left(grid, value)
        candidate_indices: list[int] = []
        if idx < len(grid):
            candidate_indices.append(idx)
        if idx > 0:
            candidate_indices.append(idx - 1)
        if not candidate_indices:
            continue

        best_idx = min(candidate_indices, key=lambda i: abs(grid[i] - value))
        best = float(grid[best_idx])
        if abs(best - value) <= max_error:
            if not snapped or abs(best - snapped[-1]) > 1e-9:
                snapped.append(best)
    return snapped


def analyze_click_track_grid_from_audio(
    audio_path: Path,
    *,
    hop_length: int = 128,
) -> dict[str, list[float]]:
    """Extract click markers from isolated click audio.

    Returns a mapping with:
    - beat_times: quarter-note beat boundaries (from hat pulses)
    - bar_start_times: bar boundaries (from kick pulses snapped to beat grid)
    - hat_times: detected hat pulse times
    - kick_times: detected kick pulse times
    """
    hop = int(hop_length)
    if hop < 1:
        raise BeatAnalysisError("hop_length must be >= 1")

    try:
        import librosa
        import numpy as np
    except Exception as exc:  # pragma: no cover - depends on optional packages
        raise BeatAnalysisError(
            "Click-track analysis requires librosa and numpy. "
            "Install with: pip install librosa"
        ) from exc

    try:
        y, sr = librosa.load(str(audio_path), sr=None, mono=True)
    except Exception as exc:
        raise BeatAnalysisError(
            f"Failed to load click track audio: {audio_path}"
        ) from exc

    if y.size == 0:
        raise BeatAnalysisError("Click track audio is empty")

    try:
        stft_mag = np.abs(librosa.stft(y=y, n_fft=2048, hop_length=hop))
        freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    except Exception as exc:
        raise BeatAnalysisError(
            "Click-track spectral analysis failed"
        ) from exc

    low_mask = freqs <= 220.0
    high_mask = freqs >= 4000.0
    if not np.any(low_mask) or not np.any(high_mask):
        raise BeatAnalysisError(
            "Click-track spectral masks are invalid for current sample rate"
        )

    high_envelope = np.asarray(stft_mag[high_mask, :].sum(axis=0), dtype=float)

    try:
        hat_frames = librosa.util.peak_pick(
            high_envelope,
            pre_max=2,
            post_max=2,
            pre_avg=4,
            post_avg=4,
            delta=0.03,
            wait=1,
        )
    except Exception as exc:
        raise BeatAnalysisError("Click-track peak picking failed") from exc

    hat_times = _dedupe_times_with_min_gap(
        librosa.frames_to_time(hat_frames, sr=sr, hop_length=hop).tolist(),
        # Keep enough headroom for tempo ramps while still rejecting obvious
        # doubled peaks.
        min_gap_seconds=0.3,
    )
    if len(hat_times) < 8:
        raise BeatAnalysisError(
            "Click-track analysis found too few hat pulses"
        )

    try:
        onset_envelope = librosa.onset.onset_strength(
            y=y, sr=sr, hop_length=hop
        )
        onset_frames = librosa.onset.onset_detect(
            onset_envelope=onset_envelope,
            sr=sr,
            hop_length=hop,
            units="frames",
            backtrack=False,
        )
    except Exception as exc:
        raise BeatAnalysisError("Click-track onset detection failed") from exc

    if len(onset_frames) < 4:
        raise BeatAnalysisError(
            "Click-track analysis found too few kick candidates"
        )

    onset_times = librosa.frames_to_time(onset_frames, sr=sr, hop_length=hop)
    kick_scores: list[float] = []
    low_ratios: list[float] = []
    for frame in onset_frames:
        f_idx = int(max(0, min(stft_mag.shape[1] - 1, frame)))
        total_energy = float(stft_mag[:, f_idx].sum()) + 1e-9
        low_energy = float(stft_mag[low_mask, f_idx].sum())
        high_energy = float(stft_mag[high_mask, f_idx].sum())
        low_ratio = low_energy / total_energy
        high_ratio = high_energy / total_energy
        low_ratios.append(low_ratio)
        kick_scores.append(low_ratio - high_ratio)

    kick_threshold = float(
        np.percentile(np.asarray(kick_scores, dtype=float), 70.0)
    )
    kick_candidates = [
        float(onset_times[i])
        for i in range(len(onset_times))
        if kick_scores[i] >= kick_threshold and low_ratios[i] >= 0.10
    ]
    raw_kick_times = _dedupe_times_with_min_gap(
        kick_candidates, min_gap_seconds=0.45
    )

    beat_times = list(hat_times)
    if len(beat_times) < 2:
        raise BeatAnalysisError(
            "Click-track analysis found too few beat pulses"
        )

    kick_times = _snap_to_nearest_grid(
        raw_kick_times, beat_times, max_error_seconds=0.18
    )
    if len(kick_times) < 2:
        raise BeatAnalysisError(
            "Click-track analysis found too few bar-start kicks"
        )

    return {
        "beat_times": [float(t) for t in beat_times],
        "bar_start_times": [float(t) for t in kick_times],
        "hat_times": [float(t) for t in hat_times],
        "kick_times": [float(t) for t in kick_times],
    }
