"""Pure conversion of original v1 layouts into explicit v2 overrides."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw

from praecentor.io.layout import layout_version, resolve_layout_config
from praecentor.io.song import SongConfigError
from praecentor.render import cue_frames as cue, title_card as title


@dataclass
class LayoutConversion:
    spec: dict[str, Any]
    warnings: list[str]
    changed: bool


def _colour(value):
    return "#%02X%02X%02X" % cue._hex_to_rgb(str(value))


def _rectangle(region):
    return dict(zip(("x", "y", "width", "height"), region))


def _text_style(source, region, preset, renderer, font_dir):
    style = deepcopy(source if isinstance(source, dict) else preset)
    if str(style.get("font_family", "Inter")).lower() != "inter":
        raise SongConfigError("Migration currently supports Inter typography only")
    if font_dir:
        style["font_dir"] = str(font_dir)
    font = renderer._load_font(style)
    if "inter" not in font.getname()[0].lower():
        raise SongConfigError("Migration requires Inter; use --font-dir or PRAECENTOR_FONTS_DIR")
    weight = int(style.get("weight", 400))
    available = [value for value in (400, 500, 600, 700, 800) if value <= weight]
    weight = 300 if weight <= 300 else max(available, default=400)
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    fitted = renderer._fit_font_for_region(draw, "Ag", style, region)
    align = style.get("align", "center")
    result = {
        "font_family": "Inter", "weight": weight, "size_px": fitted.size,
        "color": _colour(style.get("color", "#FFFFFF")),
        "align": align if align in ("left", "center", "right") else "center",
        "valign": "center", "letter_spacing_em": renderer._style_letter_spacing_px(style, font) / max(1, font.size),
        "line_height": float(style.get("line_height", 1)),
    }
    return result


def convert_v1_layout(data: dict[str, Any], *, font_dir=None) -> LayoutConversion:
    version = layout_version(data)
    if version == 2:
        resolve_layout_config(data)
        return LayoutConversion(deepcopy(data), [], False)
    source = deepcopy(data)
    video = source.get("video") or {}
    if (video.get("width", 1920), video.get("height", 1080)) != (1920, 1080):
        raise SongConfigError("Migration requires a 1920x1080 v1 layout; review noncanonical canvas sizes manually")
    warnings = [
        "V1 fitted text uses a region-height maximum in v2; review short and long chord events.",
        "V2 uses the approved continuous lyric scroll and alpha fades, not the legacy wash/lyric animation.",
        "V2 clips decorations to their owning rectangles; legacy oversized rings/pulses may differ.",
    ]
    background = deepcopy(video.get("background") or {})
    kind = background.get("type", "solid")
    if kind not in ("solid", "image", "layers"):
        raise SongConfigError(f"Unsupported v1 background type: {kind}")
    layers = [background] if kind == "image" else background.get("layers", []) if kind == "layers" else []
    result = {"version": 2, "canvas": {"width": 1920, "height": 1080},
              "background": {"color": _colour(background.get("color", "#000000")), "layers": []},
              "title": {"enabled": (source.get("title") or {}).get("enabled", True), "elements": {}},
              "song": {"elements": {}}}
    for layer in layers:
        if not isinstance(layer, dict) or layer.get("type") != "image":
            raise SongConfigError("Only image background layers can be migrated")
        media = layer.get("media")
        if isinstance(media, dict):
            media = media.get("id")
        converted = {"type": "image", "opacity": layer.get("opacity", 1)}
        if media:
            converted["media"] = str(media)
        elif layer.get("file"):
            converted["file"] = layer["file"]
        else:
            raise SongConfigError("Background layer requires a file or media reference")
        result["background"]["layers"].append(converted)
    regions = source.get("regions") or {}
    defaults = {
        "chord_main": {"x": 0.18, "y": 0.78, "width": 0.38, "height": 0.13},
        "next_chord": {"x": 0.66, "y": 0.77, "width": 0.12, "height": 0.12},
        "beat_indicator": {"x": 0.82, "y": 0.73, "width": 0.17, "height": 0.24},
    }
    resolved = {name: cue._region_to_px(regions.get(name, default), 1920, 1080)
                for name, default in defaults.items()}
    indicator = resolved["beat_indicator"]
    gutter = max(0, 1920 - indicator[0] - indicator[2])
    resolved["lyric_main"] = cue._region_to_px(regions["lyric_main"], 1920, 1080) if isinstance(
        regions.get("lyric_main"), dict) else (gutter, 0, max(1, 1920 - gutter * 2), 1058)
    resolved["riff"] = cue._region_to_px(regions["riff"], 1920, 1080) if isinstance(
        regions.get("riff"), dict) else (54, indicator[1], 1498, indicator[3])
    typography = source.get("typography") or {}
    render = source.get("render") or {}
    for name, region in resolved.items():
        element = _rectangle(region)
        if name in ("lyric_main", "chord_main", "next_chord"):
            size = {"lyric_main": 94, "chord_main": 76, "next_chord": 64}[name]
            preset = {"size_px": size, "weight": 700, "font_family": "Inter",
                      "color": "#FF9F1C" if name == "next_chord" else "#FFFFFF", "align": "center"}
            style = _text_style(typography.get(name), region, preset, cue, font_dir or render.get("font_dir"))
            if name == "lyric_main":
                style["size_px"] = (typography.get(name) or preset).get("size_px", 48)
                for part in ("a", "b", "c"):
                    key = f"split_{part}_color"
                    if key in (typography.get(name) or {}):
                        style[key] = _colour(typography[name][key])
            element["style"] = style
        result["song"]["elements"][name] = element
    beat = result["song"]["elements"]["beat_indicator"]
    beat["appearance"] = {
        "fill_color": _colour(render.get("indicator_fill_color", "#72E676")),
        "pulse_first_color": _colour(render.get(
            "beat_pulse_first_color", render.get("indicator_fill_color", "#72E676")
        )),
        "pulse_other_color": _colour(render.get("beat_pulse_non_first_color", "#3B82F6")),
        "pulse_expand_px": render.get("beat_pulse_expand_px", 28),
        "pulse_width_px": render.get("beat_pulse_width_px", 5),
    }
    for old, new in (("next_chord_phrase_ring", "ring"), ("next_chord_warning", "warning")):
        values = deepcopy(render.get(old) or {})
        if "size_ratio" in values:
            warnings.append(f"{old}.size_ratio is not carried into v2; review ring footprint.")
            del values["size_ratio"]
        values["color"] = _colour(values.get("color", result["song"]["elements"]["next_chord"]["style"]["color"]))
        result["song"]["elements"]["next_chord"].setdefault("appearance", {})[new] = values
    old_next = deepcopy(typography.get("next_chord") or {"size_px": 64, "weight": 700, "font_family": "Inter"})
    if font_dir or render.get("font_dir"):
        old_next["font_dir"] = str(font_dir or render["font_dir"])
    glyph_width = cue._style_glyph_width_px(ImageDraw.Draw(Image.new("RGB", (1, 1))), old_next)
    next_element = result["song"]["elements"]["next_chord"]
    next_element["appearance"]["loop"] = {
        "icon_scale": glyph_width * float(old_next.get("loop_icon_scale", 3)) / next_element["style"]["size_px"],
        "y_offset_px": old_next.get("loop_icon_y_offset_px", -5),
    }
    riff = deepcopy(render.get("riff") or {})
    if riff.get("pre_roll_beats", 0) < 0:
        riff["pre_roll_beats"] = max(0, riff.get("viewport_beats", 8) * riff.get("center_fraction", 0.5))
    result["song"]["elements"]["riff"]["appearance"] = riff
    behavior = source.get("behavior") or {}
    result["song"]["elements"]["lyric_main"]["behavior"] = deepcopy(behavior.get("lyric_main") or {})
    lookahead = (behavior.get("chord_lookahead") or {}).get(
        "show_when_next_event_within_beats", (behavior.get("defaults") or {}).get("lookahead_beats"))
    if lookahead is not None:
        result["song"]["elements"]["next_chord"]["behavior"] = {"lookahead_beats": lookahead}
    title_source = source.get("title") or {}
    title_regions = title_source.get("regions") or {}
    title_typography = title_source.get("typography") or {}
    title_render = title_source.get("render") or {}
    title_defaults = {
        "title_text": (0.1, 0.15, 0.8, 0.2, 78, "#FFFFFF", 800),
        "metadata_text": (0.1, 0.4, 0.8, 0.22, 42, "#D9D9D9", 700),
        "countdown_text": (0.45, 0.75, 0.1, 0.1, 72, "#FFFFFF", 700),
        "first_chord_preview": (0.875, 0.73, 0.1, 0.19, 48, "#FFFFFF", 700),
    }
    for name, (left, top, width, height, size, colour, weight) in title_defaults.items():
        default = dict(x=left, y=top, width=width, height=height)
        region = title._region_to_px(title_regions.get(name, default), 1920, 1080)
        result["title"]["elements"][name] = {
            **_rectangle(region), "style": _text_style(
                title_typography.get(name), region,
                {"size_px": size, "weight": weight, "font_family": "Inter", "color": colour, "align": "center"},
                title, font_dir or title_render.get("font_dir"),
            ),
        }
    result["title"]["countdown_beats"] = title_source.get("countdown_beats", 8)
    result["title"]["elements"]["title_text"]["enabled"] = title_source.get("show_song_title", True)
    result["title"]["elements"]["metadata_text"]["behavior"] = {
        f"show_{key}": title_source.get(f"show_{key}", True) for key in ("artist", "key", "hot_start")
    }
    result["title"]["elements"]["beat_indicator"] = {"enabled": False}
    preview = result["title"]["elements"]["first_chord_preview"]
    preview["appearance"] = {"padding_px": title_render.get("first_chord_box_padding_px", 18), "ring": {
        "color": _colour(title_render.get("first_chord_ring_color", "#E8E8E8")),
        "width_px": title_render.get("first_chord_ring_width_px", 3),
        "dotted": title_render.get("first_chord_ring_dotted", True),
        "dash_degrees": title_render.get("first_chord_ring_dash_degrees", 5.5),
        "gap_degrees": title_render.get("first_chord_ring_gap_degrees", 4.5),
    }}
    preview["behavior"] = {
        "slide_beats": title_source.get("first_chord_preview_slide_beats", 1),
        "slide_lead_beats": title_source.get("first_chord_preview_slide_lead_beats", 1),
    }
    if "components" in source or "components" in title_source:
        warnings.append("Editor-era components allowlists are ignored under original v1 semantics.")
    resolve_layout_config(result)
    return LayoutConversion(result, warnings, True)
