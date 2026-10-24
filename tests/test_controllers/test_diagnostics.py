from __future__ import annotations

from pathlib import Path

import pytest

from praecentor.controllers import diagnostics
from praecentor.controllers.errors import ControllerError
from praecentor.io.song import SongConfigError


def _song_file(tmp_path: Path) -> Path:
    path = tmp_path / "song.yaml"
    path.write_text("version: 1\n", encoding="utf-8")
    return path


def test_parse_duration_seconds_supports_number_and_timecodes() -> None:
    assert diagnostics._parse_duration_seconds(1.25) == 1.25
    assert diagnostics._parse_duration_seconds("1:02.5") == 62.5
    assert diagnostics._parse_duration_seconds("1:02:03") == 3723.0


@pytest.mark.parametrize("raw", [None, "", "1:2:3:4", object()])
def test_parse_duration_seconds_rejects_invalid_values(raw: object) -> None:
    with pytest.raises(ValueError):
        diagnostics._parse_duration_seconds(raw)


def test_positive_intervals_filters_non_positive_deltas() -> None:
    values = [1.0, 1.5, 1.5, 1.2, 2.0]

    assert diagnostics._positive_intervals(values) == [0.5, 0.8]


def test_infer_start_bpm_hint_returns_only_positive_numeric_values() -> None:
    assert diagnostics._infer_start_bpm_hint({"bpm": 96}) == 96.0
    assert diagnostics._infer_start_bpm_hint({"bpm": 0}) is None
    assert diagnostics._infer_start_bpm_hint({"bpm": "96"}) is None


def test_diagnose_beats_emits_summary_and_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_file(tmp_path)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        diagnostics,
        "load_yaml_file",
        lambda _path: {
            "audio": {
                "file": "audio.wav",
                "start_offset_seconds": "0.5",
            },
            "timing": {
                "beats": {
                    "bpm": 120,
                    "auto_dynamic_tempo": True,
                    "auto_dynamic_tempo_smoothing": 3,
                    "auto_dynamic_tempo_sensitivity": 0.6,
                }
            },
        },
    )

    seen: dict[str, object] = {}

    def fake_analyze(audio_file: Path, **kwargs):
        seen["audio_file"] = audio_file
        seen["kwargs"] = kwargs
        return [1.0, 1.5, 2.0, 2.5]

    monkeypatch.setattr(diagnostics, "analyze_beats_from_audio", fake_analyze)
    monkeypatch.setattr(
        diagnostics,
        "infer_beat_seconds_from_beats",
        lambda beats: 0.5,
    )

    emitted: list[str] = []
    diagnostics.diagnose_beats(
        song_file,
        start_bpm=None,
        dynamic_tempo="auto",
        dynamic_tempo_smoothing=None,
        dynamic_tempo_sensitivity=None,
        apply_audio_offset=True,
        show_beats=2,
        all_beats=False,
        emit=emitted.append,
    )

    assert seen["audio_file"] == audio_path.resolve()
    kwargs = seen["kwargs"]
    assert kwargs["start_bpm"] == 120.0
    assert kwargs["dynamic_tempo"] is True
    assert kwargs["dynamic_tempo_smoothing"] == 3
    assert kwargs["dynamic_tempo_sensitivity"] == 0.6

    assert any("detected_beat_count=4" in line for line in emitted)
    assert any("analysis_start_bpm_hint=120.000000" in line for line in emitted)
    assert any("inferred_beat_seconds=0.500000" in line for line in emitted)
    assert any("beat_rows_truncated=2" in line for line in emitted)


def test_diagnose_beats_all_beats_and_inference_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_file(tmp_path)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        diagnostics,
        "load_yaml_file",
        lambda _path: {
            "audio": {"file": "audio.wav", "start_offset_seconds": 0},
            "timing": {"beats": {}},
        },
    )
    monkeypatch.setattr(
        diagnostics,
        "analyze_beats_from_audio",
        lambda *_args, **_kwargs: [0.1, 0.3],
    )

    def _raise(_beats: list[float]) -> float:
        raise diagnostics.BeatAnalysisError("no inference")

    monkeypatch.setattr(diagnostics, "infer_beat_seconds_from_beats", _raise)

    emitted: list[str] = []
    diagnostics.diagnose_beats(
        song_file,
        start_bpm=90.0,
        dynamic_tempo="off",
        dynamic_tempo_smoothing=5,
        dynamic_tempo_sensitivity=0.3,
        apply_audio_offset=False,
        show_beats=1,
        all_beats=True,
        emit=emitted.append,
    )

    assert any("analysis_dynamic_tempo=False" in line for line in emitted)
    assert any("analysis_start_bpm_hint=90.000000" in line for line in emitted)
    assert any("inferred_beat_seconds=unavailable" in line for line in emitted)
    assert not any("beat_rows_truncated=" in line for line in emitted)


def test_diagnose_beats_wraps_song_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_file(tmp_path)

    def _raise(_path: Path):
        raise SongConfigError("bad song")

    monkeypatch.setattr(diagnostics, "load_yaml_file", _raise)

    with pytest.raises(ControllerError, match="bad song"):
        diagnostics.diagnose_beats(
            song_file,
            start_bpm=None,
            dynamic_tempo="auto",
            dynamic_tempo_smoothing=None,
            dynamic_tempo_sensitivity=None,
            apply_audio_offset=True,
            show_beats=10,
            all_beats=False,
        )


def test_diagnose_beats_requires_audio_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_file(tmp_path)
    monkeypatch.setattr(diagnostics, "load_yaml_file", lambda _path: {"audio": {}})

    with pytest.raises(ControllerError, match="audio.file is required"):
        diagnostics.diagnose_beats(
            song_file,
            start_bpm=None,
            dynamic_tempo="auto",
            dynamic_tempo_smoothing=None,
            dynamic_tempo_sensitivity=None,
            apply_audio_offset=True,
            show_beats=10,
            all_beats=False,
        )


