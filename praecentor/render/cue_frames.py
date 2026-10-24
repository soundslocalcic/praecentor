from __future__ import annotations

from functools import lru_cache
import math
import os
import re
from bisect import bisect_right
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFont

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PACKAGE_ASSET_DIR = _PACKAGE_ROOT / "assets"
_REPO_ASSET_DIR = _REPO_ROOT / "assets"
_ASSET_DIR = _PACKAGE_ASSET_DIR if _PACKAGE_ASSET_DIR.exists() else _REPO_ASSET_DIR
_LOOP_SVG_PATH = _ASSET_DIR / "loop.svg"
_WARNING_SVG_PATH = _ASSET_DIR / "warning.svg"
_DEFAULT_ASSET_FONTS_DIR = (
    _PACKAGE_ASSET_DIR / "fonts"
    if (_PACKAGE_ASSET_DIR / "fonts").exists()
    else (_REPO_ASSET_DIR / "fonts")
)
_LOOP_ICON_CACHE: dict[tuple[int, tuple[int, int, int]], Image.Image] = {}
_WARNING_ICON_CACHE: dict[tuple[int, tuple[int, int, int]], Image.Image] = {}
_LYRIC_PART_RE = re.compile(r"^\s*\[([abcABC])\]\s?(.*)$")


def _configured_font_dirs(
    extra_dir: str | Path | None = None,
) -> tuple[Path, ...]:
    entries: list[Path] = []
    raw_entries = [
        extra_dir,
        os.environ.get("PRAECENTOR_FONTS_DIR"),
        os.environ.get("PRAECENTOR_FONT_DIR"),
    ]
    for raw in raw_entries:
        if raw in (None, ""):
            continue
        candidate = Path(str(raw)).expanduser()
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        entries.append(candidate)

    ordered: list[Path] = []
    seen: set[Path] = set()
    for candidate in [*entries, _DEFAULT_ASSET_FONTS_DIR]:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        if resolved in seen:
            continue
        ordered.append(resolved)
        seen.add(resolved)
    return tuple(ordered)


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    value = color.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        return (0, 0, 0)
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (0, 0, 0)


def _region_to_px(
    region: dict[str, Any], width: int, height: int
) -> tuple[int, int, int, int]:
    def _to_px(raw: Any, axis_size: int, *, default: float) -> int:
        try:
            value = float(raw if raw is not None else default)
        except (TypeError, ValueError):
            value = default
        if 0.0 <= value <= 1.0:
            return int(value * axis_size)
        return int(value)

    x = _to_px(region.get("x"), width, default=0.0)
    y = _to_px(region.get("y"), height, default=0.0)
    w = _to_px(region.get("width"), width, default=1.0)
    h = _to_px(region.get("height"), height, default=1.0)
    return x, y, max(1, w), max(1, h)


def _font_candidates(style: dict[str, Any]) -> list[str]:
    family_hint = str(style.get("font_family", "Inter")).strip()
    weight = int(style.get("weight", 400))
    candidates: list[str] = []
    custom_font_dir = style.get("font_dir")

    if family_hint and "/" in family_hint:
        candidates.append(family_hint)

    for font_dir in _configured_font_dirs(custom_font_dir):
        if "inter" in family_hint.lower():
            inter_weight = "Regular"
            if weight >= 800:
                inter_weight = "ExtraBold"
            elif weight >= 700:
                inter_weight = "Bold"
            elif weight >= 600:
                inter_weight = "SemiBold"
            elif weight >= 500:
                inter_weight = "Medium"
            elif weight <= 300:
                inter_weight = "Light"
            candidates.extend(
                [
                    str(font_dir / f"Inter_24pt-{inter_weight}.ttf"),
                    str(font_dir / f"Inter_18pt-{inter_weight}.ttf"),
                    str(font_dir / f"Inter_28pt-{inter_weight}.ttf"),
                ]
            )

    if "avenir" in family_hint.lower():
        candidates.extend(
            [
                "/System/Library/Fonts/Supplemental/Avenir Next.ttc",
                "/System/Library/Fonts/Supplemental/Avenir.ttc",
            ]
        )
    if "menlo" in family_hint.lower():
        candidates.append("/System/Library/Fonts/Menlo.ttc")

    if weight >= 600:
        candidates.extend(
            [
                "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                "/Library/Fonts/Arial Bold.ttf",
            ]
        )
    candidates.extend(
        [
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial.ttf",
        ]
    )

    candidates.extend(
        [
            "DejaVuSans-Bold.ttf" if weight >= 600 else "DejaVuSans.ttf",
            "DejaVuSans.ttf",
        ]
    )
    return candidates


def _load_font(
    style: dict[str, Any], size: int | None = None
) -> ImageFont.ImageFont:
    chosen_size = int(style.get("size_px", 48)) if size is None else int(size)

    return _load_font_from_candidates(
        tuple(_font_candidates(style)), chosen_size
    )


@lru_cache(maxsize=512)
def _load_font_from_candidates(
    candidates: tuple[str, ...], chosen_size: int
) -> ImageFont.ImageFont:
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=chosen_size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit_font_for_region(
    draw: ImageDraw.ImageDraw,
    text: str,
    style: dict[str, Any],
    region: tuple[int, int, int, int],
) -> ImageFont.ImageFont:
    if not text:
        return _load_font(style)

    _, _, max_w, max_h = region
    base_size = int(style.get("size_px", 48))
    low = 12
    high = max(base_size * 3, 64)
    best = _load_font(style, size=base_size)

    while low <= high:
        mid = (low + high) // 2
        candidate_font = _load_font(style, size=mid)
        bbox = draw.multiline_textbbox(
            (0, 0), text, font=candidate_font, align="center"
        )
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        if text_w <= max_w and text_h <= max_h:
            best = candidate_font
            low = mid + 1
        else:
            high = mid - 1

    return best


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    region: tuple[int, int, int, int],
    style: dict[str, Any],
) -> None:
    if not text:
        return

    font = _fit_font_for_region(draw, text, style, region)
    color = _hex_to_rgb(str(style.get("color", "#FFFFFF")))
    align = str(style.get("align", "center"))

    x, y, w, h = region
    bbox = draw.multiline_textbbox((0, 0), text, font=font, align=align)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    text_x = x + (w - text_w) // 2
    text_y = y + (h - text_h) // 2
    draw.multiline_text(
        (text_x, text_y), text, font=font, fill=color, align=align
    )


def _draw_centered_text_fixed(
    draw: ImageDraw.ImageDraw,
    text: str,
    region: tuple[int, int, int, int],
    style: dict[str, Any],
) -> None:
    if not text:
        return

    font = _load_font(style)
    color = _hex_to_rgb(str(style.get("color", "#FFFFFF")))
    align = str(style.get("align", "center"))

    x, y, w, h = region
    bbox = draw.multiline_textbbox((0, 0), text, font=font, align=align)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    text_x = x + (w - text_w) // 2
    text_y = y + (h - text_h) // 2
    draw.multiline_text(
        (text_x, text_y), text, font=font, fill=color, align=align
    )


def _blend_color(
    foreground: tuple[int, int, int],
    background: tuple[int, int, int],
    alpha: float,
) -> tuple[int, int, int]:
    a = max(0.0, min(1.0, alpha))
    return (
        int(round((foreground[0] * a) + (background[0] * (1.0 - a)))),
        int(round((foreground[1] * a) + (background[1] * (1.0 - a)))),
        int(round((foreground[2] * a) + (background[2] * (1.0 - a)))),
    )


