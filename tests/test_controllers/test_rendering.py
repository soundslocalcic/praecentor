from __future__ import annotations

from pathlib import Path

import pytest

from praecentor.controllers import rendering
from praecentor.controllers.errors import ControllerError


def test_lyric_helpers_strip_markers_and_track_lengths() -> None:
    raw_lyric = "[a] Hello\n  still here\n[b] Later"

    assert rendering._visible_lyric_lines(raw_lyric) == [
        "Hello",
        "  still here",
        "Later",
    ]
    assert rendering._lyric_line_lengths_for_validation(raw_lyric) == [
        ("Hello", 5),
        ("  still here", 12),
        ("Later", 5),
    ]


def test_beat_time_math_tracks_time_and_index_state() -> None:
    beat_times = [0.0, 1.0, 2.0, 3.0]

    assert rendering._median_positive_delta(beat_times) == 1.0
    assert rendering._smooth_beat_times(beat_times, window=3) == beat_times
    aligned, shift = rendering._normalize_click_track_beats_to_audio_start(
        [0.2, 1.1, 2.1, 3.1]
    )
    assert shift == 0
    assert aligned[0] == 0.0
    assert aligned[-1] < 3.1

    beat_index, remaining_ratio = rendering._beat_state_at_time(
        1.25,
        beat_seconds=1.0,
        beat_times_seconds=[0.0, 1.0, 2.0],
        beat_anchor_index=0,
    )
    assert (beat_index, remaining_ratio) == (1, 0.75)
    assert rendering._time_at_raw_beat_index(
        2,
        beat_seconds=1.0,
        beat_times_seconds=[0.0, 1.0, 2.0],
    ) == 2.0
    assert rendering._beat_position_at_time(
        1.25,
        beat_seconds=1.0,
        beat_times_seconds=[0.0, 1.0, 2.0],
    ) == 1.25


def test_event_meter_overrides_carry_forward_between_events() -> None:
    events = [
        {"lyric": "Verse"},
        {"time_signature": "3/4", "lyric": "Bridge"},
        {"lyric": "Outro"},
    ]

    resolved = rendering._event_beats_per_bar_with_overrides(
        song_events=events,
        inferred_event_beats_per_bar=[4, 4, 4],
        fallback_beats_per_bar=4,
    )

    assert resolved == [4, 3, 3]


def test_render_rejects_missing_timing_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_path = tmp_path / "song.yaml"
    song_path.write_text("events: []\n", encoding="utf-8")

    monkeypatch.setattr(
        rendering,
        "_layout_path_from_song_config",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        rendering,
        "resolve_layout_path",
        lambda _path: Path("layout.yaml"),
    )
    monkeypatch.setattr(rendering, "load_yaml_file", lambda _path: {"events": []})
    monkeypatch.setattr(rendering, "load_layout_config", lambda _path: {})

    with pytest.raises(ControllerError, match="missing timing mapping"):
        rendering.render(song_path)


def test_validate_uniform_track_durations_rejects_mismatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracks = [
        {"group": "backing", "name": "a", "file_path": Path("a.wav")},
        {"group": "backing", "name": "b", "file_path": Path("b.wav")},
    ]

    monkeypatch.setattr(
        rendering,
        "_probe_audio_duration_seconds",
        lambda path, _ffmpeg_path: 12.0 if path.name == "a.wav" else 13.0,
    )

    with pytest.raises(
        ControllerError, match="Audio stem lengths do not match"
    ):
        rendering._validate_uniform_track_durations(
            tracks=tracks,
            ffmpeg_path="ffmpeg",
        )
