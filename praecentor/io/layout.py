from __future__ import annotations

from pathlib import Path
from typing import Any

from praecentor.io.layout_v1 import load_layout_config as load_v1_layout_config
from praecentor.io.song import SongConfigError

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
    return load_v1_layout_config(layout_path)
