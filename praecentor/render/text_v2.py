"""Measured, shrink-only text drawing for explicit v2 element rectangles."""

from __future__ import annotations

from functools import lru_cache
import math
from typing import Any

from PIL import Image, ImageDraw

from praecentor.render import cue_frames as legacy


def rectangle(element: dict[str, Any]) -> tuple[int, int, int, int]:
    left = math.floor(element["x"])
    top = math.floor(element["y"])
    return left, top, max(left + 1, math.ceil(element["x"] + element["width"])), max(
        top + 1, math.ceil(element["y"] + element["height"])
    )


def composite_element(image: Image.Image, overlay: Image.Image, element: dict[str, Any]) -> None:
    left, top, right, bottom = rectangle(element)
    bounds = (max(0, left), max(0, top), min(image.width, right), min(image.height, bottom))
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        return
    crop = overlay.crop(bounds)
    if image.mode == "RGBA":
        image.alpha_composite(crop, dest=bounds[:2])
    else:
        image.paste(crop, bounds[:2], crop)


@lru_cache(maxsize=256)
def _fonts(size: int, weight: int, candidates: tuple[str, ...]):
    base = legacy._load_font_from_candidates(candidates, size)
    flat = legacy._load_flat_symbol_font(size)
    if getattr(flat, "size", size) != size and hasattr(flat, "font_variant"):
        flat = flat.font_variant(size=size)
    return base, flat


def _layout(text: str, style: dict[str, Any], size: int, candidates=None):
    base, flat = _fonts(size, style["weight"], candidates or tuple(legacy._font_candidates(style)))
    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    reference = draw.textbbox((0, 0), "Ag", font=base)
    advance = max(1.0, (reference[3] - reference[1]) * style["line_height"])
    tracking = style["letter_spacing_em"] * size
    lines = []
    top = None
    bottom = None
    for index, line in enumerate(text.split("\n")):
        cursor = 0.0
        glyphs = []
        left = None
        right = None
        for glyph in line:
            font = flat if glyph == "♭" else base
            bbox = draw.textbbox((cursor, index * advance), glyph, font=font)
            left = bbox[0] if left is None else min(left, bbox[0])
            right = bbox[2] if right is None else max(right, bbox[2])
            top = bbox[1] if top is None else min(top, bbox[1])
            bottom = bbox[3] if bottom is None else max(bottom, bbox[3])
            glyphs.append((glyph, font, cursor, index * advance))
            cursor += draw.textlength(glyph, font=font) + tracking
        lines.append((glyphs, left or 0, max(0, (right or 0) - (left or 0))))
    return lines, min(0, top or 0) if text.startswith("\n") else (top or 0), bottom or 0


@lru_cache(maxsize=128)
def _fit_cached(text, style_items, width, height, candidates):
    style = dict(style_items)
    low, high = 1, math.floor(style["size_px"])
    best = None
    while low <= high:
        size = (low + high) // 2
        lines, top, bottom = _layout(text, style, size, candidates)
        if max((line[2] for line in lines), default=0) <= width and bottom - top <= height:
            best = (size, lines, top, bottom)
            low = size + 1
        else:
            high = size - 1
    return best


def fit_text(text: str, element: dict[str, Any], *, normalize_flats=False):
    if normalize_flats:
        text = legacy._normalize_chord_flats(text)
    style = element["style"]
    return _fit_cached(
        text, tuple(sorted(style.items())), element["width"], element["height"],
        tuple(legacy._font_candidates(style)),
    )


@lru_cache(maxsize=16)
def _sprite(text, style_items, width, height, candidates, color_items):
    fitted = _fit_cached(text, style_items, width, height, candidates)
    if fitted is None:
        return None
    style = dict(style_items)
    colors = dict(color_items)
    size, lines, top, bottom = fitted
    block_width = math.ceil(max((line[2] for line in lines), default=0))
    block_height = math.ceil(bottom - top)
    image = Image.new("RGBA", (max(1, block_width), max(1, block_height)))
    draw = ImageDraw.Draw(image)
    color = legacy._hex_to_rgb(style["color"])
    horizontal = {"left": 0, "center": 0.5, "right": 1}[style["align"]]
    for index, (glyphs, left, line_width) in enumerate(lines):
        offset_x = (block_width - line_width) * horizontal - left
        for glyph, font, glyph_x, glyph_y in glyphs:
            draw.text((offset_x + glyph_x, glyph_y - top), glyph, font=font, fill=(*colors.get(index, color), 255))
    return image, size


def draw_text(
    image: Image.Image, text: str, element: dict[str, Any], *, normalize_flats=False,
    opacity: float = 1.0, line_colors: dict[int, tuple[int, int, int]] | None = None,
) -> int | None:
    if not text or opacity <= 0:
        return None
    if normalize_flats:
        text = legacy._normalize_chord_flats(text)
    style = element["style"]
    cached = _sprite(
        text, tuple(sorted(style.items())), element["width"], element["height"],
        tuple(legacy._font_candidates(style)), tuple(sorted((line_colors or {}).items())),
    )
    if cached is None:
        return None
    sprite, size = cached
    if opacity < 1:
        sprite = sprite.copy()
        sprite.putalpha(sprite.getchannel("A").point(lambda value: round(value * opacity)))
    horizontal = {"left": 0, "center": 0.5, "right": 1}[style["align"]]
    vertical = {"top": 0, "center": 0.5, "bottom": 1}[style["valign"]]
    left = round(element["x"] + (element["width"] - sprite.width) * horizontal)
    top = round(element["y"] + (element["height"] - sprite.height) * vertical)
    element_left, element_top, element_right, element_bottom = rectangle(element)
    bounds = (
        max(0, left, element_left), max(0, top, element_top),
        min(image.width, left + sprite.width, element_right), min(image.height, top + sprite.height, element_bottom),
    )
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        return size
    crop = sprite.crop((bounds[0] - left, bounds[1] - top, bounds[2] - left, bounds[3] - top))
    if image.mode == "RGBA":
        image.alpha_composite(crop, dest=bounds[:2])
    else:
        image.paste(crop, bounds[:2], crop)
    return size