def _draw_centered_text_at_y(
    draw: ImageDraw.ImageDraw,
    text: str,
    region: tuple[int, int, int, int],
    center_y: int,
    style: dict[str, Any],
    background_color: tuple[int, int, int],
    alpha: float = 1.0,
) -> None:
    if not text:
        return

    font = _load_font(style)
    fg = _hex_to_rgb(str(style.get("color", "#FFFFFF")))
    color = _blend_color(fg, background_color, alpha)
    align = str(style.get("align", "center"))

    x, _y, w, _h = region
    bbox = draw.multiline_textbbox((0, 0), text, font=font, align=align)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    text_x = x + (w - text_w) // 2
    text_y = center_y - (text_h // 2)
    draw.multiline_text(
        (text_x, text_y), text, font=font, fill=color, align=align
    )


def _draw_centered_text_with_line_overrides_at_y(
    draw: ImageDraw.ImageDraw,
    text: str,
    region: tuple[int, int, int, int],
    center_y: int,
    style: dict[str, Any],
    background_color: tuple[int, int, int],
    alpha: float = 1.0,
    line_override_colors: dict[int, tuple[int, int, int]] | None = None,
) -> None:
    if not text:
        return

    if not line_override_colors:
        _draw_centered_text_at_y(
            draw,
            text,
            region,
            center_y,
            style,
            background_color,
            alpha,
        )
        return

    font = _load_font(style)
    fg = _hex_to_rgb(str(style.get("color", "#FFFFFF")))
    align = str(style.get("align", "center"))

    x, _y, w, _h = region
    bbox = draw.multiline_textbbox((0, 0), text, font=font, align=align)
    text_h = bbox[3] - bbox[1]

    line_bbox = draw.textbbox((0, 0), "Ag", font=font)
    line_height = max(1, line_bbox[3] - line_bbox[1])
    line_spacing = 4

    text_y = center_y - (text_h // 2)
    cursor_y = text_y
    lines = text.splitlines() or [""]
    for line_idx, line in enumerate(lines):
        line_text = line if line else " "
        line_text_bbox = draw.textbbox((0, 0), line_text, font=font)
        line_w = line_text_bbox[2] - line_text_bbox[0]
        line_x = x + (w - line_w) // 2
        line_fg = line_override_colors.get(line_idx, fg)
        line_color = _blend_color(line_fg, background_color, alpha)
        draw.text((line_x, cursor_y), line, font=font, fill=line_color)
        cursor_y += line_height
        if line_idx + 1 < len(lines):
            cursor_y += line_spacing


def _style_cache_key(style: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted((str(key), repr(value)) for key, value in style.items())
    )


def _line_override_colors_key(
    line_override_colors: dict[int, tuple[int, int, int]] | None,
) -> tuple[tuple[int, int, int, int], ...]:
    if not line_override_colors:
        return ()
    return tuple(
        sorted(
            (
                int(idx),
                int(rgb[0]),
                int(rgb[1]),
                int(rgb[2]),
            )
            for idx, rgb in line_override_colors.items()
        )
    )


def _render_centered_text_sprite_with_line_overrides(
    text: str,
    style: dict[str, Any],
    background_color: tuple[int, int, int],
    alpha: float,
    line_override_colors: dict[int, tuple[int, int, int]] | None = None,
) -> Image.Image:
    if not text:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))

    font = _load_font(style)
    fg = _hex_to_rgb(str(style.get("color", "#FFFFFF")))
    lines = text.splitlines() or [""]

    measure_image = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    measure_draw = ImageDraw.Draw(measure_image)
    line_bbox = measure_draw.textbbox((0, 0), "Ag", font=font)
    line_height = max(1, line_bbox[3] - line_bbox[1])
    line_spacing = 4

    line_widths: list[int] = []
    for line in lines:
        line_text = line if line else " "
        line_text_bbox = measure_draw.textbbox((0, 0), line_text, font=font)
        line_widths.append(max(1, line_text_bbox[2] - line_text_bbox[0]))

    sprite_w = max(1, max(line_widths, default=1))

    # Compute exact drawn bounds first so descenders/accents are never clipped.
    min_x: int | None = None
    min_y: int | None = None
    max_x: int | None = None
    max_y: int | None = None
    cursor_y = 0
    for line_idx, line in enumerate(lines):
        line_text = line if line else " "
        line_w = line_widths[line_idx]
        line_x = (sprite_w - line_w) // 2
        left, top, right, bottom = measure_draw.textbbox(
            (line_x, cursor_y), line_text, font=font
        )
        if min_x is None or left < min_x:
            min_x = left
        if min_y is None or top < min_y:
            min_y = top
        if max_x is None or right > max_x:
            max_x = right
        if max_y is None or bottom > max_y:
            max_y = bottom

        cursor_y += line_height
        if line_idx + 1 < len(lines):
            cursor_y += line_spacing

    if min_x is None or min_y is None or max_x is None or max_y is None:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))

    draw_w = max(1, max_x - min_x)
    draw_h = max(1, max_y - min_y)
    sprite = Image.new("RGBA", (draw_w, draw_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(sprite)
    alpha_channel = max(
        0, min(255, int(round(max(0.0, min(1.0, alpha)) * 255.0)))
    )

    cursor_y = 0
    for line_idx, line in enumerate(lines):
        line_text = line if line else " "
        line_w = line_widths[line_idx]
        line_x = (sprite_w - line_w) // 2
        line_fg = (
            fg
            if not line_override_colors
            else line_override_colors.get(line_idx, fg)
        )
        draw.text(
            (line_x - min_x, cursor_y - min_y),
            line_text,
            font=font,
            fill=(line_fg[0], line_fg[1], line_fg[2], alpha_channel),
        )
        cursor_y += line_height
        if line_idx + 1 < len(lines):
            cursor_y += line_spacing

    return sprite


def _render_chord_overlay(
    text: str,
    style: dict[str, Any],
    region_size: tuple[int, int],
) -> Image.Image:
    region_w, region_h = region_size
    overlay = Image.new(
        "RGBA", (max(1, region_w), max(1, region_h)), (0, 0, 0, 0)
    )
    draw = ImageDraw.Draw(overlay)
    _draw_chord_text_with_flat_fallback(
        draw, text, (0, 0, max(1, region_w), max(1, region_h)), style
    )
    return overlay


def _normalize_chord_flats(text: str) -> str:
    # Replace note-letter flats (Bb, Dbm, etc.) with the dedicated flat glyph.
    return re.sub(r"([A-G])b", r"\1♭", text)


@lru_cache(maxsize=64)
def _load_flat_symbol_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "DejaVuSans.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=max(8, int(size)))
        except OSError:
            continue
    return ImageFont.load_default()


def _char_width(
    draw: ImageDraw.ImageDraw, ch: str, font: ImageFont.ImageFont
) -> int:
    bbox = draw.textbbox((0, 0), ch, font=font)
    return max(1, bbox[2] - bbox[0])


def _measure_mixed_font_line(
    draw: ImageDraw.ImageDraw,
    line: str,
    base_font: ImageFont.ImageFont,
    flat_font: ImageFont.ImageFont,
) -> int:
    width = 0
    for ch in line:
        width += _char_width(draw, ch, flat_font if ch == "♭" else base_font)
    return width


def _mixed_font_block_bbox(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    *,
    base_font: ImageFont.ImageFont,
    flat_font: ImageFont.ImageFont,
    line_height_px: int,
) -> tuple[int, int, int, int]:
    min_x: int | None = None
    min_y: int | None = None
    max_x: int | None = None
    max_y: int | None = None

    for line_idx, line in enumerate(lines):
        cursor_x = 0
        cursor_y = line_idx * line_height_px
        for ch in line:
            glyph_font = flat_font if ch == "♭" else base_font
            left, top, right, bottom = draw.textbbox(
                (cursor_x, cursor_y), ch, font=glyph_font
            )
            if min_x is None or left < min_x:
                min_x = left
            if min_y is None or top < min_y:
                min_y = top
            if max_x is None or right > max_x:
                max_x = right
            if max_y is None or bottom > max_y:
                max_y = bottom
            cursor_x += _char_width(draw, ch, glyph_font)

    if min_x is None or min_y is None or max_x is None or max_y is None:
        block_h = max(1, line_height_px * max(1, len(lines)))
        return (0, 0, 1, block_h)
    return (min_x, min_y, max_x, max_y)


def _draw_chord_text_with_flat_fallback(
    draw: ImageDraw.ImageDraw,
    text: str,
    region: tuple[int, int, int, int],
    style: dict[str, Any],
) -> None:
    if not text:
        return

    text = _normalize_chord_flats(text)
    base_font = _fit_font_for_region(draw, text, style, region)
    flat_font = _load_flat_symbol_font(
        getattr(base_font, "size", int(style.get("size_px", 48)))
    )
    color = _hex_to_rgb(str(style.get("color", "#FFFFFF")))

    x, y, w, h = region
    lines = text.splitlines() or [""]
    base_line_bbox = draw.textbbox((0, 0), "Ag", font=base_font)
    base_line_height = max(1, base_line_bbox[3] - base_line_bbox[1])
    line_height_px = int(
        base_line_height * float(style.get("line_height", 1.0))
    )
    line_height_px = max(1, line_height_px)
    block_bbox = _mixed_font_block_bbox(
        draw,
        lines,
        base_font=base_font,
        flat_font=flat_font,
        line_height_px=line_height_px,
    )
    block_w = max(1, block_bbox[2] - block_bbox[0])
    block_h = max(1, block_bbox[3] - block_bbox[1])
    start_x = x + max(0, (w - block_w) // 2) - block_bbox[0]
    start_y = y + max(0, (h - block_h) // 2) - block_bbox[1]

    cursor_y = start_y
    for line in lines:
        line_w = _measure_mixed_font_line(draw, line, base_font, flat_font)
        cursor_x = start_x + max(0, (block_w - line_w) // 2)
        for ch in line:
            glyph_font = flat_font if ch == "♭" else base_font
            draw.text((cursor_x, cursor_y), ch, font=glyph_font, fill=color)
            cursor_x += _char_width(draw, ch, glyph_font)
        cursor_y += line_height_px


def _style_line_height_px(
    draw: ImageDraw.ImageDraw, style: dict[str, Any]
) -> int:
    font = _load_font(style)
    bbox = draw.textbbox((0, 0), "Ag", font=font)
    base_line_height = max(1, bbox[3] - bbox[1])
    return max(1, int(base_line_height * float(style.get("line_height", 1.0))))


def _style_glyph_width_px(
    draw: ImageDraw.ImageDraw, style: dict[str, Any], glyph: str = "A"
) -> int:
    font = _load_font(style)
    bbox = draw.textbbox((0, 0), glyph, font=font)
    return max(1, bbox[2] - bbox[0])


def _hex_color_from_rgb(color: tuple[int, int, int]) -> str:
    return f"#{color[0]:02X}{color[1]:02X}{color[2]:02X}"


def _render_loop_icon_fallback(
    width_px: int, color: tuple[int, int, int]
) -> Image.Image:
    # Simple circular-arrow fallback when SVG rasterization backend is unavailable.
    width_px = max(1, int(width_px))
    height_px = max(1, int(round(width_px * (17.0 / 20.0))))
    icon = Image.new("RGBA", (width_px, height_px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon)

    stroke = max(1, int(round(height_px * 0.12)))
    pad = max(stroke + 1, int(round(height_px * 0.1)))
    arc_bbox = [pad, pad, width_px - pad, height_px - pad]
    draw.arc(arc_bbox, start=35, end=320, fill=color + (255,), width=stroke)

    # Arrow head near top-right.
    tip_x = width_px - pad
    tip_y = int(round(height_px * 0.34))
    head = [
        (tip_x, tip_y),
        (tip_x - max(2, stroke * 2), tip_y - max(2, stroke)),
        (tip_x - max(2, stroke), tip_y + max(2, stroke * 2)),
    ]
    draw.polygon(head, fill=color + (255,))
    return icon


def _loop_icon_image(
    width_px: int, color: tuple[int, int, int]
) -> Image.Image:
    key = (max(1, width_px), color)
    cached = _LOOP_ICON_CACHE.get(key)
    if cached is not None:
        return cached

    icon: Image.Image | None = None
    if _LOOP_SVG_PATH.exists():
        try:
            import cairosvg  # type: ignore

            svg_text = _LOOP_SVG_PATH.read_text(encoding="utf-8")
            colored_svg = svg_text.replace(
                "currentColor", _hex_color_from_rgb(color)
            )
            png_bytes = cairosvg.svg2png(
                bytestring=colored_svg.encode("utf-8"), output_width=key[0]
            )
            icon = Image.open(BytesIO(png_bytes)).convert("RGBA")
        except Exception:
            icon = None

    if icon is None:
        icon = _render_loop_icon_fallback(key[0], color)

    _LOOP_ICON_CACHE[key] = icon
    return icon


def _render_warning_icon_fallback(
    width_px: int, color: tuple[int, int, int]
) -> Image.Image:
    width_px = max(1, int(width_px))
    height_px = max(1, int(round(width_px * (76.0 / 82.0))))
    icon = Image.new("RGBA", (width_px, height_px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon)

    pad = max(1, int(round(width_px * 0.04)))
    top_y = pad
    bottom_y = height_px - pad
    left_x = pad
    right_x = width_px - pad
    apex_x = width_px // 2
    triangle = [(apex_x, top_y), (right_x, bottom_y), (left_x, bottom_y)]
    draw.polygon(triangle, fill=color + (255,))

    hole_color = (0, 0, 0, 0)
    stem_w = max(1, int(round(width_px * 0.11)))
    stem_h = max(1, int(round(height_px * 0.30)))
    stem_x0 = (width_px - stem_w) // 2
    stem_y0 = int(round(height_px * 0.36))
    draw.rounded_rectangle(
        [stem_x0, stem_y0, stem_x0 + stem_w, stem_y0 + stem_h],
        radius=max(1, stem_w // 2),
        fill=hole_color,
    )
    dot_r = max(1, int(round(width_px * 0.055)))
    dot_cx = width_px // 2
    dot_cy = int(round(height_px * 0.82))
    draw.ellipse(
        [dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r],
        fill=hole_color,
    )
    return icon


def _warning_icon_image(
    width_px: int, color: tuple[int, int, int]
) -> Image.Image:
    key = (max(1, width_px), color)
    cached = _WARNING_ICON_CACHE.get(key)
    if cached is not None:
        return cached

    icon: Image.Image | None = None
    if _WARNING_SVG_PATH.exists():
        try:
            import cairosvg  # type: ignore

            svg_text = _WARNING_SVG_PATH.read_text(encoding="utf-8")
            png_bytes = cairosvg.svg2png(
                bytestring=svg_text.encode("utf-8"), output_width=key[0]
            )
            source_icon = Image.open(BytesIO(png_bytes)).convert("RGBA")
            alpha = source_icon.getchannel("A")
            tinted = Image.new("RGBA", source_icon.size, color + (255,))
            tinted.putalpha(alpha)
            icon = tinted
        except Exception:
            icon = None

    if icon is None:
        icon = _render_warning_icon_fallback(key[0], color)

    _WARNING_ICON_CACHE[key] = icon
    return icon


def _draw_loop_indicator(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    region: tuple[int, int, int, int],
    style: dict[str, Any],
) -> None:
    icon_width = _style_glyph_width_px(draw, style, glyph="A")
    try:
        loop_icon_scale = float(style.get("loop_icon_scale", 3.0))
    except (TypeError, ValueError):
        loop_icon_scale = 3.0
    icon_width = max(1, int(round(icon_width * max(0.1, loop_icon_scale))))
    color = _hex_to_rgb(str(style.get("color", "#FFFFFF")))
    icon = _loop_icon_image(icon_width, color)

    x, y, w, h = region
    try:
        y_offset_px = int(style.get("loop_icon_y_offset_px", -5))
    except (TypeError, ValueError):
        y_offset_px = -5
    ix = x + max(0, (w - icon.width) // 2)
    iy = y + max(0, (h - icon.height) // 2) + y_offset_px
    image.paste(icon, (ix, iy), icon)


def _draw_next_warning_indicator(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    next_chord_region: tuple[int, int, int, int],
    *,
    color: tuple[int, int, int],
    icon_scale: float,
    gap_px: int,
) -> None:
    icon_width = _style_glyph_width_px(
        draw,
        {
            "font_family": "Inter",
            "weight": 700,
            "size_px": max(24, next_chord_region[3] // 4),
        },
        glyph="A",
    )
    icon_width = max(1, int(round(icon_width * max(0.1, icon_scale))))
    icon = _warning_icon_image(icon_width, color)

    x, y, w, _h = next_chord_region
    ix = x + max(0, (w - icon.width) // 2)
    iy = y - icon.height - max(0, gap_px)
    image.paste(icon, (ix, iy), icon)


def _event_has_next_warning(event: dict[str, Any]) -> bool:
    raw_warning = event.get("warning")
    if not isinstance(raw_warning, str):
        return False
    return raw_warning.strip().lower() == "next"


def _flash_is_on(
    current_time: float, flash_hz: float, duty_cycle: float
) -> bool:
    if flash_hz <= 0:
        return True
    clamped_duty = max(0.0, min(1.0, duty_cycle))
    phase = (current_time * flash_hz) % 1.0
    return phase < clamped_duty


def _effective_chords(events: list[dict[str, Any]], event_index: int) -> str:
    current = ""
    for idx in range(0, event_index + 1):
        raw = events[idx].get("chords")
        if raw is None:
            continue
        if not isinstance(raw, str):
            continue
        if raw == "":
            current = ""
        else:
            current = raw
    return current


def _event_riff_text(event: dict[str, Any]) -> str:
    raw = event.get("riff")
    if not isinstance(raw, str):
        return ""
    return raw.strip()


def _parse_key_root_and_mode(key_text: str) -> tuple[int, str] | None:
    text = key_text.strip()
    if not text:
        return None

    match = re.search(r"([A-Ga-g])\s*([#b♯♭]?)", text)
    if match is None:
        return None

    letter = match.group(1).upper()
    accidental = (match.group(2) or "").replace("♯", "#").replace("♭", "b")

    base_pc = {
        "C": 0,
        "D": 2,
        "E": 4,
        "F": 5,
        "G": 7,
        "A": 9,
        "B": 11,
    }[letter]
    if accidental == "#":
        base_pc += 1
    elif accidental == "b":
        base_pc -= 1
    root_pc = base_pc % 12

    lower = text.lower()
    mode = (
        "minor"
        if ("minor" in lower or re.search(r"\bm\b", lower))
        else "major"
    )
    return root_pc, mode


def _root_for_event(
    *,
    event: dict[str, Any],
    previous_root: str | None,
    key_text: str | None,
) -> str:
    raw = event.get("root")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if previous_root is not None:
        return previous_root
    if key_text is not None and key_text.strip():
        match = re.search(r"([A-Ga-g])\s*([#b♯♭]?)", key_text.strip())
        if match is not None:
            return f"{match.group(1).upper()}{(match.group(2) or '').replace('♯', '#').replace('♭', 'b')}"  # NOQA E501

    return "C"


def _note_pitch_class_from_token(token: str) -> int:
    match = re.fullmatch(r"([A-Ga-g])([#b♯♭]?)", token.strip())
    if match is None:
        raise ValueError(f"Invalid note token '{token}'")

    letter = match.group(1).upper()
    accidental = (match.group(2) or "").replace("♯", "#").replace("♭", "b")
    base_pc = {
        "C": 0,
        "D": 2,
        "E": 4,
        "F": 5,
        "G": 7,
        "A": 9,
        "B": 11,
    }[letter]
    if accidental == "#":
        base_pc += 1
    elif accidental == "b":
        base_pc -= 1
    return base_pc % 12


def _scale_degree_for_pitch_class(
    *,
    note_pc: int,
    root_pc: int,
    mode: str,
) -> float:
    intervals = (
        [0, 2, 4, 5, 7, 9, 11] if mode == "major" else [0, 2, 3, 5, 7, 8, 10]
    )
    rel = (int(note_pc) - int(root_pc)) % 12

    for idx, semitone in enumerate(intervals):
        if rel == semitone:
            return float(idx + 1)

    wrapped = intervals + [12]
    for idx in range(len(wrapped) - 1):
        low = wrapped[idx]
        high = wrapped[idx + 1]
        if low < rel < high:
            degree = idx + 1
            if (high - low) == 1:
                return float(degree)
            if rel == (low + 1):
                return float(degree) + 0.5
            # Defensive fallback for any unexpected larger gap.
            return float(degree) + ((rel - low) / (high - low))

    return 1.0


def _parse_named_riff_notes(
    riff: str,
    *,
    key_text: str | None = None,
    root_text: str | None = None,
) -> list[tuple[float, float | None]]:
    key_info = _parse_key_root_and_mode(key_text or "")
    if key_info is None:
        root_pc, mode = (0, "major")
    else:
        root_pc, mode = key_info

    if root_text is not None and root_text.strip():
        root_match = re.search(r"([A-Ga-g])\s*([#b♯♭]?)", root_text.strip())
        if root_match is not None:
            root_letter = root_match.group(1).upper()
            root_accidental = (
                (root_match.group(2) or "").replace("♯", "#").replace("♭", "b")
            )
            base_pc = {
                "C": 0,
                "D": 2,
                "E": 4,
                "F": 5,
                "G": 7,
                "A": 9,
                "B": 11,
            }[root_letter]
            if root_accidental == "#":
                base_pc += 1
            elif root_accidental == "b":
                base_pc -= 1
            root_pc = base_pc % 12
            mode = (
                "minor"
                if re.search(r"minor|m\b", root_text.lower()) is not None
                else "major"
            )

    out: list[tuple[float, float | None]] = []
    for token in riff.split():
        match = re.fullmatch(
            r"([A-Ga-g][#b♯♭]?)(\d+(?:\.\d+)?)", token.strip()
        )
        if match is None:
            continue
        note_name = match.group(1)
        duration = float(match.group(2))
        if duration <= 0:
            continue

        note_pc = _note_pitch_class_from_token(note_name)
        degree = _scale_degree_for_pitch_class(
            note_pc=note_pc, root_pc=root_pc, mode=mode
        )
        out.append((degree, duration))

    return out


def _parse_riff_notes(
    riff: str,
    *,
    key_text: str | None = None,
    root_text: str | None = None,
) -> list[tuple[float, float | None]]:
    """Parse riff text into (pitch_number, duration_beats_or_none).

    Duration is inferred from the separator immediately following each note:
    - space -> 1 beat
    - dash  -> 0.5 beat
    - adjacent note with no separator -> 0.25 beat
    - end-of-string / unknown separator -> None (caller decides, usually final fill)
    """
    if re.search(r"[A-Ga-g]", riff):
        named = _parse_named_riff_notes(
            riff, key_text=key_text, root_text=root_text
        )
        if named:
            return named

    out: list[tuple[float, float | None]] = []
    text = riff.strip()
    i = 0
    while i < len(text):
        ch = text[i]
        if not ch.isdigit():
            i += 1
            continue

        start = i
        i += 1
        # Notes are single scale-degree digits with optional decimal fraction
        # (eg 5.5 for a semitone). This preserves adjacent runs like "13".
        if i < len(text) and text[i] == ".":
            i += 1
            fraction_start = i
            while i < len(text) and text[i].isdigit():
                i += 1
            fraction_digits = i - fraction_start
            if fraction_digits > 1:
                raise ValueError(
                    f"Riff pitch '{text[start:i]}' uses more than one decimal place; "
                    "only one decimal place is supported"
                )
        pitch = float(text[start:i])

        j = i
        duration: float | None = None
        next_i = j

        if j < len(text):
            sep = text[j]
            if sep == " ":
                duration = 1.0
                while j < len(text) and text[j] == " ":
                    j += 1
                next_i = j
            elif sep == "-":
                duration = 0.5
                while j < len(text) and text[j] == "-":
                    j += 1
                while j < len(text) and text[j] == " ":
                    j += 1
                next_i = j
            elif sep.isdigit():
                duration = 0.25
                next_i = j
            else:
                next_i = j + 1

        out.append((pitch, duration))
        i = max(start + 1, next_i)

    return out


def _riff_note_spans_for_event(
    parsed_notes: list[tuple[float, float | None]],
    *,
    event_total_beats: float,
) -> list[tuple[float, float, float]]:
    if not parsed_notes:
        return []

    total_beats = max(0.0, float(event_total_beats))
    if total_beats <= 0.0:
        return []

    spans: list[tuple[int, float, float]] = []
    cursor = 0.0
    min_note_beats = 0.03125

    for idx, (pitch, parsed_duration) in enumerate(parsed_notes):
        is_last = idx == (len(parsed_notes) - 1)
        if parsed_duration is None:
            if is_last:
                duration = max(min_note_beats, total_beats - cursor)
            else:
                duration = 0.25
        else:
            duration = max(min_note_beats, float(parsed_duration))

        if cursor >= total_beats:
            break

        if is_last and parsed_duration is None:
            note_end = total_beats
        else:
            note_end = min(total_beats, cursor + duration)
            if note_end <= cursor:
                note_end = min(total_beats, cursor + min_note_beats)

        if note_end > cursor:
            spans.append((pitch, cursor, note_end))
        cursor = note_end

    return spans


def _build_riff_timeline(
    *,
    song_events: list[dict[str, Any]],
    effective_riffs: list[str],
    event_starts_seconds: list[float],
    tail_seconds: float,
    time_origin_seconds: float,
    beat_seconds: float,
    beat_times_seconds: list[float] | None,
    beat_time_offsets_seconds: list[float] | None,
    event_beat_anchor_indices: list[int],
    riff_key_text: str | None = None,
) -> tuple[list[dict[str, float | int]], float | None, float | None]:
    notes: list[dict[str, float | int]] = []
    pitch_min: float | None = None
    pitch_max: float | None = None

    effective_roots = _effective_roots_for_all(
        song_events, key_text=riff_key_text
    )
    for idx, event in enumerate(song_events):
        riff_text = effective_riffs[idx] if idx < len(effective_riffs) else ""
        if not riff_text:
            continue

        parsed = _parse_riff_notes(
            riff_text,
            key_text=riff_key_text,
            root_text=effective_roots[idx],
        )
        if not parsed:
            continue

        event_start = event_starts_seconds[idx]
        if idx + 1 < len(event_starts_seconds):
            event_end = event_starts_seconds[idx + 1]
        else:
            event_end = event_start + tail_seconds

        event_offset = 0.0
        if beat_time_offsets_seconds is not None and idx < len(
            beat_time_offsets_seconds
        ):
            event_offset = float(beat_time_offsets_seconds[idx])

        end_offset = event_offset
        if beat_time_offsets_seconds is not None and (idx + 1) < len(
            beat_time_offsets_seconds
        ):
            end_offset = float(beat_time_offsets_seconds[idx + 1])

        anchor_idx = event_beat_anchor_indices[idx]
        event_start_time_for_beat = (
            time_origin_seconds + event_start + event_offset
        )
        event_end_time_for_beat = time_origin_seconds + event_end + end_offset

        event_start_beat = _beat_position_at_time(
            event_start_time_for_beat,
            beat_seconds=beat_seconds,
            beat_times_seconds=beat_times_seconds,
            beat_anchor_index=anchor_idx,
        )
        event_total_beats = _beats_until_target_time(
            current_time=event_start_time_for_beat,
            target_time=event_end_time_for_beat,
            beat_seconds=beat_seconds,
            beat_times_seconds=beat_times_seconds,
            beat_anchor_index=anchor_idx,
        )
        if event_total_beats <= 0.0:
            event_total_beats = max(
                0.001, (event_end - event_start) / max(0.001, beat_seconds)
            )

        spans = _riff_note_spans_for_event(
            parsed, event_total_beats=event_total_beats
        )
        for pitch, rel_start, rel_end in spans:
            abs_start = event_start_beat + rel_start
            abs_end = event_start_beat + rel_end
            notes.append(
                {
                    "start_beat": abs_start,
                    "end_beat": abs_end,
                    "pitch": pitch,
                    "event_index": idx,
                }
            )
            pitch_min = pitch if pitch_min is None else min(pitch_min, pitch)
            pitch_max = pitch if pitch_max is None else max(pitch_max, pitch)

    notes.sort(
        key=lambda item: (float(item["start_beat"]), float(item["end_beat"]))
    )
    return notes, pitch_min, pitch_max


def _riff_view_window(
    *,
    now_beat: float,
    viewport_beats: float,
    center_fraction: float,
) -> tuple[float, float]:
    viewport = max(0.5, float(viewport_beats))
    center = max(0.0, min(1.0, float(center_fraction)))
    view_start = now_beat - (viewport * center)
    view_end = view_start + viewport
    return view_start, view_end


def _riff_window_has_visible_notes(
    notes: list[dict[str, float | int]],
    *,
    view_start: float,
    view_end: float,
) -> bool:
    for note in notes:
        note_start = float(note["start_beat"])
        note_end = float(note["end_beat"])
        if note_end > view_start and note_start < view_end:
            return True
    return False


def _riff_staff_bounds(pitch_min: float, pitch_max: float) -> tuple[int, int]:
    low = int(math.floor(float(pitch_min)))
    high = int(math.ceil(float(pitch_max)))
    if high < low:
        return high, low
    return low, high


def _riff_row_offset_for_pitch(
    pitch: float, *, staff_low: int, staff_high: int
) -> float:
    span = max(0, staff_high - staff_low)
    offset = float(staff_high) - float(pitch)
    return max(0.0, min(float(span), offset))


def _draw_riff_timeline(
    draw: ImageDraw.ImageDraw,
    *,
    region: tuple[int, int, int, int],
    notes: list[dict[str, float | int]],
    now_beat: float,
    current_event_index: int,
    pitch_min: float,
    pitch_max: float,
    viewport_beats: float,
    center_fraction: float,
    lane_padding_px: int,
    lane_gap_px: int,
    note_side_margin_px: int,
    note_height_ratio: float,
    lane_color: tuple[int, int, int],
    note_color: tuple[int, int, int],
    active_note_color: tuple[int, int, int],
    playhead_color: tuple[int, int, int],
    playhead_width_px: int,
) -> None:
    if not notes:
        return

    x, y, w, h = region
    if w <= 1 or h <= 1:
        return

    staff_low, staff_high = _riff_staff_bounds(
        float(pitch_min), float(pitch_max)
    )
    total_rows = max(1, (staff_high - staff_low) + 1)
    pad = max(0, int(lane_padding_px))
    gap = max(0, int(lane_gap_px))
    note_side_margin = max(0, int(note_side_margin_px))
    inner_x = x + pad
    inner_y = y + pad
    inner_w = max(1, w - (2 * pad))
    inner_h = max(1, h - (2 * pad))

    total_gap = gap * max(0, total_rows - 1)
    lane_h = max(1, (inner_h - total_gap) // total_rows)
    note_h = max(1, int(round(lane_h * max(0.1, min(1.0, note_height_ratio)))))

    viewport = max(0.5, float(viewport_beats))
    view_start, view_end = _riff_view_window(
        now_beat=now_beat,
        viewport_beats=viewport,
        center_fraction=center_fraction,
    )

    for row in range(total_rows):
        row_top = inner_y + (row * (lane_h + gap))
        row_bottom = row_top + lane_h - 1
        radius = max(1, lane_h // 2)
        draw.rounded_rectangle(
            [inner_x, row_top, inner_x + inner_w - 1, row_bottom],
            radius=radius,
            fill=lane_color,
        )

    for note in notes:
        start_beat = float(note["start_beat"])
        end_beat = float(note["end_beat"])
        if end_beat <= view_start or start_beat >= view_end:
            continue

        pitch = float(note["pitch"])
        event_index = int(note["event_index"])
        row_offset = _riff_row_offset_for_pitch(
            pitch,
            staff_low=staff_low,
            staff_high=staff_high,
        )
        lane_top_f = inner_y + (row_offset * float(lane_h + gap))
        lane_bottom_f = lane_top_f + lane_h - 1
        lane_left = inner_x
        lane_right = inner_x + inner_w - 1

        nx0 = lane_left + int(
            round(((start_beat - view_start) / viewport) * inner_w)
        )
        nx1 = lane_left + int(
            round(((end_beat - view_start) / viewport) * inner_w)
        )
        nx0 = max(lane_left, min(lane_right, nx0))
        nx1 = max(lane_left, min(lane_right + 1, nx1))
        if nx1 <= nx0:
            nx1 = min(lane_right + 1, nx0 + 2)

        if note_side_margin > 0:
            note_width = nx1 - nx0
            shrink_each_side = min(
                note_side_margin, max(0, (note_width - 2) // 2)
            )
            if shrink_each_side > 0:
                nx0 += shrink_each_side
                nx1 -= shrink_each_side

        note_top = int(round(lane_top_f + max(0, (lane_h - note_h) / 2.0)))
        note_bottom = min(int(round(lane_bottom_f)), note_top + note_h - 1)
        is_active = (start_beat <= now_beat < end_beat) and (
            event_index == current_event_index
        )
        fill = active_note_color if is_active else note_color
        radius = max(1, (note_bottom - note_top + 1) // 2)
        draw.rounded_rectangle(
            [nx0, note_top, nx1 - 1, note_bottom], radius=radius, fill=fill
        )

    # Draw playhead on top so the strike point is always clear.
    playhead_x = inner_x + int(
        round(inner_w * max(0.0, min(1.0, center_fraction)))
    )
    stroke = max(1, int(playhead_width_px))
    half_stroke = stroke // 2
    x0 = max(inner_x, playhead_x - half_stroke)
    x1 = min(inner_x + inner_w - 1, x0 + stroke - 1)
    draw.rectangle(
        [x0, inner_y, x1, inner_y + inner_h - 1],
        fill=playhead_color,
    )


def _split_lyric_display(raw_lyric: str) -> tuple[str, dict[str, set[int]]]:
    lines = raw_lyric.splitlines()
    parsed_lines: list[str] = []
    part_line_indices: dict[str, set[int]] = {
        label: set() for label in ("a", "b", "c")
    }
    current_part = "plain"

    for raw_line in lines:
        match = _LYRIC_PART_RE.match(raw_line)
        if match is not None:
            current_part = match.group(1).lower()
            parsed_line = match.group(2).lstrip()
            parsed_lines.append(parsed_line)
            part_line_indices.setdefault(current_part, set()).add(
                len(parsed_lines) - 1
            )
            continue

        parsed_line = raw_line.lstrip()
        parsed_lines.append(parsed_line)

        if current_part != "plain" and raw_line[:1].isspace():
            part_line_indices.setdefault(current_part, set()).add(
                len(parsed_lines) - 1
            )
            continue

        current_part = "plain"

    return "\n".join(parsed_lines), part_line_indices


def _event_display_text(event: dict[str, Any]) -> str:
    lyric = event.get("lyric")
    if isinstance(lyric, str) and lyric:
        display_text, _b_line_indices = _split_lyric_display(lyric)
        return display_text
    instruction = event.get("instruction")
    if isinstance(instruction, str) and instruction:
        return f"({instruction})"
    return ""


def _event_lyric_line_override_colors(
    event: dict[str, Any],
    split_b_color: tuple[int, int, int],
    *,
    split_a_color: tuple[int, int, int] | None = None,
    split_c_color: tuple[int, int, int] | None = None,
) -> dict[int, tuple[int, int, int]]:
    lyric = event.get("lyric")
    if not isinstance(lyric, str) or not lyric:
        return {}

    _display_text, part_line_indices = _split_lyric_display(lyric)
    colors_by_part = {
        "a": split_a_color,
        "b": split_b_color,
        "c": split_c_color,
    }
    overrides: dict[int, tuple[int, int, int]] = {}
    for part, line_indices in part_line_indices.items():
        color = colors_by_part.get(part)
        if color is None:
            continue
        for line_idx in line_indices:
            overrides[line_idx] = color
    return overrides


def _lyric_line_override_colors_for_all(
    events: list[dict[str, Any]],
    split_b_color: tuple[int, int, int],
    *,
    split_a_color: tuple[int, int, int] | None = None,
    split_c_color: tuple[int, int, int] | None = None,
) -> list[dict[int, tuple[int, int, int]]]:
    return [
        _event_lyric_line_override_colors(
            event,
            split_b_color,
            split_a_color=split_a_color,
            split_c_color=split_c_color,
        )
        for event in events
    ]


def _display_texts_for_all(events: list[dict[str, Any]]) -> list[str]:
    return [_event_display_text(event) for event in events]


def _next_display_change_indices(display_texts: list[str]) -> list[int | None]:
    out: list[int | None] = [None] * len(display_texts)
    for i, current in enumerate(display_texts):
        next_idx = None
        for j in range(i + 1, len(display_texts)):
            candidate = display_texts[j]
            if not candidate:
                continue
            if candidate != current:
                next_idx = j
                break
        out[i] = next_idx
    return out


def _display_set_start_indices(display_texts: list[str]) -> list[int]:
    starts: list[int] = [0] * len(display_texts)
    current_start = 0
    previous: str | None = None
    for i, text in enumerate(display_texts):
        if i == 0 or text != previous:
            current_start = i
        starts[i] = current_start
        previous = text
    return starts


def _effective_chords_for_all(events: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    current = ""
    for event in events:
        raw = event.get("chords")
        if isinstance(raw, str):
            if raw == "":
                current = ""
            else:
                current = raw
        out.append(current)
    return out


def _effective_riffs_for_all(events: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    current = ""
    for event in events:
        raw_riff = event.get("riff")
        if isinstance(raw_riff, str):
            if raw_riff == "":
                current = ""
            else:
                current = raw_riff
        elif "chords" in event and isinstance(event.get("chords"), str):
            # Explicit chord declarations switch out of riff mode unless a new
            # riff is provided on the same event.
            current = ""
        out.append(current)
    return out


def _effective_roots_for_all(
    events: list[dict[str, Any]], *, key_text: str | None = None
) -> list[str | None]:
    out: list[str | None] = []
    current_root: str | None = None
    for event in events:
        raw_root = event.get("root")
        if isinstance(raw_root, str) and raw_root.strip():
            current_root = raw_root.strip()
        elif "root" in event and event.get("root") is None:
            current_root = None
        elif current_root is None and key_text is not None:
            match = re.search(r"([A-Ga-g])\s*([#b♯♭]?)", key_text.strip())
            if match is not None:
                accidental = (
                    (match.group(2) or "").replace("♯", "#").replace("♭", "b")
                )
                current_root = f"{match.group(1).upper()}{accidental}"
        out.append(current_root)
    return out


def _first_chord_token(chords: str) -> str:
    parts = [
        part.strip()
        for part in re.split(r"\s*=>\s*|\s*,\s*|\s+", chords.strip())
        if part.strip()
    ]
    for part in parts:
        if part:
            # Treat leading wrapper brackets as grouping syntax in lookahead
            # patterns like "(Bm7, Am7) x2" and transition markers like
            # "A => D" where only the first chord should remain visible.
            cleaned = part.lstrip("([{")
            if cleaned != part:
                cleaned = cleaned.rstrip(")]}")
            if cleaned:
                return cleaned
    return ""


def _chord_tokens(chords: str) -> list[str]:
    return [
        part.strip()
        for part in re.split(r"\s*=>\s*|\s*,\s*|\s+", chords.strip())
        if part.strip()
    ]


def _next_chord_preview_token(next_event_chords: str | None) -> str:
    if next_event_chords is None:
        return ""
    return _first_chord_token(next_event_chords)


def _should_show_loop_indicator(
    current_chords: str, next_event_chords: str | None
) -> bool:
    if not current_chords.strip() or next_event_chords is None:
        return False
    return current_chords == next_event_chords


def _next_chord_event_indices(effective_chords: list[str]) -> list[int | None]:
    out: list[int | None] = [None] * len(effective_chords)
    for i in range(len(effective_chords)):
        out[i] = i + 1 if (i + 1) < len(effective_chords) else None
    return out


def _next_lookahead_event_indices(
    song_events: list[dict[str, Any]],
    effective_chords: list[str],
) -> list[int | None]:
    out: list[int | None] = [None] * len(effective_chords)
    for i, _current in enumerate(effective_chords):
        next_idx = None
        for j in range(i + 1, len(effective_chords)):
            raw_chords = song_events[j].get("chords")
            if isinstance(raw_chords, str):
                next_idx = j
                break
        out[i] = next_idx
    return out


def _next_non_empty_raw_chord_event_indices(
    song_events: list[dict[str, Any]],
) -> list[int | None]:
    out: list[int | None] = [None] * len(song_events)
    for i in range(len(song_events)):
        next_idx = None
        for j in range(i + 1, len(song_events)):
            raw_chords = song_events[j].get("chords")
            if isinstance(raw_chords, str) and raw_chords.strip():
                next_idx = j
                break
        out[i] = next_idx
    return out


def _first_non_empty_chord_index(effective_chords: list[str]) -> int | None:
    for idx, chords in enumerate(effective_chords):
        if chords.strip():
            return idx
    return None


def _has_non_empty_raw_chords(event: dict[str, Any]) -> bool:
    raw = event.get("chords")
    return isinstance(raw, str) and bool(raw.strip())


def _is_intro_instruction_event(event: dict[str, Any]) -> bool:
    instruction = event.get("instruction")
    return (
        isinstance(instruction, str) and instruction.strip().lower() == "intro"
    )


def _leading_intro_end_index(events: list[dict[str, Any]]) -> int:
    idx = 0
    while idx < len(events) and _is_intro_instruction_event(events[idx]):
        idx += 1
    return idx


def _first_non_empty_raw_chord_index_from(
    events: list[dict[str, Any]], start_index: int
) -> int | None:
    for idx in range(max(0, start_index), len(events)):
        if _has_non_empty_raw_chords(events[idx]):
            return idx
    return None


def _resolve_intro_lookahead_target(
    *,
    song_events: list[dict[str, Any]],
    effective_chords: list[str],
    event_starts_seconds: list[float],
    time_origin_seconds: float,
    beat_seconds: float,
    beat_times_seconds: list[float] | None,
    beat_anchor_index: int,
    beats_per_bar: int,
) -> tuple[int | None, float, bool]:
    first_non_empty_chord_idx = _first_non_empty_chord_index(effective_chords)
    if first_non_empty_chord_idx is None or not event_starts_seconds:
        return first_non_empty_chord_idx, 0.0, False

    reveal_chord_idx = _first_non_empty_raw_chord_index_from(song_events, 0)
    if reveal_chord_idx is None:
        reveal_chord_idx = first_non_empty_chord_idx

    first_event_time = time_origin_seconds + event_starts_seconds[0]
    reveal_time = time_origin_seconds + event_starts_seconds[reveal_chord_idx]

    starts_chordless = (
        not bool(effective_chords[0].strip())
        and reveal_time > first_event_time
    )

    return reveal_chord_idx, reveal_time, starts_chordless


def _should_hide_long_intro_lookahead(
    *,
    event_index: int,
    current_chords: str,
    first_non_empty_chord_index: int | None,
    long_intro_without_chords: bool,
    current_time: float,
    first_chord_time: float,
    beat_seconds: float,
    beat_times_seconds: list[float] | None,
    beat_anchor_index: int,
    beats_per_bar: int,
    intro_lookahead_lead_beats: float | None = None,
) -> bool:
    if first_non_empty_chord_index is None:
        return False
    if not long_intro_without_chords:
        return False
    if event_index >= first_non_empty_chord_index:
        return False

    now_beat_pos = _beat_position_at_time(
        current_time,
        beat_seconds=beat_seconds,
        beat_times_seconds=beat_times_seconds,
        beat_anchor_index=beat_anchor_index,
    )
    first_chord_beat_pos = _beat_position_at_time(
        first_chord_time,
        beat_seconds=beat_seconds,
        beat_times_seconds=beat_times_seconds,
        beat_anchor_index=beat_anchor_index,
    )
    beats_until_first_chord = first_chord_beat_pos - now_beat_pos
    if intro_lookahead_lead_beats is None:
        lead_beats = float(beats_per_bar)
    else:
        lead_beats = float(intro_lookahead_lead_beats)
    return beats_until_first_chord > max(0.001, lead_beats)


def _optional_positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


def _beats_until_target_time(
    *,
    current_time: float,
    target_time: float,
    beat_seconds: float,
    beat_times_seconds: list[float] | None,
    beat_anchor_index: int,
) -> float:
    current_beat_pos = _beat_position_at_time(
        current_time,
        beat_seconds=beat_seconds,
        beat_times_seconds=beat_times_seconds,
        beat_anchor_index=beat_anchor_index,
    )
    target_beat_pos = _beat_position_at_time(
        target_time,
        beat_seconds=beat_seconds,
        beat_times_seconds=beat_times_seconds,
        beat_anchor_index=beat_anchor_index,
    )
    return target_beat_pos - current_beat_pos


def _should_blank_next_chord_preview(
    *,
    current_chords: str,
    current_time: float,
    next_chord_time: float,
    show_within_beats: float | None,
    beat_seconds: float,
    beat_times_seconds: list[float] | None,
    beat_anchor_index: int,
) -> bool:
    # Only blank upcoming text when the current event is intentionally chordless.
    # If chords are active (including inherited chords), keep showing the next token.
    if current_chords.strip():
        return False
    if show_within_beats is None:
        return False
    beats_until_next_chord = _beats_until_target_time(
        current_time=current_time,
        target_time=next_chord_time,
        beat_seconds=beat_seconds,
        beat_times_seconds=beat_times_seconds,
        beat_anchor_index=beat_anchor_index,
    )
    return beats_until_next_chord > show_within_beats


def _should_show_explicit_silence_lookahead(
    *,
    current_time: float,
    next_event_time: float,
    show_within_beats: float | None,
    beat_seconds: float,
    beat_times_seconds: list[float] | None,
    beat_anchor_index: int,
) -> bool:
    # Explicit silence is itself meaningful lookahead: keep the countdown ring
    # visible for the full current event so players can time the release.
    _ = (
        current_time,
        next_event_time,
        show_within_beats,
        beat_seconds,
        beat_times_seconds,
        beat_anchor_index,
    )
    return True


def _last_non_empty_display_text(events: list[dict[str, Any]]) -> str:
    for event in reversed(events):
        text = _event_display_text(event)
        if text:
            return text
    return ""


def _chord_set_start_indices(effective_chords: list[str]) -> list[int]:
    starts: list[int] = [0] * len(effective_chords)
    current_start = 0
    previous = ""
    for i, chords in enumerate(effective_chords):
        if i == 0 or chords != previous:
            current_start = i
        starts[i] = current_start
        previous = chords
    return starts


def _beats_per_bar_from_timing(timing: dict[str, Any]) -> int:
    raw = timing.get("time_signature") if isinstance(timing, dict) else None
    if isinstance(raw, str) and "/" in raw:
        left = raw.split("/", 1)[0].strip()
        try:
            beats = int(left)
            if beats > 0:
                return beats
        except ValueError:
            pass
    return 4


def _draw_duration_indicator(
    draw: ImageDraw.ImageDraw,
    region: tuple[int, int, int, int],
    remaining_ratio: float,
    background_color: tuple[int, int, int],
    foreground_color: tuple[int, int, int],
) -> None:
    x, y, w, h = region
    size = min(w, h)
    cx = x + (w - size) // 2
    cy = y + (h - size) // 2
    bbox = [cx, cy, cx + size - 1, cy + size - 1]

    draw.ellipse(bbox, fill=foreground_color, outline=foreground_color)
    remaining = max(0.0, min(1.0, remaining_ratio))
    elapsed = 1.0 - remaining
    if elapsed > 0:
        draw.pieslice(
            bbox,
            start=-90,
            end=-90 + int(round(elapsed * 360.0)),
            fill=background_color,
        )


def _draw_progress_bar_at_y(
    draw: ImageDraw.ImageDraw,
    region: tuple[int, int, int, int],
    *,
    center_y: int,
    progress: float,
    width_ratio: float = 0.6,
    height_px: int = 48,
    border_width_px: int = 3,
) -> None:
    x, y, w, _h = region
    bar_w = max(1, int(round(w * max(0.0, min(1.0, width_ratio)))))
    bar_h = max(1, int(height_px))

    bar_left = x + max(0, (w - bar_w) // 2)
    bar_top = center_y - (bar_h // 2)
    bar_right = bar_left + bar_w - 1
    bar_bottom = bar_top + bar_h - 1

    border_color = (255, 255, 255, 255)
    draw.rectangle(
        [bar_left, bar_top, bar_right, bar_bottom],
        outline=border_color,
        width=max(1, int(border_width_px)),
    )

    inset = max(2, int(border_width_px) + 1)
    fill_left = bar_left + inset
    fill_top = bar_top + inset
    fill_right_max = bar_right - inset
    fill_bottom = bar_bottom - inset
    fill_track_width = max(0, fill_right_max - fill_left + 1)

    clamped_progress = max(0.0, min(1.0, progress))
    fill_width = int(round(fill_track_width * clamped_progress))
    if fill_width > 0 and fill_top <= fill_bottom:
        draw.rectangle(
            [
                fill_left,
                fill_top,
                fill_left + fill_width - 1,
                fill_bottom,
            ],
            fill=(255, 255, 255, 230),
        )


def _draw_beat_pulse_ring(
    image: Image.Image,
    region: tuple[int, int, int, int],
    *,
    beat_remaining_ratio: float,
    color: tuple[int, int, int],
    max_expand_px: int,
    ring_width_px: int,
) -> None:
    remaining = max(0.0, min(1.0, beat_remaining_ratio))
    if remaining <= 0.0:
        return

    elapsed = 1.0 - remaining
    expansion = int(round(max(0, max_expand_px) * elapsed))
    alpha = int(round(255.0 * remaining))
    if alpha <= 0:
        return

    x, y, w, h = region
    size = min(w, h)
    cx = x + (w - size) // 2
    cy = y + (h - size) // 2
    bbox = [
        cx - expansion,
        cy - expansion,
        cx + size - 1 + expansion,
        cy + size - 1 + expansion,
    ]

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.ellipse(
        bbox,
        outline=(color[0], color[1], color[2], alpha),
        width=max(1, ring_width_px),
    )
    image.paste(overlay, (0, 0), overlay)


def _cycle_remaining_ratio(
    *,
    current_time: float,
    cycle_start: float,
    cycle_end: float,
) -> float:
    duration = max(0.001, cycle_end - cycle_start)
    progress = (current_time - cycle_start) / duration
    return max(0.0, min(1.0, 1.0 - progress))


def _beat_cycle_remaining_ratio(
    *,
    current_time: float,
    cycle_start: float,
    cycle_end: float,
    beat_seconds: float,
    beat_times_seconds: list[float] | None,
    beat_anchor_index: int,
) -> float:
    if beat_times_seconds:
        total_beats = _beats_until_target_time(
            current_time=cycle_start,
            target_time=cycle_end,
            beat_seconds=beat_seconds,
            beat_times_seconds=beat_times_seconds,
            beat_anchor_index=beat_anchor_index,
        )
        if total_beats > 0.0:
            beats_remaining = _beats_until_target_time(
                current_time=current_time,
                target_time=cycle_end,
                beat_seconds=beat_seconds,
                beat_times_seconds=beat_times_seconds,
                beat_anchor_index=beat_anchor_index,
            )
            return max(0.0, min(1.0, beats_remaining / total_beats))

    return _cycle_remaining_ratio(
        current_time=current_time, cycle_start=cycle_start, cycle_end=cycle_end
    )


def _draw_phrase_countdown_ring(
    image: Image.Image,
    region: tuple[int, int, int, int],
    *,
    remaining_ratio: float,
    color: tuple[int, int, int],
    ring_width_px: int,
    padding_px: int,
    size_ratio: float,
    dotted: bool,
    dash_degrees: float,
    gap_degrees: float,
) -> None:
    remaining = max(0.0, min(1.0, remaining_ratio))
    if remaining <= 0.0:
        return

    x, y, w, h = region
    base_size = min(w, h)
    # Allow values >1.0 so callers can place the dotted ring outside
    # the icon/text footprint.
    size = max(1, int(round(base_size * max(0.1, min(3.0, size_ratio)))))
    size = max(1, size - (2 * max(0, int(padding_px))))
    cx = x + (w - size) // 2
    cy = y + (h - size) // 2
    bbox = [cx, cy, cx + size - 1, cy + size - 1]

    start_angle = -90 - int(round(remaining * 360.0))
    end_angle = -90
    if start_angle >= end_angle:
        return

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    stroke_width = max(1, int(ring_width_px))
    if dotted:
        dash_span = max(1.0, float(dash_degrees))
        gap_span = max(0.0, float(gap_degrees))
        pattern_start = -450.0
        pattern_end = -90.0
        cursor = pattern_start
        while cursor < pattern_end:
            segment_end = min(pattern_end, cursor + dash_span)
            visible_start = max(float(start_angle), cursor)
            visible_end = min(float(end_angle), segment_end)
            if visible_end > visible_start:
                overlay_draw.arc(
                    bbox,
                    start=visible_start,
                    end=visible_end,
                    fill=(color[0], color[1], color[2], 255),
                    width=stroke_width,
                )
            cursor = segment_end + gap_span
    else:
        overlay_draw.arc(
            bbox,
            start=start_angle,
            end=end_angle,
            fill=(color[0], color[1], color[2], 255),
            width=stroke_width,
        )
    image.paste(overlay, (0, 0), overlay)


def _measure_centered_text_height(
    draw: ImageDraw.ImageDraw,
    text: str,
    region: tuple[int, int, int, int],
    style: dict[str, Any],
) -> int:
    if not text:
        return 0

    font = _fit_font_for_region(draw, text, style, region)
    align = str(style.get("align", "center"))
    bbox = draw.multiline_textbbox((0, 0), text, font=font, align=align)
    return max(0, bbox[3] - bbox[1])


def _compute_preview_scroll_metrics(
    *,
    current_center_y: int,
    preview_center_y: int,
    current_text_height: int,
    next_text_height: int,
    base_scroll_window_seconds: float,
    seconds_until_next_lyric: float,
    preview_margin_px: int,
) -> tuple[int, float]:
    base_delta_px = max(1.0, float(preview_center_y - current_center_y))
    base_window_seconds = max(0.001, float(base_scroll_window_seconds))
    base_speed_px_per_second = base_delta_px / base_window_seconds

    required_preview_center = preview_center_y
    overlap_threshold = (
        current_center_y
        + (max(0, int(current_text_height)) / 2.0)
        + max(0, int(preview_margin_px))
        + (max(0, int(next_text_height)) / 2.0)
    )
    if overlap_threshold > required_preview_center:
        required_preview_center = int(round(overlap_threshold))

    required_delta_px = max(
        1.0, float(required_preview_center - current_center_y)
    )
    required_window_seconds = required_delta_px / base_speed_px_per_second
    seconds_available = max(0.001, float(seconds_until_next_lyric))
    effective_window_seconds = min(
        seconds_available, max(base_window_seconds, required_window_seconds)
    )

    return required_preview_center, effective_window_seconds


def _beat_pulse_color_for_index(
    bar_phase_index: int,
    *,
    beats_per_bar: int,
    first_beat_color: tuple[int, int, int],
    non_first_beat_color: tuple[int, int, int],
) -> tuple[int, int, int]:
    if bar_phase_index == 0:
        return first_beat_color
    return non_first_beat_color


def _resolve_event_phrase_structure(
    *,
    song_events: list[dict[str, Any]],
    default_beats_per_phrase: int,
    event_beats_per_phrase: list[int],
) -> tuple[list[int], list[int]]:
    """Return per-event (base_phrase_beats, active_extra_beats)."""
    base_by_event: list[int] = []
    active_extra_by_event: list[int] = []

    base_beats = max(1, int(default_beats_per_phrase))

    for idx, event in enumerate(song_events):
        raw_base = event.get("beats_per_phrase")
        if raw_base == "":
            base_beats = max(1, int(default_beats_per_phrase))
        elif raw_base is None:
            pass
        else:
            try:
                parsed_base = int(raw_base)
            except (TypeError, ValueError):
                parsed_base = base_beats
            if parsed_base > 0:
                base_beats = parsed_base

        base_for_event = base_beats
        resolved_total = max(1, int(event_beats_per_phrase[idx]))
        raw_extra = event.get("extra_beats")
        if raw_extra is None:
            raw_extra = event.get("beats_per_phrase_extra")
        if raw_extra not in (None, ""):
            try:
                parsed_extra = int(raw_extra)
            except (TypeError, ValueError):
                parsed_extra = 0
            if parsed_extra >= 0:
                base_for_event = max(1, resolved_total - parsed_extra)

        base_by_event.append(base_for_event)
        active_extra_by_event.append(max(0, resolved_total - base_for_event))

    return base_by_event, active_extra_by_event


def _cycle_progress_for_phrase_position(
    *,
    phrase_beats_into: float,
    base_phrase_beats: int,
    extra_beats: int,
    beats_per_bar: int,
) -> tuple[float, int]:
    """Return (cycle_beats_into, cycle_len_beats) for the active bar-cycle.

    If extra beats are configured and base phrase length spans multiple bars,
    the final bar is extended while prior bars stay at beats_per_bar.
    """
    beats_in_bar = max(1, int(beats_per_bar))
    base_beats = max(1, int(base_phrase_beats))
    extra = max(0, int(extra_beats))
    total_beats = max(1, base_beats + extra)
    beats_into = phrase_beats_into % total_beats

    if (
        extra <= 0
        or base_beats <= beats_in_bar
        or (base_beats % beats_in_bar) != 0
    ):
        return beats_into % beats_in_bar, beats_in_bar

    full_bars = max(1, base_beats // beats_in_bar)
    leading_bars = max(0, full_bars - 1)
    leading_span = leading_bars * beats_in_bar
    if beats_into < leading_span:
        return beats_into % beats_in_bar, beats_in_bar

    return beats_into - leading_span, beats_in_bar + extra


def _phrase_beat_index_for_beat(
    beat_index: int,
    *,
    phrase_start_beat: int,
    phrase_len_beats: int,
) -> int:
    length = max(1, int(phrase_len_beats))
    return (beat_index - phrase_start_beat) % length


def _median_positive_delta(values: list[float]) -> float | None:
    deltas = [b - a for a, b in zip(values, values[1:]) if b - a > 0]
    if not deltas:
        return None
    ordered = sorted(deltas)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _beat_position_at_time(
    current_time: float,
    *,
    beat_seconds: float,
    beat_times_seconds: list[float] | None,
    beat_anchor_index: int,
) -> float:
    beat_index, remaining_ratio, _seconds_to_next = _beat_state_at_time(
        current_time,
        beat_seconds=beat_seconds,
        beat_times_seconds=beat_times_seconds,
        beat_anchor_index=beat_anchor_index,
    )
    return beat_index + (1.0 - remaining_ratio)


def _instruction_progress_mode_for_event(
    instruction_key: str,
    *,
    window_start_seconds: float,
    window_end_seconds: float,
    beat_seconds: float,
    beat_times_seconds: list[float] | None,
    beat_anchor_index: int,
    beats_per_bar: int,
    min_break_beats: float = 16.0,
    min_outro_beats: float = 8.0,
    min_intro_bars: float = 8.0,
) -> bool:
    if instruction_key not in {"intro", "break", "outro"}:
        return False

    # Keep all three instruction types on one consistent threshold rule.
    _ = (beats_per_bar, min_outro_beats, min_intro_bars)

    start_beat_pos = _beat_position_at_time(
        window_start_seconds,
        beat_seconds=beat_seconds,
        beat_times_seconds=beat_times_seconds,
        beat_anchor_index=beat_anchor_index,
    )
    end_beat_pos = _beat_position_at_time(
        window_end_seconds,
        beat_seconds=beat_seconds,
        beat_times_seconds=beat_times_seconds,
        beat_anchor_index=beat_anchor_index,
    )
    duration_beats = max(0.0, end_beat_pos - start_beat_pos)
    return duration_beats > max(0.0, min_break_beats)


def _next_lyric_indices(events: list[dict[str, Any]]) -> list[int | None]:
    out: list[int | None] = [None] * len(events)
    next_lyric_idx: int | None = None

    for i in range(len(events) - 1, -1, -1):
        out[i] = next_lyric_idx
        lyric = events[i].get("lyric")
        if isinstance(lyric, str) and lyric.strip():
            next_lyric_idx = i

    return out


def _next_riff_indices(effective_riffs: list[str]) -> list[int | None]:
    out: list[int | None] = [None] * len(effective_riffs)
    next_riff_idx: int | None = None

    for i in range(len(effective_riffs) - 1, -1, -1):
        out[i] = next_riff_idx
        if effective_riffs[i].strip():
            next_riff_idx = i

    return out


def _instruction_progress_windows(
    events: list[dict[str, Any]],
    event_starts_seconds: list[float],
    tail_seconds: float,
) -> list[tuple[str, float, float] | None]:
    windows: list[tuple[str, float, float] | None] = [None] * len(events)

    def _instruction_key(event: dict[str, Any]) -> str | None:
        lyric = event.get("lyric")
        if isinstance(lyric, str) and lyric.strip():
            return None
        instruction = event.get("instruction")
        if not isinstance(instruction, str):
            return None
        key = instruction.strip().lower()
        if key in {"intro", "break", "outro"}:
            return key
        return None

    i = 0
    while i < len(events):
        key = _instruction_key(events[i])
        if key is None:
            i += 1
            continue

        j = i + 1
        while j < len(events) and _instruction_key(events[j]) == key:
            j += 1

        window_start = event_starts_seconds[i]
        if j < len(event_starts_seconds):
            window_end = event_starts_seconds[j]
        else:
            window_end = event_starts_seconds[-1] + tail_seconds

        for k in range(i, j):
            windows[k] = (key, window_start, window_end)
        i = j

    return windows


def _beat_state_at_time(
    current_time: float,
    *,
    beat_seconds: float,
    beat_times_seconds: list[float] | None,
    beat_anchor_index: int,
) -> tuple[int, float, float]:
    fallback_interval = max(0.001, beat_seconds)

    if beat_times_seconds:
        median_interval = _median_positive_delta(beat_times_seconds)
        if median_interval is not None:
            fallback_interval = max(0.001, median_interval)

        idx = bisect_right(beat_times_seconds, current_time) - 1
        if idx < 0:
            first_t = beat_times_seconds[0]
            beats_before_first = max(
                1,
                int(
                    (max(0.0, first_t - current_time) / fallback_interval)
                    + 0.999999
                ),
            )
            raw_beat_index = -beats_before_first
            next_t = first_t - ((beats_before_first - 1) * fallback_interval)
            prev_t = next_t - fallback_interval
        elif idx >= len(beat_times_seconds) - 1:
            offset = max(0.0, current_time - beat_times_seconds[-1])
            extra_beats = int(offset // fallback_interval)
            raw_beat_index = (len(beat_times_seconds) - 1) + extra_beats
            prev_t = beat_times_seconds[-1] + (extra_beats * fallback_interval)
            next_t = prev_t + fallback_interval
        else:
            raw_beat_index = idx
            prev_t = beat_times_seconds[idx]
            next_t = beat_times_seconds[idx + 1]
    else:
        raw_beat_index = int(max(0.0, current_time) / fallback_interval)
        prev_t = raw_beat_index * fallback_interval
        next_t = prev_t + fallback_interval

    interval = max(0.001, next_t - prev_t)
    seconds_to_next_beat = max(0.0, next_t - current_time)
    remaining_ratio = max(0.0, min(1.0, seconds_to_next_beat / interval))
    beat_index = raw_beat_index + beat_anchor_index
    return beat_index, remaining_ratio, seconds_to_next_beat


def _bar_cycle_state_from_beat_state(
    beat_index: int,
    beat_fraction_elapsed: float,
    *,
    beats_per_bar: int,
) -> tuple[float, int]:
    beats_in_bar = max(1, int(beats_per_bar))
    cycle_beats_into = (beat_index % beats_in_bar) + beat_fraction_elapsed
    return cycle_beats_into, beats_in_bar


def _canonicalize_detected_beats_per_bar(
    raw_beats_per_bar: int,
    *,
    fallback_beats_per_bar: int,
) -> int:
    beats_in_bar = max(1, int(raw_beats_per_bar))
    fallback = max(1, int(fallback_beats_per_bar))
    if beats_in_bar < 1 or beats_in_bar > 16:
        return fallback
    return beats_in_bar


def _beats_per_bar_at_time(
    current_time: float,
    *,
    beat_seconds: float,
    beat_times_seconds: list[float] | None,
    beat_anchor_index: int,
    bar_start_times_seconds: list[float] | None,
    fallback_beats_per_bar: int,
) -> int:
    if not bar_start_times_seconds or len(bar_start_times_seconds) < 2:
        return max(1, int(fallback_beats_per_bar))

    bars = sorted(float(t) for t in bar_start_times_seconds)
    idx = bisect_right(bars, current_time) - 1
    if idx < 0:
        idx = 0
    if idx >= len(bars) - 1:
        idx = len(bars) - 2

    start = bars[idx]
    end = bars[idx + 1]
    start_pos = _beat_position_at_time(
        start,
        beat_seconds=beat_seconds,
        beat_times_seconds=beat_times_seconds,
        beat_anchor_index=beat_anchor_index,
    )
    end_pos = _beat_position_at_time(
        end,
        beat_seconds=beat_seconds,
        beat_times_seconds=beat_times_seconds,
        beat_anchor_index=beat_anchor_index,
    )
    raw_beats_in_bar = int(round(max(1.0, end_pos - start_pos)))
    if raw_beats_in_bar < 1 or raw_beats_in_bar > 16:
        return max(1, int(fallback_beats_per_bar))
    return _canonicalize_detected_beats_per_bar(
        raw_beats_in_bar,
        fallback_beats_per_bar=fallback_beats_per_bar,
    )


def _bar_cycle_state_at_time(
    current_time: float,
    *,
    beat_seconds: float,
    beat_times_seconds: list[float] | None,
    beat_anchor_index: int,
    bar_start_times_seconds: list[float] | None,
    fallback_beats_per_bar: int,
) -> tuple[float, int]:
    if not bar_start_times_seconds or len(bar_start_times_seconds) < 2:
        beat_index, beat_remaining_ratio, _seconds_to_next_beat = (
            _beat_state_at_time(
                current_time,
                beat_seconds=beat_seconds,
                beat_times_seconds=beat_times_seconds,
                beat_anchor_index=beat_anchor_index,
            )
        )
        beat_fraction_elapsed = 1.0 - beat_remaining_ratio
        return _bar_cycle_state_from_beat_state(
            beat_index,
            beat_fraction_elapsed,
            beats_per_bar=fallback_beats_per_bar,
        )

    bars = sorted(float(t) for t in bar_start_times_seconds)
    idx = bisect_right(bars, current_time) - 1
    if idx < 0:
        idx = 0
    if idx >= len(bars) - 1:
        idx = len(bars) - 2

    start = bars[idx]
    beats_in_bar = _beats_per_bar_at_time(
        current_time,
        beat_seconds=beat_seconds,
        beat_times_seconds=beat_times_seconds,
        beat_anchor_index=beat_anchor_index,
        bar_start_times_seconds=bars,
        fallback_beats_per_bar=fallback_beats_per_bar,
    )

    start_beat_index, start_remaining_ratio, _seconds_to_next_start = (
        _beat_state_at_time(
            start,
            beat_seconds=beat_seconds,
            beat_times_seconds=beat_times_seconds,
            beat_anchor_index=beat_anchor_index,
        )
    )
    current_beat_index, current_remaining_ratio, _seconds_to_next_current = (
        _beat_state_at_time(
            current_time,
            beat_seconds=beat_seconds,
            beat_times_seconds=beat_times_seconds,
            beat_anchor_index=beat_anchor_index,
        )
    )

    start_beat_pos = start_beat_index + (1.0 - start_remaining_ratio)
    current_beat_pos = current_beat_index + (1.0 - current_remaining_ratio)
    cycle_beats_into = current_beat_pos - start_beat_pos
    if cycle_beats_into < 0.0:
        cycle_beats_into = 0.0
    if cycle_beats_into > beats_in_bar:
        cycle_beats_into = float(beats_in_bar)
    return cycle_beats_into, beats_in_bar


def _beat_time_at_index(
    beat_index: int,
    *,
    beat_seconds: float,
    beat_times_seconds: list[float] | None,
    beat_anchor_index: int,
) -> float:
    raw_beat_index = beat_index - beat_anchor_index
    fallback_interval = max(0.001, beat_seconds)
    if beat_times_seconds:
        median_interval = _median_positive_delta(beat_times_seconds)
        if median_interval is not None:
            fallback_interval = max(0.001, median_interval)

        if raw_beat_index < 0:
            return beat_times_seconds[0] + (raw_beat_index * fallback_interval)
        if raw_beat_index < len(beat_times_seconds):
            return beat_times_seconds[raw_beat_index]

        extra_beats = raw_beat_index - (len(beat_times_seconds) - 1)
        return beat_times_seconds[-1] + (extra_beats * fallback_interval)

    return raw_beat_index * fallback_interval


def _event_times_for_beat_math(
    event_starts_seconds: list[float],
    beat_time_offsets_seconds: list[float] | None = None,
) -> list[float]:
    if beat_time_offsets_seconds is None:
        return list(event_starts_seconds)
    if len(beat_time_offsets_seconds) != len(event_starts_seconds):
        return list(event_starts_seconds)
    return [
        float(event_starts_seconds[idx])
        + float(beat_time_offsets_seconds[idx])
        for idx in range(len(event_starts_seconds))
    ]


def _resolve_event_beat_anchor_indices(
    *,
    song_events: list[dict[str, Any]],
    event_starts_seconds: list[float],
    beat_seconds: float,
    beat_times_seconds: list[float] | None,
    default_beat_anchor_index: int,
    event_beats_per_bar: list[int] | None = None,
) -> list[int]:
    """
    Return per-event beat anchors, honoring reset_beat from each reset event onward.
    """
    anchors: list[int] = []
    current_anchor = int(default_beat_anchor_index)
    current_beats_per_bar = None

    for idx, event in enumerate(song_events):
        beats_per_bar_for_event = None
        if event_beats_per_bar is not None and idx < len(event_beats_per_bar):
            beats_per_bar_for_event = max(1, int(event_beats_per_bar[idx]))

        if event.get("reset_beat") is True:
            raw_beat_index, _remaining_ratio, _seconds_to_next = (
                _beat_state_at_time(
                    event_starts_seconds[idx],
                    beat_seconds=beat_seconds,
                    beat_times_seconds=beat_times_seconds,
                    beat_anchor_index=0,
                )
            )
            current_anchor = -raw_beat_index
            current_beats_per_bar = beats_per_bar_for_event
        elif beats_per_bar_for_event is not None:
            if current_beats_per_bar is None:
                current_beats_per_bar = beats_per_bar_for_event
            elif beats_per_bar_for_event != current_beats_per_bar:
                raw_beat_index, _remaining_ratio, _seconds_to_next = (
                    _beat_state_at_time(
                        event_starts_seconds[idx],
                        beat_seconds=beat_seconds,
                        beat_times_seconds=beat_times_seconds,
                        beat_anchor_index=0,
                    )
                )
                current_anchor = (-raw_beat_index) % beats_per_bar_for_event
                current_beats_per_bar = beats_per_bar_for_event
        anchors.append(current_anchor)

    return anchors


def _event_resets_phrase_clock(event: dict[str, Any]) -> bool:
    if event.get("reset_beat") is True:
        return True
    if "beats_per_phrase" in event:
        return True
    if "extra_beats" in event:
        return True
    if "beats_per_phrase_extra" in event:
        return True
    return False


def _resolve_event_phrase_fraction_offsets(
    *,
    song_events: list[dict[str, Any]],
    event_starts_seconds: list[float],
    beat_seconds: float,
    beat_times_seconds: list[float] | None,
    event_beat_anchor_indices: list[int],
) -> list[float]:
    """Return per-event phase offsets so reset_beat starts phrase clock at 0.0."""
    offsets: list[float] = []
    current_offset = 0.0

    for idx, event in enumerate(song_events):
        if _event_resets_phrase_clock(event):
            _beat_index, remaining_ratio, _seconds_to_next = (
                _beat_state_at_time(
                    event_starts_seconds[idx],
                    beat_seconds=beat_seconds,
                    beat_times_seconds=beat_times_seconds,
                    beat_anchor_index=event_beat_anchor_indices[idx],
                )
            )
            current_offset = max(0.0, min(1.0, 1.0 - remaining_ratio))
        offsets.append(current_offset)

    return offsets


def _resolve_phrase_profile_for_events(
    *,
    song_events: list[dict[str, Any]],
    event_starts_seconds: list[float],
    default_beats_per_phrase: int,
    beat_seconds: float,
    beat_times_seconds: list[float] | None,
    beat_anchor_index: int,
    event_beat_anchor_indices: list[int] | None = None,
    phase_offset_for_resets: int = 0,
    beats_per_bar: int | list[int] = 4,
) -> tuple[list[int], list[int]]:
    event_beats_per_phrase: list[int] = []
    event_phrase_start_beats: list[int] = []

    base_beats_per_phrase = max(1, int(default_beats_per_phrase))
    current_beats_per_phrase = base_beats_per_phrase
    current_phrase_start_beat = 0

    def _event_phrase_offset_seconds(event: dict[str, Any]) -> float:
        raw = event.get("beats_per_phrase_offset_seconds", 0.0)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0.0

    def _event_beat_position(
        time_seconds: float, *, anchor_index: int
    ) -> float:
        beat_index, remaining_ratio, _seconds_to_next = _beat_state_at_time(
            time_seconds,
            beat_seconds=beat_seconds,
            beat_times_seconds=beat_times_seconds,
            beat_anchor_index=anchor_index,
        )
        return beat_index + (1.0 - remaining_ratio)

    for idx, event in enumerate(song_events):
        if isinstance(beats_per_bar, list):
            beats_per_bar_for_event = (
                beats_per_bar[idx]
                if idx < len(beats_per_bar)
                else beats_per_bar[-1]
            )
        else:
            beats_per_bar_for_event = beats_per_bar
        anchor_for_event = (
            event_beat_anchor_indices[idx]
            if event_beat_anchor_indices is not None
            else beat_anchor_index
        )
        event_start = event_starts_seconds[idx]
        event_start_beat, _, _ = _beat_state_at_time(
            event_start,
            beat_seconds=beat_seconds,
            beat_times_seconds=beat_times_seconds,
            beat_anchor_index=anchor_for_event,
        )
        event_start_raw_beat = event_start_beat - anchor_for_event

        # Advance phrase boundaries using the active phrase length. This lets
        # one-shot extra_beats extend only the current phrase, then revert.
        while event_start_beat >= (
            current_phrase_start_beat + current_beats_per_phrase
        ):
            current_phrase_start_beat += current_beats_per_phrase
            current_beats_per_phrase = base_beats_per_phrase

        event_reset_start = event_start + _event_phrase_offset_seconds(event)
        event_reset_start_beat, _, _ = _beat_state_at_time(
            event_reset_start,
            beat_seconds=beat_seconds,
            beat_times_seconds=beat_times_seconds,
            beat_anchor_index=anchor_for_event,
        )
        event_reset_start_raw_beat = event_reset_start_beat - anchor_for_event

        if idx == 0:
            # Anchor phrase math to raw beat-space (without beat_offset) so
            # manual beat_offset still shifts displayed phase as intended.
            current_phrase_start_beat = event_start_raw_beat

        if idx > 0 and event.get("reset_beat") is True:
            # reset_beat should visibly restart the beat/phrase clock at this
            # event boundary, so anchor phrase origin in beat-index space here.
            current_phrase_start_beat = (
                event_start_beat + phase_offset_for_resets
            )
            current_beats_per_phrase = base_beats_per_phrase

        if (
            idx > 0
            and _event_resets_phrase_clock(event)
            and event.get("reset_beat") is not True
        ):
            # Explicit phrase-shape controls (beats_per_phrase / extra_beats)
            # start a fresh phrase clock at this event boundary.
            current_phrase_start_beat = (
                event_start_beat + phase_offset_for_resets
            )
            current_beats_per_phrase = base_beats_per_phrase

        raw = event.get("beats_per_phrase")
        if raw is None:
            pass
        elif raw == "":
            base_beats_per_phrase = max(1, int(default_beats_per_phrase))
            if idx == 0:
                current_phrase_start_beat = event_reset_start_raw_beat
            else:
                current_phrase_start_beat = (
                    event_reset_start_raw_beat + phase_offset_for_resets
                )
            current_beats_per_phrase = base_beats_per_phrase
        else:
            try:
                parsed = int(raw)
            except (TypeError, ValueError):
                parsed = base_beats_per_phrase
            if parsed > 0:
                base_beats_per_phrase = parsed
                if idx == 0:
                    current_phrase_start_beat = event_reset_start_raw_beat
                else:
                    current_phrase_start_beat = (
                        event_reset_start_raw_beat + phase_offset_for_resets
                    )
                current_beats_per_phrase = base_beats_per_phrase

        raw_extra = event.get("extra_beats")
        if raw_extra is None:
            raw_extra = event.get("beats_per_phrase_extra")
        if raw_extra == "":
            current_beats_per_phrase = base_beats_per_phrase
        elif raw_extra is None:
            pass
        else:
            try:
                parsed_extra = int(raw_extra)
            except (TypeError, ValueError):
                parsed_extra = 0
            if parsed_extra >= 0:
                base_for_extended_phrase = base_beats_per_phrase
                has_explicit_base_override = event.get(
                    "beats_per_phrase"
                ) not in (None, "")
                if (
                    not has_explicit_base_override
                    and (idx + 1) < len(event_starts_seconds)
                    and parsed_extra > 0
                ):
                    event_end = event_starts_seconds[idx + 1]
                    start_pos = _event_beat_position(
                        event_start, anchor_index=anchor_for_event
                    )
                    end_pos = _event_beat_position(
                        event_end, anchor_index=anchor_for_event
                    )
                    span_beats = max(1.0, end_pos - start_pos)
                    inferred_base = max(1.0, span_beats - parsed_extra)
                    beats_in_bar = max(1, int(beats_per_bar_for_event))
                    inferred_bars = max(
                        1, int(round(inferred_base / beats_in_bar))
                    )
                    base_for_extended_phrase = inferred_bars * beats_in_bar

                current_beats_per_phrase = max(
                    1, base_for_extended_phrase + parsed_extra
                )

        event_beats_per_phrase.append(current_beats_per_phrase)
        event_phrase_start_beats.append(current_phrase_start_beat)

    return event_beats_per_phrase, event_phrase_start_beats


def _resolve_phrase_reset_hold_seconds(
    *,
    song_events: list[dict[str, Any]],
    event_starts_seconds: list[float],
    beat_seconds: float,
    beat_times_seconds: list[float] | None,
    beat_anchor_index: int,
    event_beat_anchor_indices: list[int] | None = None,
    default_beats_per_phrase: int,
) -> list[float | None]:
    """
    Return per-event hold duration so changed phrase sections can visibly start at 1.1.
    """
    hold_seconds: list[float | None] = [None] * len(song_events)

    base_beats_per_phrase = max(1, int(default_beats_per_phrase))
    current_extra_beats = 0
    current_beats_per_phrase = base_beats_per_phrase

    def _event_phrase_offset_seconds(event: dict[str, Any]) -> float:
        raw = event.get("beats_per_phrase_offset_seconds", 0.0)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0.0

    for idx, event in enumerate(song_events):
        if idx > 0 and event.get("reset_beat") is True:
            current_extra_beats = 0

        raw = event.get("beats_per_phrase")
        changed = False

        if raw == "":
            base_beats_per_phrase = max(1, int(default_beats_per_phrase))
        elif raw is None:
            pass
        else:
            try:
                parsed = int(raw)
            except (TypeError, ValueError):
                parsed = base_beats_per_phrase
            if parsed > 0:
                base_beats_per_phrase = parsed

        raw_extra = event.get("extra_beats")
        if raw_extra is None:
            raw_extra = event.get("beats_per_phrase_extra")
        if raw_extra is None:
            pass
        elif raw_extra == "":
            current_extra_beats = 0
        else:
            try:
                parsed_extra = int(raw_extra)
            except (TypeError, ValueError):
                parsed_extra = current_extra_beats
            if parsed_extra >= 0:
                current_extra_beats = parsed_extra

        new_beats_per_phrase = max(
            1, base_beats_per_phrase + current_extra_beats
        )
        changed = new_beats_per_phrase != current_beats_per_phrase
        current_beats_per_phrase = new_beats_per_phrase

        if idx == 0 or not changed:
            continue

        anchor_for_event = (
            event_beat_anchor_indices[idx]
            if event_beat_anchor_indices is not None
            else beat_anchor_index
        )
        event_reset_start = event_starts_seconds[
            idx
        ] + _event_phrase_offset_seconds(event)
        _beat_index, _remaining_ratio, seconds_to_next_beat = (
            _beat_state_at_time(
                event_reset_start,
                beat_seconds=beat_seconds,
                beat_times_seconds=beat_times_seconds,
                beat_anchor_index=anchor_for_event,
            )
        )
        hold_seconds[idx] = max(0.0, seconds_to_next_beat)

    return hold_seconds


def render_cue_frames(
    *,
    output_dir: Path,
    width: int,
    height: int,
    fps: int,
    background_color: str,
    song_events: list[dict[str, Any]],
    event_starts_seconds: list[float],
    tail_seconds: float,
    layout_data: dict[str, Any],
    beat_seconds: float,
    beats_per_bar: int,
    event_beats_per_bar: list[int] | None = None,
    default_beats_per_phrase: int,
    beat_anchor_index: int,
    phrase_anchor_index: int | None = None,
    beat_times_seconds: list[float] | None = None,
    bar_start_times_seconds: list[float] | None = None,
    beat_time_offsets_seconds: list[float] | None = None,
    hold_reset_until_next_beat: bool = False,
    keep_postlude_beat_clock: bool = False,
    lyric_lead_in_beats: float = 4.0,
    intro_lookahead_lead_beats: float | None = None,
    time_origin_seconds: float = 0.0,
    postlude_seconds: float = 0.0,
    riff_key_text: str = "",
    background_frame_provider: Callable[[], Image.Image] | None = None,
    background_is_transparent: bool = False,
    frame_sink: Callable[[Image.Image, int], None] | None = None,
    frame_progress_callback: Callable[[], None] | None = None,
) -> dict[str, Any]:
    if frame_sink is None:
        output_dir.mkdir(parents=True, exist_ok=True)

    regions = (
        layout_data.get("regions")
        if isinstance(layout_data.get("regions"), dict)
        else {}
    )
    typography = (
        layout_data.get("typography")
        if isinstance(layout_data.get("typography"), dict)
        else {}
    )

    lyric_region = _region_to_px(
        regions.get(
            "lyric_main", {"x": 0.08, "y": 0.0, "width": 0.84, "height": 0.98}
        ),
        width,
        height,
    )
    chord_region = _region_to_px(
        regions.get(
            "chord_main", {"x": 0.18, "y": 0.78, "width": 0.38, "height": 0.13}
        ),
        width,
        height,
    )
    next_chord_region = _region_to_px(
        regions.get(
            "next_chord", {"x": 0.66, "y": 0.77, "width": 0.12, "height": 0.12}
        ),
        width,
        height,
    )
    indicator_region = _region_to_px(
        regions.get(
            "beat_indicator",
            {"x": 0.82, "y": 0.73, "width": 0.17, "height": 0.24},
        ),
        width,
        height,
    )
    raw_riff_region = regions.get("riff")
    if isinstance(raw_riff_region, dict):
        riff_region = _region_to_px(raw_riff_region, width, height)
    else:
        riff_width_target = int(round(width * (1498.0 / 1920.0)))
        riff_width = max(1, min(riff_width_target, width - 54))
        riff_height = max(1, indicator_region[3])
        riff_x = 54
        riff_region = (
            riff_x,
            indicator_region[1],
            riff_width,
            riff_height,
        )

    render_cfg = (
        layout_data.get("render")
        if isinstance(layout_data.get("render"), dict)
        else {}
    )
    custom_font_dir = (
        render_cfg.get("font_dir")
        if isinstance(render_cfg.get("font_dir"), (str, os.PathLike))
        else None
    )

    lyric_style = {
        **typography.get(
            "lyric_main",
            {
                "size_px": 94,
                "color": "#FFFFFF",
                "align": "center",
                "font_family": "Inter",
                "weight": 700,
            },
        ),
        "font_dir": custom_font_dir,
    }
    split_a_line_color = _hex_to_rgb(
        str(lyric_style.get("split_a_color", "#ADFFDA"))
    )
    split_b_line_color = _hex_to_rgb(
        str(lyric_style.get("split_b_color", "#F7E6D8"))
    )
    split_c_line_color = _hex_to_rgb(
        str(lyric_style.get("split_c_color", "#E8DFF7"))
    )
    chord_style = {
        **typography.get(
            "chord_main",
            {
                "size_px": 76,
                "color": "#FFFFFF",
                "align": "center",
                "font_family": "Inter",
                "weight": 700,
            },
        ),
        "font_dir": custom_font_dir,
    }
    next_chord_style = {
        **typography.get(
            "next_chord",
            {
                "size_px": 64,
                "color": "#FF9F1C",
                "align": "center",
                "weight": 700,
                "font_family": "Inter",
            },
        ),
        "font_dir": custom_font_dir,
    }
    riff_render_cfg = (
        render_cfg.get("riff")
        if isinstance(render_cfg.get("riff"), dict)
        else {}
    )
    try:
        riff_viewport_beats = float(riff_render_cfg.get("viewport_beats", 8.0))
    except (TypeError, ValueError):
        riff_viewport_beats = 8.0
    try:
        riff_center_fraction = float(
            riff_render_cfg.get("center_fraction", 0.5)
        )
    except (TypeError, ValueError):
        riff_center_fraction = 0.5
    riff_center_fraction = max(0.0, min(1.0, riff_center_fraction))
    try:
        riff_pre_roll_beats = float(
            riff_render_cfg.get("pre_roll_beats", -1.0)
        )
    except (TypeError, ValueError):
        riff_pre_roll_beats = -1.0
    if riff_pre_roll_beats < 0.0:
        riff_pre_roll_beats = max(
            0.0, riff_viewport_beats * riff_center_fraction
        )
    try:
        riff_post_roll_beats = float(
            riff_render_cfg.get("post_roll_beats", 0.0)
        )
    except (TypeError, ValueError):
        riff_post_roll_beats = 0.0
    riff_post_roll_beats = max(0.0, riff_post_roll_beats)
    riff_lane_padding_px = 0
    try:
        riff_lane_gap_px = int(riff_render_cfg.get("lane_gap_px", 12))
    except (TypeError, ValueError):
        riff_lane_gap_px = 12
    try:
        riff_note_side_margin_px = int(
            riff_render_cfg.get("note_side_margin_px", 2)
        )
    except (TypeError, ValueError):
        riff_note_side_margin_px = 2
    try:
        riff_note_height_ratio = float(
            riff_render_cfg.get("note_height_ratio", 0.58)
        )
    except (TypeError, ValueError):
        riff_note_height_ratio = 0.58

    riff_lane_color = _hex_to_rgb(
        str(riff_render_cfg.get("lane_color", "#2A2A2A"))
    )
    riff_note_color = _hex_to_rgb(
        str(riff_render_cfg.get("note_color", "#F5F5F5"))
    )
    riff_active_note_color = _hex_to_rgb(
        str(riff_render_cfg.get("active_note_color", "#19FF19"))
    )
    riff_playhead_color = _hex_to_rgb(
        str(riff_render_cfg.get("playhead_color", "#FF8000"))
    )
    try:
        riff_playhead_width_px = int(
            riff_render_cfg.get("playhead_width_px", 4)
        )
    except (TypeError, ValueError):
        riff_playhead_width_px = 4
    bg_color = _hex_to_rgb(background_color)
    indicator_fg = _hex_to_rgb(
        str(render_cfg.get("indicator_fill_color", "#72E676"))
    )
    pulse_first_color = _hex_to_rgb(
        str(
            render_cfg.get(
                "beat_pulse_first_color",
                render_cfg.get("indicator_fill_color", "#72E676"),
            )
        )
    )
    pulse_non_first_color = _hex_to_rgb(
        str(render_cfg.get("beat_pulse_non_first_color", "#3B82F6"))
    )
    pulse_expand_px = int(render_cfg.get("beat_pulse_expand_px", 28))
    pulse_width_px = int(render_cfg.get("beat_pulse_width_px", 5))

    next_chord_ring_cfg = (
        render_cfg.get("next_chord_phrase_ring")
        if isinstance(render_cfg.get("next_chord_phrase_ring"), dict)
        else {}
    )
    next_chord_warning_cfg = (
        render_cfg.get("next_chord_warning")
        if isinstance(render_cfg.get("next_chord_warning"), dict)
        else {}
    )
    ring_enabled_raw = next_chord_ring_cfg.get("enabled", True)
    next_chord_ring_enabled = (
        bool(ring_enabled_raw) if isinstance(ring_enabled_raw, bool) else True
    )
    ring_color_raw = next_chord_ring_cfg.get(
        "color", next_chord_style.get("color", "#FFFFFF")
    )
    next_chord_ring_color = _hex_to_rgb(str(ring_color_raw))
    try:
        next_chord_ring_width_px = max(
            1, int(next_chord_ring_cfg.get("width_px", 6))
        )
    except (TypeError, ValueError):
        next_chord_ring_width_px = 6
    try:
        next_chord_ring_padding_px = max(
            0, int(next_chord_ring_cfg.get("padding_px", 0))
        )
    except (TypeError, ValueError):
        next_chord_ring_padding_px = 0
    try:
        next_chord_ring_size_ratio = float(
            next_chord_ring_cfg.get("size_ratio", 1.5)
        )
    except (TypeError, ValueError):
        next_chord_ring_size_ratio = 2.0
    dotted_raw = next_chord_ring_cfg.get("dotted", True)
    next_chord_ring_dotted = (
        bool(dotted_raw) if isinstance(dotted_raw, bool) else True
    )
    try:
        next_chord_ring_dash_degrees = float(
            next_chord_ring_cfg.get("dash_degrees", 5.5)
        )
    except (TypeError, ValueError):
        next_chord_ring_dash_degrees = 5.5
    try:
        next_chord_ring_gap_degrees = float(
            next_chord_ring_cfg.get("gap_degrees", 4.5)
        )
    except (TypeError, ValueError):
        next_chord_ring_gap_degrees = 4.5

    warning_enabled_raw = next_chord_warning_cfg.get("enabled", True)
    next_chord_warning_enabled = (
        bool(warning_enabled_raw)
        if isinstance(warning_enabled_raw, bool)
        else True
    )
    warning_color_raw = next_chord_warning_cfg.get(
        "color", next_chord_style.get("color", "#FFFFFF")
    )
    next_chord_warning_color = _hex_to_rgb(str(warning_color_raw))
    try:
        next_chord_warning_icon_scale = float(
            next_chord_warning_cfg.get("icon_scale", 1.8)
        )
    except (TypeError, ValueError):
        next_chord_warning_icon_scale = 1.8
    try:
        next_chord_warning_gap_px = max(
            0, int(next_chord_warning_cfg.get("gap_px", 16))
        )
    except (TypeError, ValueError):
        next_chord_warning_gap_px = 16
    try:
        next_chord_warning_flash_hz = float(
            next_chord_warning_cfg.get("flash_hz", 2.5)
        )
    except (TypeError, ValueError):
        next_chord_warning_flash_hz = 2.5
    try:
        next_chord_warning_flash_duty_cycle = float(
            next_chord_warning_cfg.get("flash_duty_cycle", 0.5)
        )
    except (TypeError, ValueError):
        next_chord_warning_flash_duty_cycle = 0.5

    effective_chords = _effective_chords_for_all(song_events)
    effective_riffs = _effective_riffs_for_all(song_events)
    display_texts = _display_texts_for_all(song_events)
    lyric_line_override_colors = _lyric_line_override_colors_for_all(
        song_events,
        split_b_line_color,
        split_a_color=split_a_line_color,
        split_c_color=split_c_line_color,
    )
    next_display_change_indices = _next_display_change_indices(display_texts)
    display_set_start_indices = _display_set_start_indices(display_texts)
    next_lyric_indices = _next_lyric_indices(song_events)
    next_riff_indices = _next_riff_indices(effective_riffs)
    instruction_windows = _instruction_progress_windows(
        song_events,
        event_starts_seconds,
        tail_seconds,
    )
    next_chord_event_indices = _next_chord_event_indices(effective_chords)
    next_lookahead_event_indices = _next_lookahead_event_indices(
        song_events, effective_chords
    )
    next_non_empty_raw_chord_event_indices = (
        _next_non_empty_raw_chord_event_indices(song_events)
    )
    first_non_empty_chord_idx = _first_non_empty_chord_index(effective_chords)
    if event_beats_per_bar is None or len(event_beats_per_bar) != len(
        song_events
    ):
        event_beats_per_bar = [max(1, int(beats_per_bar))] * len(song_events)
    event_starts_for_beat_math = [
        time_origin_seconds + t for t in event_starts_seconds
    ]
    event_beat_anchor_indices = _resolve_event_beat_anchor_indices(
        song_events=song_events,
        event_starts_seconds=event_starts_for_beat_math,
        beat_seconds=beat_seconds,
        beat_times_seconds=beat_times_seconds,
        default_beat_anchor_index=beat_anchor_index,
        event_beats_per_bar=event_beats_per_bar,
    )
    phrase_anchor_default = beat_anchor_index
    event_phrase_anchor_indices = event_beat_anchor_indices
    _event_beats_per_phrase, _event_phrase_start_beats = (
        _resolve_phrase_profile_for_events(
            song_events=song_events,
            event_starts_seconds=event_starts_for_beat_math,
            default_beats_per_phrase=default_beats_per_phrase,
            beat_seconds=beat_seconds,
            beat_times_seconds=beat_times_seconds,
            beat_anchor_index=phrase_anchor_default,
            event_beat_anchor_indices=event_phrase_anchor_indices,
            phase_offset_for_resets=0,
            beats_per_bar=event_beats_per_bar,
        )
    )
    _event_base_beats_per_phrase, _event_extra_beats = (
        _resolve_event_phrase_structure(
            song_events=song_events,
            default_beats_per_phrase=default_beats_per_phrase,
            event_beats_per_phrase=_event_beats_per_phrase,
        )
    )
    (
        first_non_empty_chord_idx,
        first_chord_time_for_intro,
        long_intro_without_chords,
    ) = _resolve_intro_lookahead_target(
        song_events=song_events,
        effective_chords=effective_chords,
        event_starts_seconds=event_starts_seconds,
        time_origin_seconds=time_origin_seconds,
        beat_seconds=beat_seconds,
        beat_times_seconds=beat_times_seconds,
        beat_anchor_index=beat_anchor_index,
        beats_per_bar=beats_per_bar,
    )
    riff_timeline_notes, riff_pitch_min, riff_pitch_max = _build_riff_timeline(
        song_events=song_events,
        effective_riffs=effective_riffs,
        event_starts_seconds=event_starts_seconds,
        tail_seconds=tail_seconds,
        time_origin_seconds=time_origin_seconds,
        beat_seconds=beat_seconds,
        beat_times_seconds=beat_times_seconds,
        beat_time_offsets_seconds=beat_time_offsets_seconds,
        event_beat_anchor_indices=event_beat_anchor_indices,
        riff_key_text=riff_key_text,
    )

    behavior = (
        layout_data.get("behavior")
        if isinstance(layout_data.get("behavior"), dict)
        else {}
    )
    default_behavior = (
        behavior.get("defaults")
        if isinstance(behavior.get("defaults"), dict)
        else {}
    )
    chord_lookahead_behavior = (
        behavior.get("chord_lookahead")
        if isinstance(behavior.get("chord_lookahead"), dict)
        else {}
    )
    next_chord_show_within_beats = _optional_positive_float(
        chord_lookahead_behavior.get("show_when_next_event_within_beats")
    )
    if next_chord_show_within_beats is None:
        next_chord_show_within_beats = _optional_positive_float(
            default_behavior.get("lookahead_beats")
        )

    lyric_behavior = (
        behavior.get("lyric_main")
        if isinstance(behavior.get("lyric_main"), dict)
        else {}
    )
    scroll_beats_raw = lyric_behavior.get("scroll_preview_beats", 3.0)
    try:
        scroll_beats = max(0.25, float(scroll_beats_raw))
    except (TypeError, ValueError):
        scroll_beats = 3.0
    preview_opacity_raw = lyric_behavior.get("preview_opacity", 0.5)
    try:
        preview_opacity = max(0.0, min(1.0, float(preview_opacity_raw)))
    except (TypeError, ValueError):
        preview_opacity = 0.5
    preview_margin_px_raw = lyric_behavior.get("preview_margin_px", 24)
    try:
        preview_margin_px = max(0, int(preview_margin_px_raw))
    except (TypeError, ValueError):
        preview_margin_px = 24
    scroll_window_seconds = max(0.001, beat_seconds * scroll_beats)

    text_measure_image = Image.new(
        "RGB", (max(1, lyric_region[2]), max(1, lyric_region[3])), (0, 0, 0)
    )
    text_measure_draw = ImageDraw.Draw(text_measure_image)
    text_height_cache: dict[str, int] = {}

    def _lyric_text_height(value: str) -> int:
        if value not in text_height_cache:
            text_height_cache[value] = _measure_centered_text_height(
                text_measure_draw,
                value,
                (0, 0, lyric_region[2], lyric_region[3]),
                lyric_style,
            )
        return text_height_cache[value]

    wash_layer: Image.Image | None = None
    wash_asset_path = (
        Path(__file__).resolve().parents[2] / "assets" / "wash.png"
    )
    if wash_asset_path.exists():
        try:
            with Image.open(wash_asset_path) as wash_img:
                loaded_wash = wash_img.convert("RGBA")
                if loaded_wash.size != (width, height):
                    if hasattr(Image, "Resampling"):
                        resample = Image.Resampling.LANCZOS
                    else:
                        resample = Image.LANCZOS
                    loaded_wash = loaded_wash.resize((width, height), resample)
                wash_layer = loaded_wash
        except OSError:
            wash_layer = None

    lyric_sprite_cache: dict[
        tuple[
            str,
            tuple[tuple[str, str], ...],
            tuple[int, int, int],
            int,
            tuple[tuple[int, int, int, int], ...],
        ],
        Image.Image,
    ] = {}
    chord_overlay_cache: dict[
        tuple[str, tuple[tuple[str, str], ...], int, int], Image.Image
    ] = {}
    lyric_style_key = _style_cache_key(lyric_style)
    chord_style_key = _style_cache_key(chord_style)
    next_chord_style_key = _style_cache_key(next_chord_style)
    chord_region_size = (chord_region[2], chord_region[3])
    next_chord_region_size = (next_chord_region[2], next_chord_region[3])

    def _cached_lyric_sprite(
        text: str,
        *,
        alpha: float,
        line_override_colors: dict[int, tuple[int, int, int]] | None,
    ) -> Image.Image:
        alpha_bucket = max(0, min(64, int(round(alpha * 64.0))))
        key = (
            text,
            lyric_style_key,
            bg_color,
            alpha_bucket,
            _line_override_colors_key(line_override_colors),
        )
        cached = lyric_sprite_cache.get(key)
        if cached is not None:
            return cached
        bucket_alpha = alpha_bucket / 64.0
        sprite = _render_centered_text_sprite_with_line_overrides(
            text,
            lyric_style,
            bg_color,
            bucket_alpha,
            line_override_colors,
        )
        lyric_sprite_cache[key] = sprite
        return sprite

    def _cached_chord_overlay(
        text: str,
        *,
        style: dict[str, Any],
        style_key: tuple[tuple[str, str], ...],
        region_size: tuple[int, int],
    ) -> Image.Image:
        key = (text, style_key, int(region_size[0]), int(region_size[1]))
        cached = chord_overlay_cache.get(key)
        if cached is not None:
            return cached
        overlay = _render_chord_overlay(text, style, region_size)
        chord_overlay_cache[key] = overlay
        return overlay

    total_frames = 0
    for idx, event in enumerate(song_events):
        event_beat_anchor_index = event_beat_anchor_indices[idx]
        start = event_starts_seconds[idx]
        beat_time_offset = 0.0
        if beat_time_offsets_seconds is not None and idx < len(
            beat_time_offsets_seconds
        ):
            beat_time_offset = float(beat_time_offsets_seconds[idx])
        if idx + 1 < len(event_starts_seconds):
            end = event_starts_seconds[idx + 1]
        else:
            end = start + tail_seconds

        # Use global frame indices so per-event rounding does not accumulate
        # timeline drift across long songs with many cue events.
        start_frame = int(round(start * fps))
        end_frame = int(round(end * fps))
        if end_frame <= start_frame:
            end_frame = start_frame + 1
        frame_count = max(1, end_frame - start_frame)

        chord_text = effective_chords[idx]
        event_has_next_warning = _event_has_next_warning(event)

        next_event_idx = next_lookahead_event_indices[idx]
        next_non_empty_chord_event_idx = (
            next_non_empty_raw_chord_event_indices[idx]
        )
        next_loop_event_idx = next_chord_event_indices[idx]
        next_loop_event_chords = (
            effective_chords[next_loop_event_idx]
            if next_loop_event_idx is not None
            else None
        )
        next_event_chords = (
            effective_chords[next_event_idx]
            if next_event_idx is not None
            else None
        )
        next_event_raw_chords = (
            song_events[next_event_idx].get("chords")
            if next_event_idx is not None
            else None
        )
        next_event_is_explicit_silence = (
            isinstance(next_event_raw_chords, str)
            and next_event_raw_chords == ""
        )
        next_chord_token = _next_chord_preview_token(next_event_chords)
        next_non_empty_chord_token = _next_chord_preview_token(
            effective_chords[next_non_empty_chord_event_idx]
            if next_non_empty_chord_event_idx is not None
            else None
        )
        show_loop_indicator = _should_show_loop_indicator(
            chord_text, next_loop_event_chords
        )
        if (
            first_non_empty_chord_idx is not None
            and idx < first_non_empty_chord_idx
        ):
            next_chord_token = _first_chord_token(
                effective_chords[first_non_empty_chord_idx]
            )
            show_loop_indicator = False

        for frame_idx in range(frame_count):
            current_time = (start_frame + frame_idx) / max(1, fps)
            beat_time = current_time + time_origin_seconds + beat_time_offset

            lyric_text = display_texts[idx]
            instruction_window = instruction_windows[idx]
            if instruction_window is not None:
                instruction_key, window_start, window_end = instruction_window
                instruction_progress_mode = (
                    _instruction_progress_mode_for_event(
                        instruction_key,
                        window_start_seconds=window_start
                        + time_origin_seconds,
                        window_end_seconds=window_end + time_origin_seconds,
                        beat_seconds=beat_seconds,
                        beat_times_seconds=beat_times_seconds,
                        beat_anchor_index=event_beat_anchor_indices[idx],
                        beats_per_bar=event_beats_per_bar[idx],
                        min_break_beats=16.0,
                        min_outro_beats=8.0,
                        min_intro_bars=8.0,
                    )
                )
            else:
                window_start, window_end = start, end
                instruction_progress_mode = False

            if instruction_progress_mode:
                next_lyric_idx = next_lyric_indices[idx]
            else:
                next_lyric_idx = next_display_change_indices[idx]
            next_lyric_text = (
                display_texts[next_lyric_idx]
                if next_lyric_idx is not None
                else ""
            )

            if background_is_transparent:
                img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            elif background_frame_provider is None:
                img = Image.new(
                    "RGB", (width, height), _hex_to_rgb(background_color)
                )
            else:
                img = background_frame_provider().convert("RGB")
                if img.size != (width, height):
                    if hasattr(Image, "Resampling"):
                        resample = Image.Resampling.LANCZOS
                    else:
                        resample = Image.LANCZOS
                    img = img.resize((width, height), resample)
                img = img.copy()
            draw = ImageDraw.Draw(img)

            lyric_x, lyric_y, lyric_w, lyric_h = lyric_region
            current_center_base = lyric_y + int(round(lyric_h * 0.33))
            preview_center_base = lyric_y + int(round(lyric_h * 0.73))
            lift = 0
            preview_alpha = preview_opacity
            current_alpha = 1.0

            if next_lyric_idx is not None and lyric_text and next_lyric_text:
                next_lyric_start = event_starts_seconds[next_lyric_idx]
                lead_in_seconds = max(0.0, lyric_lead_in_beats * beat_seconds)
                next_lyric_trigger_time = next_lyric_start - lead_in_seconds
                display_segment_start = event_starts_seconds[
                    display_set_start_indices[idx]
                ]
                current_text_height = _lyric_text_height(lyric_text)
                next_text_height = _lyric_text_height(next_lyric_text)
                preview_center_for_event, event_scroll_window_seconds = (
                    _compute_preview_scroll_metrics(
                        current_center_y=current_center_base,
                        preview_center_y=preview_center_base,
                        current_text_height=current_text_height,
                        next_text_height=next_text_height,
                        base_scroll_window_seconds=scroll_window_seconds,
                        seconds_until_next_lyric=max(
                            0.001,
                            next_lyric_trigger_time - display_segment_start,
                        ),
                        preview_margin_px=preview_margin_px,
                    )
                )
                seconds_to_next_lyric = max(
                    0.0, next_lyric_trigger_time - current_time
                )
                scroll_progress = max(
                    0.0,
                    min(
                        1.0,
                        (event_scroll_window_seconds - seconds_to_next_lyric)
                        / max(0.001, event_scroll_window_seconds),
                    ),
                )
                line_delta = preview_center_for_event - current_center_base
                lift = int(round(line_delta * scroll_progress))
                preview_alpha = preview_opacity + (
                    (1.0 - preview_opacity) * scroll_progress
                )
                current_alpha = 1.0 - (
                    (1.0 - preview_opacity) * scroll_progress * 0.35
                )

            lyric_overlay_to_paste: Image.Image | None = None
            lyric_overlay = Image.new("RGBA", (lyric_w, lyric_h), (0, 0, 0, 0))
            lyric_overlay_draw = ImageDraw.Draw(lyric_overlay)
            local_lyric_region = (0, 0, lyric_w, lyric_h)
            if lyric_text and not instruction_progress_mode:
                current_center_y_local = (current_center_base - lift) - lyric_y
                current_sprite = _cached_lyric_sprite(
                    lyric_text,
                    alpha=current_alpha,
                    line_override_colors=lyric_line_override_colors[idx],
                )
                current_sprite_x = (lyric_w - current_sprite.width) // 2
                current_sprite_y = current_center_y_local - (
                    current_sprite.height // 2
                )
                lyric_overlay.paste(
                    current_sprite,
                    (current_sprite_x, current_sprite_y),
                    current_sprite,
                )
            elif instruction_progress_mode:
                progress = max(
                    0.0,
                    min(
                        1.0,
                        (current_time - window_start)
                        / max(0.001, window_end - window_start),
                    ),
                )
                _draw_progress_bar_at_y(
                    lyric_overlay_draw,
                    local_lyric_region,
                    center_y=(current_center_base - lift) - lyric_y,
                    progress=progress,
                    width_ratio=0.6,
                    height_px=48,
                    border_width_px=3,
                )
            if next_lyric_text:
                preview_center_y = (
                    preview_center_for_event
                    if next_lyric_idx is not None
                    and lyric_text
                    and next_lyric_text
                    else preview_center_base
                )
                next_center_y_local = (preview_center_y - lift) - lyric_y
                next_sprite = _cached_lyric_sprite(
                    next_lyric_text,
                    alpha=preview_alpha,
                    line_override_colors=(
                        lyric_line_override_colors[next_lyric_idx]
                        if next_lyric_idx is not None
                        else {}
                    ),
                )
                next_sprite_x = (lyric_w - next_sprite.width) // 2
                next_sprite_y = next_center_y_local - (next_sprite.height // 2)
                lyric_overlay.paste(
                    next_sprite, (next_sprite_x, next_sprite_y), next_sprite
                )
            lyric_overlay_to_paste = lyric_overlay

            if wash_layer is not None:
                img.paste(wash_layer, (0, 0), wash_layer)

            if lyric_overlay_to_paste is not None:
                img.paste(
                    lyric_overlay_to_paste,
                    (lyric_x, lyric_y),
                    lyric_overlay_to_paste,
                )

            current_beat_position = _beat_position_at_time(
                beat_time,
                beat_seconds=beat_seconds,
                beat_times_seconds=beat_times_seconds,
                beat_anchor_index=event_beat_anchor_index,
            )

            riff_mode = False
            current_event_riff = (
                effective_riffs[idx] if idx < len(effective_riffs) else ""
            )
            if current_event_riff:
                riff_mode = True
            else:
                next_riff_idx = next_riff_indices[idx]
                if next_riff_idx is not None:
                    next_riff_offset = 0.0
                    if (
                        beat_time_offsets_seconds is not None
                        and next_riff_idx < len(beat_time_offsets_seconds)
                    ):
                        next_riff_offset = float(
                            beat_time_offsets_seconds[next_riff_idx]
                        )
                    next_riff_time = (
                        time_origin_seconds
                        + event_starts_seconds[next_riff_idx]
                        + next_riff_offset
                    )
                    beats_until_next_riff = _beats_until_target_time(
                        current_time=beat_time,
                        target_time=next_riff_time,
                        beat_seconds=beat_seconds,
                        beat_times_seconds=beat_times_seconds,
                        beat_anchor_index=beat_anchor_index,
                    )
                    riff_mode = (
                        0.0 <= beats_until_next_riff <= riff_pre_roll_beats
                    )

            if riff_mode:
                riff_view_start, riff_view_end = _riff_view_window(
                    now_beat=current_beat_position,
                    viewport_beats=riff_viewport_beats,
                    center_fraction=riff_center_fraction,
                )
                riff_mode = _riff_window_has_visible_notes(
                    riff_timeline_notes,
                    view_start=riff_view_start,
                    view_end=riff_view_end,
                )

            if (
                riff_mode
                and riff_pitch_min is not None
                and riff_pitch_max is not None
            ):
                _draw_riff_timeline(
                    draw,
                    region=riff_region,
                    notes=riff_timeline_notes,
                    now_beat=current_beat_position,
                    current_event_index=idx,
                    pitch_min=riff_pitch_min,
                    pitch_max=riff_pitch_max,
                    viewport_beats=riff_viewport_beats,
                    center_fraction=riff_center_fraction,
                    lane_padding_px=riff_lane_padding_px,
                    lane_gap_px=riff_lane_gap_px,
                    note_side_margin_px=riff_note_side_margin_px,
                    note_height_ratio=riff_note_height_ratio,
                    lane_color=riff_lane_color,
                    note_color=riff_note_color,
                    active_note_color=riff_active_note_color,
                    playhead_color=riff_playhead_color,
                    playhead_width_px=riff_playhead_width_px,
                )
            elif chord_text:
                chord_overlay = _cached_chord_overlay(
                    chord_text,
                    style=chord_style,
                    style_key=chord_style_key,
                    region_size=chord_region_size,
                )
                img.paste(
                    chord_overlay,
                    (chord_region[0], chord_region[1]),
                    chord_overlay,
                )

            cycle_beats_into, cycle_len_beats = _bar_cycle_state_at_time(
                beat_time,
                beat_seconds=beat_seconds,
                beat_times_seconds=beat_times_seconds,
                beat_anchor_index=event_beat_anchor_index,
                bar_start_times_seconds=bar_start_times_seconds,
                fallback_beats_per_bar=event_beats_per_bar[idx],
            )
            cycle_remaining_ratio = max(
                0.0,
                min(1.0, 1.0 - (cycle_beats_into / max(1, cycle_len_beats))),
            )

            _draw_duration_indicator(
                draw,
                indicator_region,
                remaining_ratio=cycle_remaining_ratio,
                background_color=bg_color,
                foreground_color=indicator_fg,
            )
            _draw_beat_pulse_ring(
                img,
                indicator_region,
                beat_remaining_ratio=max(
                    0.0, min(1.0, 1.0 - (cycle_beats_into % 1.0))
                ),
                color=_beat_pulse_color_for_index(
                    int(cycle_beats_into),
                    beats_per_bar=cycle_len_beats,
                    first_beat_color=pulse_first_color,
                    non_first_beat_color=pulse_non_first_color,
                ),
                max_expand_px=pulse_expand_px,
                ring_width_px=pulse_width_px,
            )

            draw_next_chord_token = (
                next_non_empty_chord_token if riff_mode else next_chord_token
            )
            draw_loop_indicator = show_loop_indicator and not riff_mode
            draw_blank_next_chord_ring = False
            hide_for_long_intro = _should_hide_long_intro_lookahead(
                event_index=idx,
                current_chords=chord_text,
                first_non_empty_chord_index=first_non_empty_chord_idx,
                long_intro_without_chords=long_intro_without_chords,
                current_time=beat_time,
                first_chord_time=first_chord_time_for_intro,
                beat_seconds=beat_seconds,
                beat_times_seconds=beat_times_seconds,
                beat_anchor_index=beat_anchor_index,
                beats_per_bar=event_beats_per_bar[idx],
                intro_lookahead_lead_beats=intro_lookahead_lead_beats,
            )
            if hide_for_long_intro:
                draw_next_chord_token = ""
                draw_loop_indicator = False

            if (
                riff_mode
                and draw_next_chord_token
                and next_non_empty_chord_event_idx is not None
                and next_chord_show_within_beats is not None
            ):
                riff_lookahead_offset = 0.0
                if (
                    beat_time_offsets_seconds is not None
                    and next_non_empty_chord_event_idx
                    < len(beat_time_offsets_seconds)
                ):
                    riff_lookahead_offset = float(
                        beat_time_offsets_seconds[
                            next_non_empty_chord_event_idx
                        ]
                    )
                riff_next_chord_time = (
                    time_origin_seconds
                    + event_starts_seconds[next_non_empty_chord_event_idx]
                    + riff_lookahead_offset
                )
                riff_beats_until_next_chord = _beats_until_target_time(
                    current_time=beat_time,
                    target_time=riff_next_chord_time,
                    beat_seconds=beat_seconds,
                    beat_times_seconds=beat_times_seconds,
                    beat_anchor_index=beat_anchor_index,
                )
                if riff_beats_until_next_chord > next_chord_show_within_beats:
                    draw_next_chord_token = ""

            should_check_lookahead_window = (
                (
                    next_non_empty_chord_event_idx
                    if riff_mode
                    else next_event_idx
                )
                is not None
                and next_chord_show_within_beats is not None
                and not draw_loop_indicator
                and bool(draw_next_chord_token)
            )
            if should_check_lookahead_window:
                lookahead_event_idx = (
                    next_non_empty_chord_event_idx
                    if riff_mode
                    else next_event_idx
                )
                next_event_offset = 0.0
                if (
                    beat_time_offsets_seconds is not None
                    and lookahead_event_idx is not None
                    and lookahead_event_idx < len(beat_time_offsets_seconds)
                ):
                    next_event_offset = float(
                        beat_time_offsets_seconds[lookahead_event_idx]
                    )
                next_chord_time = (
                    time_origin_seconds
                    + event_starts_seconds[lookahead_event_idx]
                    + next_event_offset
                )
                if _should_blank_next_chord_preview(
                    current_chords="" if riff_mode else chord_text,
                    current_time=beat_time,
                    next_chord_time=next_chord_time,
                    show_within_beats=next_chord_show_within_beats,
                    beat_seconds=beat_seconds,
                    beat_times_seconds=beat_times_seconds,
                    beat_anchor_index=beat_anchor_index,
                ):
                    draw_next_chord_token = ""
                    if not riff_mode:
                        draw_blank_next_chord_ring = True

            if (
                not riff_mode
                and next_event_idx is not None
                and next_event_is_explicit_silence
                and not draw_loop_indicator
                and not hide_for_long_intro
            ):
                next_event_offset = 0.0
                if (
                    beat_time_offsets_seconds is not None
                    and next_event_idx < len(beat_time_offsets_seconds)
                ):
                    next_event_offset = float(
                        beat_time_offsets_seconds[next_event_idx]
                    )
                next_event_time = (
                    time_origin_seconds
                    + event_starts_seconds[next_event_idx]
                    + next_event_offset
                )
                if _should_show_explicit_silence_lookahead(
                    current_time=beat_time,
                    next_event_time=next_event_time,
                    show_within_beats=next_chord_show_within_beats,
                    beat_seconds=beat_seconds,
                    beat_times_seconds=beat_times_seconds,
                    beat_anchor_index=beat_anchor_index,
                ):
                    draw_loop_indicator = False
                    draw_next_chord_token = ""
                    draw_blank_next_chord_ring = True

            draw_next_chord_content = draw_loop_indicator or bool(
                draw_next_chord_token
            )
            draw_next_chord_ring = (
                draw_next_chord_content or draw_blank_next_chord_ring
            )
            if next_chord_ring_enabled and draw_next_chord_ring:
                ring_start = start + time_origin_seconds + beat_time_offset
                ring_end = end + time_origin_seconds + beat_time_offset
                ring_remaining = _beat_cycle_remaining_ratio(
                    current_time=beat_time,
                    cycle_start=ring_start,
                    cycle_end=ring_end,
                    beat_seconds=beat_seconds,
                    beat_times_seconds=beat_times_seconds,
                    beat_anchor_index=event_beat_anchor_index,
                )
                _draw_phrase_countdown_ring(
                    img,
                    next_chord_region,
                    remaining_ratio=ring_remaining,
                    color=next_chord_ring_color,
                    ring_width_px=next_chord_ring_width_px,
                    padding_px=next_chord_ring_padding_px,
                    size_ratio=next_chord_ring_size_ratio,
                    dotted=next_chord_ring_dotted,
                    dash_degrees=next_chord_ring_dash_degrees,
                    gap_degrees=next_chord_ring_gap_degrees,
                )

            # Draw text/icon after the ring so chord glyphs stay visually on top.
            if draw_loop_indicator:
                _draw_loop_indicator(
                    img, draw, next_chord_region, next_chord_style
                )
            elif draw_next_chord_token:
                next_chord_overlay = _cached_chord_overlay(
                    draw_next_chord_token,
                    style=next_chord_style,
                    style_key=next_chord_style_key,
                    region_size=next_chord_region_size,
                )
                img.paste(
                    next_chord_overlay,
                    (next_chord_region[0], next_chord_region[1]),
                    next_chord_overlay,
                )

            if (
                next_chord_warning_enabled
                and event_has_next_warning
                and _flash_is_on(
                    beat_time,
                    flash_hz=next_chord_warning_flash_hz,
                    duty_cycle=next_chord_warning_flash_duty_cycle,
                )
            ):
                _draw_next_warning_indicator(
                    img,
                    draw,
                    next_chord_region,
                    color=next_chord_warning_color,
                    icon_scale=next_chord_warning_icon_scale,
                    gap_px=next_chord_warning_gap_px,
                )

            if frame_sink is None:
                frame_name = f"cue_{total_frames:06d}.png"
                output_dir.mkdir(parents=True, exist_ok=True)
                img.save(output_dir / frame_name)
            else:
                frame_sink(img, total_frames)
            if frame_progress_callback is not None:
                frame_progress_callback()
            total_frames += 1

    # Optional postlude keeps animated backgrounds alive after cue events
    # and beat HUD are done, avoiding a static final tpad frame.
    if postlude_seconds > 0 and song_events:
        postlude_frame_count = max(1, int(round(postlude_seconds * fps)))
        final_chord_text = effective_chords[-1] if effective_chords else ""
        final_display_idx = None
        for idx in range(len(display_texts) - 1, -1, -1):
            if display_texts[idx]:
                final_display_idx = idx
                break
        final_display_text = (
            display_texts[final_display_idx]
            if final_display_idx is not None
            else ""
        )
        final_line_override_colors = (
            lyric_line_override_colors[final_display_idx]
            if final_display_idx is not None
            else {}
        )
        lyric_x, lyric_y, lyric_w, lyric_h = lyric_region
        postlude_center_y = lyric_y + int(round(lyric_h * 0.33))
        postlude_start_time = event_starts_seconds[-1] + tail_seconds
        postlude_beat_anchor_index = event_beat_anchor_indices[-1]
        postlude_beats_per_bar = event_beats_per_bar[-1]

        for _frame_idx in range(postlude_frame_count):
            if background_is_transparent:
                img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            elif background_frame_provider is None:
                img = Image.new(
                    "RGB", (width, height), _hex_to_rgb(background_color)
                )
            else:
                img = background_frame_provider().convert("RGB")
                if img.size != (width, height):
                    if hasattr(Image, "Resampling"):
                        resample = Image.Resampling.LANCZOS
                    else:
                        resample = Image.LANCZOS
                    img = img.resize((width, height), resample)
                img = img.copy()
            draw = ImageDraw.Draw(img)

            lyric_overlay_to_paste: Image.Image | None = None
            if final_display_text:
                lyric_overlay = Image.new(
                    "RGBA", (lyric_w, lyric_h), (0, 0, 0, 0)
                )
                final_sprite = _cached_lyric_sprite(
                    final_display_text,
                    alpha=1.0,
                    line_override_colors=final_line_override_colors,
                )
                final_sprite_x = (lyric_w - final_sprite.width) // 2
                final_sprite_y = (postlude_center_y - lyric_y) - (
                    final_sprite.height // 2
                )
                lyric_overlay.paste(
                    final_sprite,
                    (final_sprite_x, final_sprite_y),
                    final_sprite,
                )
                lyric_overlay_to_paste = lyric_overlay

            if wash_layer is not None:
                img.paste(wash_layer, (0, 0), wash_layer)

            if lyric_overlay_to_paste is not None:
                img.paste(
                    lyric_overlay_to_paste,
                    (lyric_x, lyric_y),
                    lyric_overlay_to_paste,
                )

            if final_chord_text:
                final_chord_overlay = _cached_chord_overlay(
                    final_chord_text,
                    style=chord_style,
                    style_key=chord_style_key,
                    region_size=chord_region_size,
                )
                img.paste(
                    final_chord_overlay,
                    (chord_region[0], chord_region[1]),
                    final_chord_overlay,
                )

            postlude_time = postlude_start_time + (_frame_idx / max(1, fps))
            if keep_postlude_beat_clock:
                postlude_beat_time = postlude_time + time_origin_seconds
                if (
                    beat_time_offsets_seconds is not None
                    and beat_time_offsets_seconds
                ):
                    postlude_beat_time += float(beat_time_offsets_seconds[-1])
                cycle_beats_into, cycle_len_beats = _bar_cycle_state_at_time(
                    postlude_beat_time,
                    beat_seconds=beat_seconds,
                    beat_times_seconds=beat_times_seconds,
                    beat_anchor_index=postlude_beat_anchor_index,
                    bar_start_times_seconds=bar_start_times_seconds,
                    fallback_beats_per_bar=postlude_beats_per_bar,
                )
                cycle_remaining_ratio = max(
                    0.0,
                    min(
                        1.0, 1.0 - (cycle_beats_into / max(1, cycle_len_beats))
                    ),
                )

                _draw_duration_indicator(
                    draw,
                    indicator_region,
                    remaining_ratio=cycle_remaining_ratio,
                    background_color=bg_color,
                    foreground_color=indicator_fg,
                )
                _draw_beat_pulse_ring(
                    img,
                    indicator_region,
                    beat_remaining_ratio=max(
                        0.0, min(1.0, 1.0 - (cycle_beats_into % 1.0))
                    ),
                    color=_beat_pulse_color_for_index(
                        int(cycle_beats_into),
                        beats_per_bar=cycle_len_beats,
                        first_beat_color=pulse_first_color,
                        non_first_beat_color=pulse_non_first_color,
                    ),
                    max_expand_px=pulse_expand_px,
                    ring_width_px=pulse_width_px,
                )

            if frame_sink is None:
                frame_name = f"cue_{total_frames:06d}.png"
                output_dir.mkdir(parents=True, exist_ok=True)
                img.save(output_dir / frame_name)
            else:
                frame_sink(img, total_frames)
            if frame_progress_callback is not None:
                frame_progress_callback()
            total_frames += 1

    return {
        "frame_count": total_frames,
        "fps": fps,
    }
