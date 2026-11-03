"""Original layout loading semantics for explicit v1 rendering."""

from pathlib import Path
from typing import Any

from praecentor.io.song import SongConfigError, load_yaml_file


def load_layout_config(layout_path: Path) -> dict[str, Any]:
    data = load_yaml_file(layout_path)

    title = data.get("title")
    if title is not None and not isinstance(title, dict):
        raise SongConfigError("layout.title must be a mapping when provided")

    return data
