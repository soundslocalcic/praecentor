from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from praecentor.io.layout_v1 import load_layout_config as load_v1_layout_config
from praecentor.io.layout_v2 import validate_layout_config as validate_v2_layout_config
from praecentor.io.song import SongConfigError

DEFAULT_LAYOUT_RELATIVE = Path("examples/layouts/default.yaml")
DEFAULT_LAYOUT_VERSION = 1


def layout_version(data: dict[str, Any]) -> int:
    if not isinstance(data, dict):
        raise SongConfigError("layout must be a mapping")
    version = data.get("version", DEFAULT_LAYOUT_VERSION)
    if type(version) is not int or version not in (1, 2):
        raise SongConfigError("layout.version must be the integer 1 or 2")
    return version


class _UniqueLayoutLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _value_node in node.value:
            key = self.construct_object(key_node, deep=True)
            if not isinstance(key, str):
                raise SongConfigError("v2 layout YAML keys must be strings")
            if key in seen:
                raise SongConfigError(f"Duplicate layout YAML key {key!r} at line {key_node.start_mark.line + 1}")
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def resolve_layout_config(data: dict[str, Any]) -> dict[str, Any]:
    if layout_version(data) == 2:
        return validate_v2_layout_config(data)
    title = data.get("title")
    if title is not None and not isinstance(title, dict):
        raise SongConfigError("layout.title must be a mapping when provided")
    return deepcopy(data)


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


def load_layout_overrides(layout_path: Path, *, version: int | None = None) -> dict[str, Any]:
    text = layout_path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
        if data is None:
            data = {}
        declared = layout_version(data)
        selected = declared if version is None else layout_version({"version": version})
        if version is not None and "version" in data and declared != selected:
            raise SongConfigError("Explicit layout version conflicts with the file's version")
        root = yaml.compose(text, Loader=yaml.SafeLoader)
        if isinstance(root, yaml.MappingNode):
            versions = [key for key, _value in root.value if key.value == "version"]
            if len(versions) > 1:
                raise SongConfigError("Duplicate layout YAML key 'version'")
        if selected == 2:
            data = yaml.load(text, Loader=_UniqueLayoutLoader) or {}
        if version is not None:
            data["version"] = selected
        resolve_layout_config(data)
        return deepcopy(data)
    except yaml.YAMLError as exc:
        raise SongConfigError(f"Invalid layout YAML in {layout_path}: {exc}") from exc


def load_layout_config(layout_path: Path) -> dict[str, Any]:
    data = load_layout_overrides(layout_path)
    if layout_version(data) == 1:
        return load_v1_layout_config(layout_path)
    return resolve_layout_config(data)
