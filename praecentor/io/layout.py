from __future__ import annotations

from pathlib import Path
from typing import Any

from praecentor.io.song import SongConfigError, load_yaml_file

DEFAULT_LAYOUT_RELATIVE = Path("examples/layouts/default.yaml")


def resolve_layout_path(layout_file: Path | None) -> Path:
    if layout_file is not None:
        return layout_file.resolve()

    default_path = DEFAULT_LAYOUT_RELATIVE.resolve()
    if not default_path.exists():
        raise SongConfigError(
            "No layout file provided and default layout was not found at "
            f"{DEFAULT_LAYOUT_RELATIVE}"
        )

    return default_path


def load_layout_config(layout_path: Path) -> dict[str, Any]:
    data = load_yaml_file(layout_path)

    title = data.get("title")
    if title is not None and not isinstance(title, dict):
        raise SongConfigError("layout.title must be a mapping when provided")

    return data
