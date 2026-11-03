from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from praecentor.audio.beat_analysis import (
    ANALYSIS_SAMPLE_RATE,
    DEFAULT_ANALYSIS_HOP_LENGTH,
    BeatAnalysisError,
    analyze_beats_from_audio,
    analyze_click_track_grid_from_audio,
)


class _FrameTimes(list):
    def tolist(self) -> list[float]:
        return list(self)


def test_analyze_beats_uses_resampled_audio_and_default_hop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}

    def load(_filename: str, *, sr: int, mono: bool) -> tuple[list[float], int]:
        calls["load"] = (sr, mono)
        return [0.0], sr

    def beat_track(**kwargs: object) -> tuple[int, list[int]]:
        calls["beat_track"] = kwargs
        return 120, [0, 1]

    def frames_to_time(
        frames: list[int], *, sr: int, hop_length: int
    ) -> _FrameTimes:
        return _FrameTimes(
            [frame * hop_length / sr for frame in frames]
        )

    monkeypatch.setitem(
        sys.modules,
        "librosa",
        SimpleNamespace(
            load=load,
            beat=SimpleNamespace(beat_track=beat_track),
            frames_to_time=frames_to_time,
        ),
    )

    beats = analyze_beats_from_audio(Path("click.wav"))

    assert calls["load"] == (ANALYSIS_SAMPLE_RATE, True)
    assert calls["beat_track"]["hop_length"] == DEFAULT_ANALYSIS_HOP_LENGTH
    assert beats == [0.0, DEFAULT_ANALYSIS_HOP_LENGTH / ANALYSIS_SAMPLE_RATE]


def test_click_grid_resamples_audio_before_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, bool]] = []

    def load(_filename: str, *, sr: int, mono: bool) -> tuple[object, int]:
        calls.append((sr, mono))
        return SimpleNamespace(size=0), sr

    monkeypatch.setitem(sys.modules, "librosa", SimpleNamespace(load=load))

    with pytest.raises(BeatAnalysisError, match="audio is empty"):
        analyze_click_track_grid_from_audio(Path("click.wav"))

    assert calls == [(ANALYSIS_SAMPLE_RATE, True)]


def _synthetic_808_click(
    beat_times: list[float], *, beats_per_bar: int, sr: int
):
    np = pytest.importorskip("numpy")
    rng = np.random.default_rng(808)
    y = np.zeros(int((beat_times[-1] + 1.0) * sr), dtype=np.float32)
    hat_t = np.arange(int(0.04 * sr)) / sr
    hat = rng.uniform(-1, 1, hat_t.size) * np.exp(-hat_t / 0.008) * 0.4
    hat -= np.convolve(hat, np.ones(4) / 4, mode="same")
    kick_t = np.arange(int(0.5 * sr)) / sr
    kick = np.sin(2 * np.pi * 55 * kick_t) * np.exp(-kick_t / 0.18) * 0.8
    for idx, start in enumerate(beat_times):
        at = round(start * sr)
        y[at: at + hat.size] += hat
        if idx % beats_per_bar == 0:
            y[at: at + kick.size] += kick[: y.size - at]
    return y


def test_click_grid_keeps_whole_bars_through_tempo_ramp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("scipy")
    beats_per_bar = 4
    beat_times = [0.25]
    while len(beat_times) < 96:
        progress = len(beat_times) / 96
        bpm = 90 + (170 - 90) * progress
        beat_times.append(beat_times[-1] + 60.0 / bpm)

    def load(_filename: str, *, sr: int, mono: bool) -> tuple[object, int]:
        return (
            _synthetic_808_click(
                beat_times, beats_per_bar=beats_per_bar, sr=sr
            ),
            sr,
        )

    monkeypatch.setitem(sys.modules, "librosa", SimpleNamespace(load=load))

    grid = analyze_click_track_grid_from_audio(Path("click.wav"))

    beats = grid["beat_times"]
    assert len(beats) == len(beat_times)
    assert max(abs(a - b) for a, b in zip(beats, beat_times)) < 0.003
    bar_beat_indices = [beats.index(t) for t in grid["bar_start_times"]]
    assert bar_beat_indices == list(range(0, len(beat_times), beats_per_bar))
