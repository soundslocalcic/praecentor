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


def test_layout_version_is_explicit_and_does_not_guess_from_shape() -> None:
    assert layout.layout_version({}) == 1
    assert layout.layout_version({"version": 1}) == 1
    assert layout.layout_version({"version": 2}) == 2
    assert layout.layout_version({"song": {"elements": {}}}) == 1


@pytest.mark.parametrize("version", [None, True, False, 0, 3, 1.0, "1", "2", [], {}])
def test_layout_version_rejects_unsupported_values(version) -> None:
    with pytest.raises(SongConfigError, match="layout.version"):
        layout.layout_version({"version": version})


def test_v2_loader_validates_and_preserves_input_defaults(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "v2.yaml",
        "version: 2\nsong:\n  elements:\n    chord_main:\n"
        "      x: 54\n      y: 788\n      width: 1498\n      height: 259\n",
    )
    config = layout.load_layout_config(path)
    assert config["version"] == 2
    assert config["song"]["elements"]["chord_main"]["x"] == 54
    assert config["song"]["elements"]["chord_main"]["style"]["size_px"] == 76


@pytest.mark.parametrize("text", [
    "version: 2\nsong:\n  elements:\n    chord_main: {}\n    chord_main: {}\n",
    "version: 2\nbackground:\n  color: '#000'\n  color: '#FFF'\n",
    "version: 2\nversion: 1\n",
])
def test_loader_rejects_duplicate_v2_keys_and_ambiguous_versions(tmp_path: Path, text: str) -> None:
    with pytest.raises(SongConfigError, match="Duplicate layout YAML key"):
        layout.load_layout_config(_write(tmp_path / "duplicate.yaml", text))


def test_loader_keeps_legacy_nested_duplicate_semantics(tmp_path: Path) -> None:
    path = _write(tmp_path / "legacy.yaml", "version: 1\ntitle:\n  enabled: true\n  enabled: false\n")
    assert layout.load_layout_config(path)["title"]["enabled"] is False


def test_loader_rejects_unsupported_version_and_legacy_shaped_v2(tmp_path: Path) -> None:
    with pytest.raises(SongConfigError, match="layout.version"):
        layout.load_layout_config(_write(tmp_path / "unknown.yaml", "version: 3\n"))
    with pytest.raises(SongConfigError, match="unsupported field 'regions'"):
        layout.load_layout_config(_write(tmp_path / "invalid-v2.yaml", "version: 2\nregions: {}\n"))