def test_diagnose_beats_rejects_missing_audio_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_file(tmp_path)
    monkeypatch.setattr(
        diagnostics,
        "load_yaml_file",
        lambda _path: {"audio": {"file": "missing.wav"}},
    )

    with pytest.raises(ControllerError, match="Audio file not found"):
        diagnostics.diagnose_beats(
            song_file,
            start_bpm=None,
            dynamic_tempo="auto",
            dynamic_tempo_smoothing=None,
            dynamic_tempo_sensitivity=None,
            apply_audio_offset=True,
            show_beats=10,
            all_beats=False,
        )


def test_diagnose_beats_rejects_invalid_smoothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_file(tmp_path)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        diagnostics,
        "load_yaml_file",
        lambda _path: {
            "audio": {"file": "audio.wav"},
            "timing": {"beats": {"auto_dynamic_tempo_smoothing": 0}},
        },
    )

    with pytest.raises(ControllerError, match="smoothing"):
        diagnostics.diagnose_beats(
            song_file,
            start_bpm=None,
            dynamic_tempo="auto",
            dynamic_tempo_smoothing=None,
            dynamic_tempo_sensitivity=None,
            apply_audio_offset=True,
            show_beats=10,
            all_beats=False,
        )


def test_diagnose_beats_rejects_non_numeric_smoothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_file(tmp_path)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        diagnostics,
        "load_yaml_file",
        lambda _path: {
            "audio": {"file": "audio.wav"},
            "timing": {"beats": {"auto_dynamic_tempo_smoothing": "abc"}},
        },
    )

    with pytest.raises(ControllerError, match="smoothing"):
        diagnostics.diagnose_beats(
            song_file,
            start_bpm=None,
            dynamic_tempo="auto",
            dynamic_tempo_smoothing=None,
            dynamic_tempo_sensitivity=None,
            apply_audio_offset=True,
            show_beats=10,
            all_beats=False,
        )


def test_diagnose_beats_rejects_invalid_sensitivity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_file(tmp_path)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        diagnostics,
        "load_yaml_file",
        lambda _path: {
            "audio": {"file": "audio.wav"},
            "timing": {"beats": {"auto_dynamic_tempo_sensitivity": 1.5}},
        },
    )

    with pytest.raises(ControllerError, match="sensitivity"):
        diagnostics.diagnose_beats(
            song_file,
            start_bpm=None,
            dynamic_tempo="auto",
            dynamic_tempo_smoothing=None,
            dynamic_tempo_sensitivity=None,
            apply_audio_offset=True,
            show_beats=10,
            all_beats=False,
        )


def test_diagnose_beats_rejects_non_numeric_sensitivity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_file(tmp_path)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        diagnostics,
        "load_yaml_file",
        lambda _path: {
            "audio": {"file": "audio.wav"},
            "timing": {
                "beats": {"auto_dynamic_tempo_sensitivity": "high"}
            },
        },
    )

    with pytest.raises(ControllerError, match="sensitivity"):
        diagnostics.diagnose_beats(
            song_file,
            start_bpm=None,
            dynamic_tempo="auto",
            dynamic_tempo_smoothing=None,
            dynamic_tempo_sensitivity=None,
            apply_audio_offset=True,
            show_beats=10,
            all_beats=False,
        )


def test_diagnose_beats_rejects_invalid_audio_offset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_file(tmp_path)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        diagnostics,
        "load_yaml_file",
        lambda _path: {
            "audio": {"file": "audio.wav", "start_offset_seconds": "oops"},
            "timing": {"beats": {}},
        },
    )

    with pytest.raises(ControllerError, match="audio.start_offset_seconds"):
        diagnostics.diagnose_beats(
            song_file,
            start_bpm=None,
            dynamic_tempo="auto",
            dynamic_tempo_smoothing=None,
            dynamic_tempo_sensitivity=None,
            apply_audio_offset=True,
            show_beats=10,
            all_beats=False,
        )


def test_diagnose_beats_rejects_negative_audio_offset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_file(tmp_path)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        diagnostics,
        "load_yaml_file",
        lambda _path: {
            "audio": {"file": "audio.wav", "start_offset_seconds": -0.1},
            "timing": {"beats": {}},
        },
    )

    with pytest.raises(ControllerError, match="must be >= 0"):
        diagnostics.diagnose_beats(
            song_file,
            start_bpm=None,
            dynamic_tempo="auto",
            dynamic_tempo_smoothing=None,
            dynamic_tempo_sensitivity=None,
            apply_audio_offset=True,
            show_beats=10,
            all_beats=False,
        )


def test_diagnose_beats_wraps_analysis_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_file(tmp_path)
    audio_path = tmp_path / "audio.wav"
    audio_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        diagnostics,
        "load_yaml_file",
        lambda _path: {
            "audio": {"file": "audio.wav", "start_offset_seconds": 0.0},
            "timing": {"beats": {}},
        },
    )

    def _raise(*_args, **_kwargs):
        raise diagnostics.BeatAnalysisError("analysis failed")

    monkeypatch.setattr(diagnostics, "analyze_beats_from_audio", _raise)

    with pytest.raises(ControllerError, match="analysis failed"):
        diagnostics.diagnose_beats(
            song_file,
            start_bpm=None,
            dynamic_tempo="auto",
            dynamic_tempo_smoothing=None,
            dynamic_tempo_sensitivity=None,
            apply_audio_offset=True,
            show_beats=10,
            all_beats=False,
        )
