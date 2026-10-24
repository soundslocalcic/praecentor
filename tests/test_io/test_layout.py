from __future__ import annotations

from pathlib import Path

import pytest

from praecentor.io import layout
from praecentor.io.song import SongConfigError


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_resolve_layout_path_returns_explicit_path(tmp_path: Path) -> None:
    layout_path = _write(tmp_path / "layout.yaml", "title: {}\n")

    resolved = layout.resolve_layout_path(layout_path)

    assert resolved == layout_path.resolve()


def test_resolve_layout_path_raises_when_default_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        layout,
        "DEFAULT_LAYOUT_RELATIVE",
        Path("does/not/exist/default.yaml"),
    )

    with pytest.raises(SongConfigError, match="default layout"):
        layout.resolve_layout_path(None)


def test_load_layout_config_rejects_non_mapping_title(tmp_path: Path) -> None:
    layout_path = _write(tmp_path / "layout.yaml", "title: text\n")

    with pytest.raises(SongConfigError, match="layout.title"):
        layout.load_layout_config(layout_path)
