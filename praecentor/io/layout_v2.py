"""Strict, immutable v2 layout validation on a fixed pixel canvas."""

from __future__ import annotations

from copy import deepcopy
import math
import re
from pathlib import Path
from typing import Any

import yaml

from praecentor.io.song import SongConfigError

CANVAS_WIDTH = 1920
CANVAS_HEIGHT = 1080
CANVAS_REM_PX = 94
DEFAULT_LAYOUT_PATH = Path(__file__).resolve().parents[1] / "assets" / "layouts" / "default-v2.yaml"
TITLE_ELEMENTS = ("title_text", "metadata_text", "countdown_text", "first_chord_preview", "beat_indicator")
SONG_ELEMENTS = ("lyric_main", "chord_main", "next_chord", "beat_indicator", "riff")
TEXT_SIZES = {
    "title_text": 78, "metadata_text": 42, "countdown_text": 72,
    "first_chord_preview": 48, "lyric_main": 94, "chord_main": 76, "next_chord": 64,
}
TEXT_STYLE = {
    "font_family": "Inter", "weight": 700, "color": "#FFFFFF",
    "align": "center", "valign": "center", "letter_spacing_em": 0.0, "line_height": 1.0,
}
RING = {
    "enabled": True, "color": "#FF9F1C", "width_px": 6, "padding_px": 0,
    "dotted": True, "dash_degrees": 5.5, "gap_degrees": 4.5,
}
APPEARANCE = {
    "next_chord": {
        "ring": RING,
        "warning": {
            "enabled": True, "color": "#FF9F1C", "icon_scale": 1.8,
            "gap_px": 16, "flash_hz": 2.5, "flash_duty_cycle": 0.5,
        },
        "loop": {"icon_scale": 3.0, "y_offset_px": -5},
    },
    "first_chord_preview": {
        "padding_px": 18,
        "ring": {**RING, "color": "#E8E8E8", "width_px": 3},
    },
    "beat_indicator": {
        "fill_color": "#72E676", "pulse_first_color": "#72E676",
        "pulse_other_color": "#3B82F6", "pulse_expand_px": 28, "pulse_width_px": 5,
    },
    "riff": {
        "viewport_beats": 8.0, "center_fraction": 0.5, "pre_roll_beats": 0.0,
        "post_roll_beats": 0.0, "lane_gap_px": 12, "note_side_margin_px": 2,
        "note_height_ratio": 0.58, "lane_color": "#2A2A2A", "note_color": "#F5F5F5",
        "active_note_color": "#19FF19", "playhead_color": "#FF8000", "playhead_width_px": 4,
    },
}
BEHAVIOR = {
    "metadata_text": {"show_artist": True, "show_key": True, "show_hot_start": True},
    "lyric_main": {
        "scroll_preview_beats": 3.0, "preview_opacity": 0.5, "outgoing_opacity": 0.0,
        "preview_margin_px": None, "event_margin_em": None, "event_margin_rem": 1.5,
        "entrance_beats": 0.0, "exit_beats": 1.0, "edge_fade_px": 282,
    },
    "next_chord": {"lookahead_beats": 4.0},
    "first_chord_preview": {"slide_beats": 1.0, "slide_lead_beats": 1.0},
}
POSITIVE_SETTINGS = {
    "size_px", "line_height", "viewport_beats", "note_height_ratio", "icon_scale",
    "dash_degrees", "scroll_preview_beats", "slide_beats", "lookahead_beats",
}
UNIT_SETTINGS = {"center_fraction", "note_height_ratio", "preview_opacity", "outgoing_opacity", "flash_duty_cycle"}


def _mapping(value: Any, path: str, allowed) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SongConfigError(f"{path} must be a mapping")
    for key in value:
        if not isinstance(key, str) or key not in allowed:
            raise SongConfigError(f"{path} contains unsupported field {key!r}")
    return value


