from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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


def test_prevalidate_output_targets_supports_named_track_shape() -> None:
    output_targets = [
        {
            "file": Path("song_multi.mp4"),
            "tracks": [
                {"name": "Session", "stems": ["backing.base"]},
                {"name": "Practice", "stems": ["backing.base", "vocals.*"]},
            ],
        }
    ]
    stem_audio = {
        "track_index": {
            "backing.base": {
                "file_path": Path("Backing.mp3"),
                "gain": 1.0,
            },
            "vocals.a": {"file_path": Path("A.mp3"), "gain": 1.0},
            "vocals.b": {"file_path": Path("B.mp3"), "gain": 1.0},
        }
    }

    rendering._prevalidate_output_targets(
        output_targets=output_targets,
        stem_audio=stem_audio,
    )

    assert output_targets[0]["selected_tracks"] is None
    named_tracks = output_targets[0]["named_tracks"]
    assert isinstance(named_tracks, list)
    assert [track["name"] for track in named_tracks] == [
        "Session",
        "Practice",
    ]
    assert len(named_tracks[0]["selected_tracks"]) == 1
    assert len(named_tracks[1]["selected_tracks"]) == 3


def test_prevalidate_output_targets_keeps_legacy_selector_shape() -> None:
    output_targets = [
        {
            "file": Path("song_single.mp4"),
            "tracks": ["backing.base", "vocals.*"],
        }
    ]
    stem_audio = {
        "track_index": {
            "backing.base": {
                "file_path": Path("Backing.mp3"),
                "gain": 1.0,
            },
            "vocals.a": {"file_path": Path("A.mp3"), "gain": 1.0},
        }
    }

    rendering._prevalidate_output_targets(
        output_targets=output_targets,
        stem_audio=stem_audio,
    )

    assert isinstance(output_targets[0]["selected_tracks"], list)
    assert output_targets[0]["named_tracks"] is None


def test_run_mux_for_target_named_tracks_writes_multi_audio_maps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_cmd: list[str] = []

    def _fake_run(cmd, stderr=None):
        captured_cmd.extend(str(part) for part in cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(rendering.subprocess, "run", _fake_run)
    monkeypatch.setattr(
        rendering,
        "_peak_normalization_gain_db",
        lambda **_kwargs: None,
    )

    target = {
        "file": Path("song_multi.mp4"),
        "named_tracks": [
            {
                "name": "Session",
                "selected_tracks": [
                    {
                        "file_path": Path("Backing.mp3"),
                        "gain": 1.0,
                    }
                ],
            },
            {
                "name": "Practice",
                "selected_tracks": [
                    {
                        "file_path": Path("Backing.mp3"),
                        "gain": 1.0,
                    },
                    {
                        "file_path": Path("VocalA.mp3"),
                        "gain": 1.0,
                    },
                ],
            },
        ],
    }

    _, _, success, _, _ = rendering._run_mux_for_target(
        target_idx=1,
        target=target,
        mux_video_source_path=Path("silent.mp4"),
        ffmpeg_path="ffmpeg",
        ffmpeg_audio_seek_seconds=0.0,
        audio_path=Path("song.wav"),
        stem_audio={"track_index": {}},
        audio_delay_ms=500,
        target_output_duration=42.0,
    )

    assert success is True
    assert "-map" in captured_cmd
    assert "[a0]" in captured_cmd
    assert "[a1]" in captured_cmd
    assert "-disposition:a:0" in captured_cmd
    assert "default" in captured_cmd
    assert "-disposition:a:1" in captured_cmd
    assert "-ar:a" in captured_cmd
    assert "48000" in captured_cmd
    assert "-metadata:s:a:0" in captured_cmd
    assert "title=Session" in captured_cmd
    assert "-metadata:s:a:1" in captured_cmd
    assert "title=Practice" in captured_cmd
