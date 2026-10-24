from __future__ import annotations

from pathlib import Path
from typing import Callable

from praecentor.controllers.errors import ControllerError
from praecentor.io.song import SongConfigError, load_yaml_file


def validate_song(
    song_file: Path, *, emit: Callable[[str], None] | None = None
) -> int:
    emit = emit or (lambda _line: None)

    try:
        data = load_yaml_file(song_file)
    except SongConfigError as exc:
        raise ControllerError(str(exc)) from exc

    events = data.get("events")
    if not isinstance(events, list):
        raise ControllerError("Song file must contain an events list")

    emit(f"OK: {song_file} ({len(events)} events)")
    return len(events)
