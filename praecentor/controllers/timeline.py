from __future__ import annotations

from pathlib import Path
from typing import Callable


def inspect_timeline(
    song_file: Path, *, emit: Callable[[str], None] | None = None
) -> None:
    emit = emit or (lambda _line: None)
    emit(f"Timeline command scaffold for: {song_file}")
