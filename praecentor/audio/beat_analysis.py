from __future__ import annotations

import statistics
from bisect import bisect_left
from pathlib import Path
from typing import Any

ANALYSIS_SAMPLE_RATE = 22050
DEFAULT_ANALYSIS_HOP_LENGTH = 256

CLICK_HAT_HIGHPASS_HZ = 5000.0
CLICK_KICK_LOWPASS_HZ = 150.0
# Allows hat tempos up to 300 BPM during ramps.
CLICK_HAT_MIN_GAP_SECONDS = 0.2
CLICK_KICK_MIN_GAP_SECONDS = 0.45


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
    hop_length: int = DEFAULT_ANALYSIS_HOP_LENGTH,
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
        y, sr = librosa.load(
            str(audio_path), sr=ANALYSIS_SAMPLE_RATE, mono=True
        )
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


def _band_envelope(
    y: Any, sos: Any, *, block: int, hold_bins: int = 1
) -> Any:
    import numpy as np
    from scipy.ndimage import maximum_filter1d
    from scipy.signal import sosfiltfilt

    rectified = np.abs(sosfiltfilt(sos, y)).astype(np.float32, copy=False)
    bins = rectified.size // block
    envelope = rectified[: bins * block].reshape(bins, block).max(axis=1)
    if hold_bins > 1:
        envelope = maximum_filter1d(envelope, size=hold_bins)
    return envelope


def _pick_pulse_times(
    envelope: Any,
    *,
    seconds_per_bin: float,
    min_gap_seconds: float,
    relative_height: float = 0.25,
    floor_ratio: float = 0.05,
    window_seconds: float = 3.0,
) -> list[float]:
    import numpy as np
    from scipy.ndimage import maximum_filter1d
    from scipy.signal import find_peaks

    if envelope.size == 0:
        return []
    peak_level = float(envelope.max())
    if peak_level <= 0:
        return []

    # A rolling reference keeps quiet sections and level changes detectable.
    window = max(1, round(window_seconds / seconds_per_bin))
    local_max = maximum_filter1d(envelope, size=window)
    height = np.maximum(local_max * relative_height, peak_level * floor_ratio)
    peaks, _ = find_peaks(
        envelope,
        height=height,
        distance=max(1, round(min_gap_seconds / seconds_per_bin)),
    )

    times: list[float] = []
    for peak in peaks:
        # Time each pulse at its half-height attack, not its decaying peak.
        half = envelope[peak] * 0.5
        idx = int(peak)
        while idx > 0 and envelope[idx - 1] >= half:
            idx -= 1
        times.append(idx * seconds_per_bin)
    return times


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
) -> dict[str, list[float]]:
    """Extract click markers from isolated click audio.

    Hats and kicks are separated with band filters and timed from 1 ms
    envelopes, avoiding a full-resolution spectrogram.

    Returns a mapping with:
    - beat_times: quarter-note beat boundaries (from hat pulses)
    - bar_start_times: bar boundaries (from kick pulses snapped to beat grid)
    - hat_times: detected hat pulse times
    - kick_times: detected kick pulse times
    """
    try:
        import librosa
        from scipy.signal import butter
    except Exception as exc:  # pragma: no cover - depends on optional packages
        raise BeatAnalysisError(
            "Click-track analysis requires librosa and scipy. "
            "Install with: pip install librosa"
        ) from exc

    try:
        y, sr = librosa.load(
            str(audio_path), sr=ANALYSIS_SAMPLE_RATE, mono=True
        )
    except Exception as exc:
        raise BeatAnalysisError(
            f"Failed to load click track audio: {audio_path}"
        ) from exc

    if y.size == 0:
        raise BeatAnalysisError("Click track audio is empty")

    block = max(1, int(sr) // 1000)
    seconds_per_bin = block / float(sr)
    try:
        hat_sos = butter(
            4, CLICK_HAT_HIGHPASS_HZ, btype="highpass", fs=sr, output="sos"
        )
        kick_sos = butter(
            4, CLICK_KICK_LOWPASS_HZ, btype="lowpass", fs=sr, output="sos"
        )
        hat_envelope = _band_envelope(y, hat_sos, block=block)
        # Bridge the gaps between low-frequency cycles in the kick envelope.
        kick_envelope = _band_envelope(
            y, kick_sos, block=block, hold_bins=12
        )
        hat_times = _pick_pulse_times(
            hat_envelope,
            seconds_per_bin=seconds_per_bin,
            min_gap_seconds=CLICK_HAT_MIN_GAP_SECONDS,
        )
        raw_kick_times = _pick_pulse_times(
            kick_envelope,
            seconds_per_bin=seconds_per_bin,
            min_gap_seconds=CLICK_KICK_MIN_GAP_SECONDS,
        )
    except Exception as exc:
        raise BeatAnalysisError(
            "Click-track spectral analysis failed"
        ) from exc

    if len(hat_times) < 8:
        raise BeatAnalysisError(
            "Click-track analysis found too few hat pulses"
        )

    beat_times = list(hat_times)
    kick_times = _snap_to_nearest_grid(
        raw_kick_times, beat_times, max_error_seconds=0.08
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
