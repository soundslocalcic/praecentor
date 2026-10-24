from __future__ import annotations

from pathlib import Path

import pytest

from praecentor.io.song import SongConfigError
from praecentor.io.tap_map import (
    load_beat_tap_map,
    load_tap_map,
    save_tap_map,
)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_load_tap_map_returns_defaults_for_missing_file(tmp_path: Path) -> None:
    data = load_tap_map(tmp_path / "missing.yaml", event_count=3)

    assert data["version"] == 1
    assert data["events"] == []
    assert data["event_map"] == {}


def test_load_tap_map_builds_indexed_event_map(tmp_path: Path) -> None:
    tap_path = _write(
        tmp_path / "tap.yaml",
        "events:\n"
        "  - index: 0\n"
        "    at_seconds: 1.25\n"
        "  - index: 2\n"
        "    at_seconds: 2.50\n",
    )

    data = load_tap_map(tap_path, event_count=4)

    assert data["event_map"] == {0: 1.25, 2: 2.5}


def test_load_tap_map_rejects_duplicate_indexes(tmp_path: Path) -> None:
    tap_path = _write(
        tmp_path / "tap.yaml",
        "events:\n"
        "  - index: 1\n"
        "    at_seconds: 1.0\n"
        "  - index: 1\n"
        "    at_seconds: 2.0\n",
    )

    with pytest.raises(SongConfigError, match="duplicate index"):
        load_tap_map(tap_path, event_count=3)


def test_load_beat_tap_map_rejects_decreasing_time_order(tmp_path: Path) -> None:
    tap_path = _write(
        tmp_path / "beats.yaml",
        "events:\n"
        "  - index: 0\n"
        "    at_seconds: 2.0\n"
        "  - index: 1\n"
        "    at_seconds: 1.0\n",
    )

    with pytest.raises(SongConfigError, match="non-decreasing"):
        load_beat_tap_map(tap_path)


def test_save_tap_map_writes_roundtrippable_yaml(tmp_path: Path) -> None:
    tap_path = tmp_path / "maps/tap.yaml"

    save_tap_map(
        tap_path,
        song_file="song.yaml",
        reference_audio_used="audio.wav",
        event_map={2: 2.1234567, 0: 1.0},
    )
    loaded = load_tap_map(tap_path, event_count=3)

    assert loaded["song_file"] == "song.yaml"
    assert loaded["reference_audio_used"] == "audio.wav"
    assert loaded["event_map"] == {0: 1.0, 2: 2.123457}