def _number(value: Any, path: str, *, minimum=None, maximum=None, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SongConfigError(f"{path} must be a finite number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise SongConfigError(f"{path} must be a finite number")
    if positive and value <= 0:
        raise SongConfigError(f"{path} must be greater than zero")
    if minimum is not None and value < minimum:
        raise SongConfigError(f"{path} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise SongConfigError(f"{path} must be at most {maximum}")
    return value


def _settings(value: Any, defaults: dict[str, Any], path: str) -> dict[str, Any]:
    raw = _mapping(value, path, defaults)
    result = deepcopy(defaults)
    for key, setting in raw.items():
        target = f"{path}.{key}"
        default = defaults[key]
        if key in ("preview_margin_px", "event_margin_em"):
            result[key] = None if setting is None else _number(setting, target, minimum=0)
        elif isinstance(default, dict):
            result[key] = _settings(setting, default, target)
        elif isinstance(default, bool):
            if not isinstance(setting, bool):
                raise SongConfigError(f"{target} must be a boolean")
            result[key] = setting
        elif isinstance(default, (int, float)):
            minimum = -1.0 if key == "letter_spacing_em" else None if key == "y_offset_px" else 0
            maximum = 1.0 if key in UNIT_SETTINGS or key == "letter_spacing_em" else None
            result[key] = _number(setting, target, minimum=minimum, maximum=maximum, positive=key in POSITIVE_SETTINGS)
            if key == "weight" and (type(setting) is not int or setting not in (300, 400, 500, 600, 700, 800)):
                raise SongConfigError(f"{target} must be one of 300, 400, 500, 600, 700, 800")
        elif key == "font_family":
            if setting != "Inter":
                raise SongConfigError(f"{target} must be Inter")
            result[key] = setting
        elif key in ("align", "valign"):
            choices = ("left", "center", "right") if key == "align" else ("top", "center", "bottom")
            if not isinstance(setting, str) or setting not in choices:
                raise SongConfigError(f"{target} must be one of {', '.join(choices)}")
            result[key] = setting
        else:
            if not isinstance(setting, str) or not re.fullmatch(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})", setting):
                raise SongConfigError(f"{target} must be a #RGB or #RRGGBB colour")
            result[key] = setting
    return result


def _element(value: Any, name: str, path: str) -> dict[str, Any]:
    allowed = {"x", "y", "width", "height", "enabled"}
    if name in TEXT_SIZES:
        allowed.add("style")
    if name in APPEARANCE:
        allowed.add("appearance")
    if name in BEHAVIOR:
        allowed.add("behavior")
    raw = _mapping(value, path, allowed)
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise SongConfigError(f"{path}.enabled must be a boolean")
    result = {"enabled": enabled}
    for key in ("x", "y", "width", "height"):
        if key not in raw:
            raise SongConfigError(f"{path}.{key} is required in pixels")
        result[key] = _number(raw[key], f"{path}.{key}", positive=key in ("width", "height"))
    if name in TEXT_SIZES:
        defaults = {**TEXT_STYLE, "size_px": TEXT_SIZES[name]}
        if name == "title_text":
            defaults["weight"] = 800
        if name == "metadata_text":
            defaults["color"] = "#D9D9D9"
        if name == "next_chord":
            defaults["color"] = "#FF9F1C"
        if name == "lyric_main":
            defaults.update(split_a_color="#ADFFDA", split_b_color="#F7E6D8", split_c_color="#E8DFF7")
        result["style"] = _settings(raw.get("style", {}), defaults, f"{path}.style")
    if name in APPEARANCE:
        result["appearance"] = _settings(raw.get("appearance", {}), APPEARANCE[name], f"{path}.appearance")
    if name in BEHAVIOR:
        result["behavior"] = _settings(raw.get("behavior", {}), BEHAVIOR[name], f"{path}.behavior")
    return result


def _merge_defaults(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(defaults)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_defaults(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def enabled_elements(config: dict[str, Any], section: str) -> dict[str, Any]:
    if not config[section]["enabled"]:
        return {}
    return {name: element for name, element in config[section]["elements"].items() if element["enabled"]}


def validate_layout_config(data: dict[str, Any]) -> dict[str, Any]:
    root_fields = {"version", "canvas", "background", "title", "song"}
    if isinstance(data, dict) and "version" not in data:
        legacy = sorted(str(key) for key in data if key not in root_fields)
        if legacy:
            raise SongConfigError(
                f"layout contains unsupported field {legacy[0]!r}. Layouts without a version are v2; "
                "add 'version: 1' to render a legacy layout, or migrate it to v2."
            )
    raw = _mapping(data, "layout", root_fields)
    version = raw.get("version", 2)
    if type(version) is not int or version != 2:
        raise SongConfigError("layout.version must be the integer 2 for the v2 validator")
    try:
        defaults = yaml.safe_load(DEFAULT_LAYOUT_PATH.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SongConfigError(f"Unable to load the default v2 template: {exc}") from exc
    if not isinstance(defaults, dict) or defaults.get("version") != 2:
        raise SongConfigError("The default v2 template must be a version: 2 mapping")
    raw = _merge_defaults(defaults, raw)
    canvas = _mapping(raw.get("canvas", {}), "layout.canvas", {"width", "height"})
    for key, expected in (("width", CANVAS_WIDTH), ("height", CANVAS_HEIGHT)):
        value = _number(canvas.get(key, expected), f"layout.canvas.{key}")
        if value != expected:
            raise SongConfigError(f"layout.canvas.{key} must be {expected}")
    result = {"version": 2, "canvas": {"width": CANVAS_WIDTH, "height": CANVAS_HEIGHT}}
    background = _mapping(raw.get("background", {}), "layout.background", {"color", "layers"})
    result["background"] = _settings(
        {"color": background.get("color", "#000000")}, {"color": "#000000"}, "layout.background"
    )
    layers = background.get("layers", [])
    if not isinstance(layers, list):
        raise SongConfigError("layout.background.layers must be a list")
    result["background"]["layers"] = []
    for index, value in enumerate(layers):
        path = f"layout.background.layers[{index}]"
        layer = _mapping(value, path, {"type", "file", "media", "opacity"})
        if layer.get("type") != "image":
            raise SongConfigError(f"{path}.type must be image")
        sources = [key for key in ("file", "media") if key in layer]
        if len(sources) != 1:
            raise SongConfigError(f"{path} requires exactly one of file or media")
        source = sources[0]
        if not isinstance(layer[source], str) or not layer[source].strip():
            raise SongConfigError(f"{path}.{source} must be a non-empty string")
        opacity = _number(layer.get("opacity", 1.0), f"{path}.opacity", minimum=0, maximum=1)
        result["background"]["layers"].append({"type": "image", source: layer[source], "opacity": opacity})
    for section, names in (("title", TITLE_ELEMENTS), ("song", SONG_ELEMENTS)):
        path = f"layout.{section}"
        allowed = {"elements", "enabled", "countdown_beats"} if section == "title" else {"elements", "enabled"}
        section_data = _mapping(raw.get(section, {}), path, allowed)
        elements = _mapping(section_data.get("elements", {}), f"{path}.elements", names)
        result[section] = {"elements": {
            name: _element(value, name, f"{path}.elements.{name}") for name, value in elements.items()
        }}
        enabled = section_data.get("enabled", True)
        if not isinstance(enabled, bool):
            raise SongConfigError(f"{path}.enabled must be a boolean")
        result[section]["enabled"] = enabled
        if section == "title":
            if "countdown_beats" in section_data:
                count = section_data["countdown_beats"]
                if type(count) is not int or count < 0:
                    raise SongConfigError("layout.title.countdown_beats must be a non-negative integer")
                result[section]["countdown_beats"] = count
    return result
