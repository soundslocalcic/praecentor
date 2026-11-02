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
