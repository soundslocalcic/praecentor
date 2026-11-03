"""Version-aware layout rendering entry points for controllers and Python callers."""

from __future__ import annotations

from typing import Any

from praecentor.io.layout import layout_version, resolve_layout_config
from praecentor.io.song import SongConfigError
from praecentor.render import v1, v2


def ensure_rendering_supported(layout_data: dict[str, Any]) -> None:
    config = resolve_layout_config(layout_data)
    if layout_version(config) == 2:
        v2.ensure_rendering_supported()


def render_cue_frames(*, layout_data: dict[str, Any], **kwargs):
    config = resolve_layout_config(layout_data)
    renderer = v1 if layout_version(config) == 1 else v2
    return renderer.render_cue_frames(layout_data=config, **kwargs)


def render_title_countdown_frames(*, layout_data=None, title_layout=None, **kwargs):
    config = resolve_layout_config(layout_data if layout_data is not None else {})
    if layout_version(config) == 1:
        title = title_layout if title_layout is not None else config.get("title") or {}
        return v1.render_title_countdown_frames(title_layout=title, **kwargs)
    if title_layout is not None:
        raise SongConfigError("v2 title rendering requires the full layout_data, not title_layout")
    return v2.render_title_countdown_frames(layout_data=config, **kwargs)


def build_background_layer_renderer(*, layout_data=None, **kwargs):
    config = resolve_layout_config(layout_data if layout_data is not None else {})
    if layout_version(config) == 1:
        return v1.build_background_layer_renderer(**kwargs)
    return v2.build_background_layer_renderer(layout_data=config, **kwargs)
