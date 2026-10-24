from __future__ import annotations

from pathlib import Path

import pytest

from praecentor.controllers.errors import ControllerError
from praecentor.controllers.validation import validate_song


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_validate_song_returns_event_count_and_emits(tmp_path: Path) -> None:
    song_path = _write(
        tmp_path / "song.yaml",
        "events:\n"
        "  - lyric: Hello\n"
        "  - instruction: Verse\n",
    )
    emitted: list[str] = []

    count = validate_song(song_path, emit=emitted.append)

    assert count == 2
    assert emitted and "2 events" in emitted[0]


def test_validate_song_wraps_song_config_errors(tmp_path: Path) -> None:
    song_path = _write(tmp_path / "invalid.yaml", "- not: a-root-mapping\n")

    with pytest.raises(ControllerError, match="Expected a mapping"):
        validate_song(song_path)


def test_validate_song_rejects_non_list_events(tmp_path: Path) -> None:
    song_path = _write(tmp_path / "invalid.yaml", "events: nope\n")

    with pytest.raises(ControllerError, match="events list"):
        validate_song(song_path)
