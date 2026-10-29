from __future__ import annotations

from functools import lru_cache
import os
import re
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFont

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PACKAGE_ASSET_DIR = _PACKAGE_ROOT / "assets"
_REPO_ASSET_DIR = _REPO_ROOT / "assets"
_DEFAULT_ASSET_FONTS_DIR = (
    _PACKAGE_ASSET_DIR / "fonts"
    if (_PACKAGE_ASSET_DIR / "fonts").exists()
    else (_REPO_ASSET_DIR / "fonts")
)


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
    x = int(float(region.get("x", 0.0)) * width)
    y = int(float(region.get("y", 0.0)) * height)
    w = int(float(region.get("width", 1.0)) * width)
    h = int(float(region.get("height", 1.0)) * height)
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

    # macOS system candidates first (most likely in this workspace).
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
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        ]
    )

    # Pillow/common fallbacks.
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


def _style_letter_spacing_px(
    style: dict[str, Any], font: ImageFont.ImageFont
) -> float:
    raw = style.get("letter_spacing_em")
    family_hint = str(style.get("font_family", "Inter")).strip().lower()

    if raw is None:
        em_value = -0.05 if "inter" in family_hint else 0.0
    elif isinstance(raw, (int, float)):
        em_value = float(raw)
    elif isinstance(raw, str):
        text = raw.strip().lower()
        try:
            if text.endswith("em"):
                em_value = float(text[:-2].strip())
            else:
                em_value = float(text)
        except ValueError:
            em_value = -0.05 if "inter" in family_hint else 0.0
    else:
        em_value = -0.05 if "inter" in family_hint else 0.0

    font_size = max(1, int(getattr(font, "size", int(style.get("size_px", 48)))))
    return float(em_value) * float(font_size)


def _line_width_with_spacing(
    draw: ImageDraw.ImageDraw,
    line: str,
    font: ImageFont.ImageFont,
    letter_spacing_px: float,
) -> int:
    left, right = _line_bounds_with_spacing(
        draw, line, font, letter_spacing_px
    )
    return max(1, right - left)


def _line_bounds_with_spacing(
    draw: ImageDraw.ImageDraw,
    line: str,
    font: ImageFont.ImageFont,
    letter_spacing_px: float,
) -> tuple[int, int]:
    line_text = line if line else " "
    min_x: int | None = None
    max_x: int | None = None
    cursor_x = 0.0
    for idx, ch in enumerate(line_text):
        bbox = draw.textbbox((int(round(cursor_x)), 0), ch, font=font)
        if min_x is None or bbox[0] < min_x:
            min_x = bbox[0]
        if max_x is None or bbox[2] > max_x:
            max_x = bbox[2]
        cursor_x += max(1, bbox[2] - bbox[0])
        if idx + 1 < len(line_text):
            cursor_x += letter_spacing_px

    if min_x is None or max_x is None:
        return (0, 1)
    return (int(min_x), int(max_x))


def _draw_line_with_spacing(
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    y: int,
    line: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int] | tuple[int, int, int, int],
    letter_spacing_px: float,
) -> None:
    line_text = line if line else " "
    min_x, _max_x = _line_bounds_with_spacing(
        draw, line_text, font, letter_spacing_px
    )
    cursor_x = float(x - min_x)
    for idx, ch in enumerate(line_text):
        draw.text((int(round(cursor_x)), y), ch, font=font, fill=fill)
        bbox = draw.textbbox((0, 0), ch, font=font)
        cursor_x += max(1, bbox[2] - bbox[0])
        if idx + 1 < len(line_text):
            cursor_x += letter_spacing_px


def _measure_multiline_block(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    *,
    letter_spacing_px: float,
    line_spacing: int = 4,
) -> tuple[int, int, int, list[int], list[str]]:
    lines = text.splitlines() or [""]
    line_bbox = draw.textbbox((0, 0), "Ag", font=font)
    line_height = max(1, line_bbox[3] - line_bbox[1])
    line_widths = [
        _line_width_with_spacing(draw, line, font, letter_spacing_px)
        for line in lines
    ]
    block_w = max(1, max(line_widths, default=1))
    block_h = max(1, (line_height * len(lines)) + (line_spacing * (len(lines) - 1)))
    return block_w, block_h, line_height, line_widths, lines


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
        letter_spacing_px = _style_letter_spacing_px(style, candidate_font)
        text_w, text_h, _line_height, _line_widths, _lines = _measure_multiline_block(
            draw,
            text,
            candidate_font,
            letter_spacing_px=letter_spacing_px,
        )
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
    letter_spacing_px = _style_letter_spacing_px(style, font)

    x, y, w, h = region
    block_w, block_h, line_height, line_widths, lines = _measure_multiline_block(
        draw,
        text,
        font,
        letter_spacing_px=letter_spacing_px,
    )

    text_x = x + (w - block_w) // 2
    text_y = y + (h - block_h) // 2
    cursor_y = text_y
    for line_idx, line in enumerate(lines):
        line_w = line_widths[line_idx]
        if align == "right":
            line_x = text_x + (block_w - line_w)
        elif align == "left":
            line_x = text_x
        else:
            line_x = text_x + (block_w - line_w) // 2
        _draw_line_with_spacing(
            draw,
            x=line_x,
            y=cursor_y,
            line=line,
            font=font,
            fill=color,
            letter_spacing_px=letter_spacing_px,
        )
        cursor_y += line_height
        if line_idx + 1 < len(lines):
            cursor_y += 4


def _draw_box(
    draw: ImageDraw.ImageDraw,
    region: tuple[int, int, int, int],
    border_color: tuple[int, int, int],
    border_px: int,
) -> None:
    x, y, w, h = region
    for i in range(max(1, border_px)):
        draw.rectangle(
            [x + i, y + i, x + w - 1 - i, y + h - 1 - i], outline=border_color
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
    size = max(1, int(round(base_size * max(0.1, min(1.0, size_ratio)))))
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
    letter_spacing_px: float = 0.0,
) -> int:
    width = 0
    for idx, ch in enumerate(line):
        width += _char_width(draw, ch, flat_font if ch == "♭" else base_font)
        if idx + 1 < len(line):
            width += int(round(letter_spacing_px))
    return width


def _mixed_font_block_bbox(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    *,
    base_font: ImageFont.ImageFont,
    flat_font: ImageFont.ImageFont,
    line_height_px: int,
    letter_spacing_px: float,
) -> tuple[int, int, int, int]:
    min_x: int | None = None
    min_y: int | None = None
    max_x: int | None = None
    max_y: int | None = None

    for line_idx, line in enumerate(lines):
        cursor_x = 0
        cursor_y = line_idx * line_height_px
        for ch_idx, ch in enumerate(line):
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
            if ch_idx + 1 < len(line):
                cursor_x += int(round(letter_spacing_px))

    if min_x is None or min_y is None or max_x is None or max_y is None:
        block_h = max(1, line_height_px * max(1, len(lines)))
        return (0, 0, 1, block_h)
    return (min_x, min_y, max_x, max_y)


def _draw_chord_text_with_flat_fallback(
    draw: ImageDraw.ImageDraw,
    text: str,
    region: tuple[int, int, int, int],
    style: dict[str, Any],
    padding_px: int = 0,
    normalize_note_flats: bool = True,
) -> None:
    if not text:
        return

    x, y, w, h = region
    inner_padding = max(0, int(padding_px))
    inner_region = (
        x + inner_padding,
        y + inner_padding,
        max(1, w - (inner_padding * 2)),
        max(1, h - (inner_padding * 2)),
    )

    if normalize_note_flats:
        text = _normalize_chord_flats(text)
    base_font = _fit_font_for_region(draw, text, style, inner_region)
    flat_font = _load_flat_symbol_font(
        getattr(base_font, "size", int(style.get("size_px", 48)))
    )
    letter_spacing_px = _style_letter_spacing_px(style, base_font)
    color = _hex_to_rgb(str(style.get("color", "#FFFFFF")))

    x, y, w, h = inner_region
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
        letter_spacing_px=letter_spacing_px,
    )
    block_w = max(1, block_bbox[2] - block_bbox[0])
    block_h = max(1, block_bbox[3] - block_bbox[1])
    start_x = x + max(0, (w - block_w) // 2) - block_bbox[0]
    start_y = y + max(0, (h - block_h) // 2) - block_bbox[1]

    cursor_y = start_y
    for line in lines:
        line_w = _measure_mixed_font_line(
            draw,
            line,
            base_font,
            flat_font,
            letter_spacing_px,
        )
        cursor_x = start_x + max(0, (block_w - line_w) // 2)
        for ch_idx, ch in enumerate(line):
            glyph_font = flat_font if ch == "♭" else base_font
            draw.text((cursor_x, cursor_y), ch, font=glyph_font, fill=color)
            cursor_x += _char_width(draw, ch, glyph_font)
            if ch_idx + 1 < len(line):
                cursor_x += int(round(letter_spacing_px))
        cursor_y += line_height_px


def render_title_countdown_frames(
    *,
    output_dir: Path,
    width: int,
    height: int,
    fps: int,
    background_color: str,
    title_text: str,
    artist_text: str,
    key_text: str,
    hot_start: bool,
    countdown_beats: int,
    beat_seconds: float,
    title_layout: dict[str, Any],
    countdown_start_number: int | None = None,
    first_chord_preview_text: str | None = None,
    first_chord_preview_lead_beats: float | None = None,
    background_frame_provider: Callable[[], Image.Image] | None = None,
    background_is_transparent: bool = False,
    frame_sink: Callable[[Image.Image, int], None] | None = None,
    frame_progress_callback: Callable[[], None] | None = None,
) -> dict[str, Any]:
    if frame_sink is None:
        output_dir.mkdir(parents=True, exist_ok=True)

    regions = (
        title_layout.get("regions", {})
        if isinstance(title_layout.get("regions"), dict)
        else {}
    )
    typography = (
        title_layout.get("typography", {})
        if isinstance(title_layout.get("typography"), dict)
        else {}
    )

    title_region = _region_to_px(
        regions.get(
            "title_text", {"x": 0.1, "y": 0.15, "width": 0.8, "height": 0.2}
        ),
        width,
        height,
    )
    metadata_region = _region_to_px(
        regions.get(
            "metadata_text", {"x": 0.1, "y": 0.4, "width": 0.8, "height": 0.22}
        ),
        width,
        height,
    )
    countdown_region = _region_to_px(
        regions.get(
            "countdown_text",
            {"x": 0.45, "y": 0.75, "width": 0.1, "height": 0.1},
        ),
        width,
        height,
    )
    first_chord_preview_region = _region_to_px(
        regions.get(
            "first_chord_preview",
            {"x": 0.875, "y": 0.73, "width": 0.10, "height": 0.19},
        ),
        width,
        height,
    )

    render_cfg = (
        title_layout.get("render")
        if isinstance(title_layout.get("render"), dict)
        else {}
    )
    custom_font_dir = (
        render_cfg.get("font_dir")
        if isinstance(render_cfg.get("font_dir"), (str, os.PathLike))
        else None
    )

    title_style = {
        **typography.get(
            "title_text",
            {
                "size_px": 78,
                "color": "#FFFFFF",
                "align": "center",
                "font_family": "Inter",
                "weight": 800,
            },
        ),
        "font_dir": custom_font_dir,
    }
    metadata_style = {
        **typography.get(
            "metadata_text",
            {
                "size_px": 42,
                "color": "#D9D9D9",
                "align": "center",
                "font_family": "Inter",
                "weight": 700,
            },
        ),
        "font_dir": custom_font_dir,
    }
    countdown_style = {
        **typography.get(
            "countdown_text",
            {
                "size_px": 72,
                "color": "#FFFFFF",
                "align": "center",
                "font_family": "Inter",
                "weight": 700,
            },
        ),
        "font_dir": custom_font_dir,
    }
    first_chord_preview_style = {
        **typography.get(
            "first_chord_preview",
            {
                "size_px": 48,
                "color": "#FFFFFF",
                "align": "center",
                "weight": 700,
                "font_family": "Inter",
            },
        ),
        "font_dir": custom_font_dir,
    }
    first_chord_border_px = int(
        render_cfg.get(
            "first_chord_ring_width_px",
            render_cfg.get(
                "first_chord_box_border_px", render_cfg.get("box_border_px", 3)
            ),
        )
    )
    first_chord_box_padding_px = int(
        render_cfg.get("first_chord_box_padding_px", 18)
    )
    first_chord_border_color = _hex_to_rgb(
        str(
            render_cfg.get(
                "first_chord_ring_color",
                render_cfg.get(
                    "first_chord_box_border_color",
                    render_cfg.get("box_border_color", "#E8E8E8"),
                ),
            )
        )
    )
    try:
        first_chord_ring_size_ratio = float(
            render_cfg.get("first_chord_ring_size_ratio", 0.92)
        )
    except (TypeError, ValueError):
        first_chord_ring_size_ratio = 0.92
    dotted_raw = render_cfg.get("first_chord_ring_dotted", True)
    first_chord_ring_dotted = (
        bool(dotted_raw) if isinstance(dotted_raw, bool) else True
    )
    try:
        first_chord_ring_dash_degrees = float(
            render_cfg.get("first_chord_ring_dash_degrees", 5.5)
        )
    except (TypeError, ValueError):
        first_chord_ring_dash_degrees = 5.5
    try:
        first_chord_ring_gap_degrees = float(
            render_cfg.get("first_chord_ring_gap_degrees", 4.5)
        )
    except (TypeError, ValueError):
        first_chord_ring_gap_degrees = 4.5

    show_title = bool(title_layout.get("show_song_title", True))
    show_artist = bool(title_layout.get("show_artist", True))
    show_key = bool(title_layout.get("show_key", True))
    show_hot_start = bool(title_layout.get("show_hot_start", True))
    start_number = (
        countdown_beats
        if countdown_start_number is None
        else int(countdown_start_number)
    )
    first_chord_preview_text = (first_chord_preview_text or "").strip()
    if first_chord_preview_lead_beats is None:
        preview_countdown_beats = min(4.0, max(0.0, float(countdown_beats)))
    else:
        preview_countdown_beats = max(
            0.0,
            min(float(countdown_beats), float(first_chord_preview_lead_beats)),
        )

    metadata_lines: list[str] = []
    if show_artist and artist_text:
        metadata_lines.append(artist_text)
    if show_key and key_text:
        metadata_lines.append(f"Key: {_normalize_chord_flats(key_text)}")
    if show_hot_start and hot_start:
        metadata_lines.append("HOT START")
    metadata_text = "\n".join(metadata_lines)

    def _countdown_frames_per_beat(
        count: int, beat_len_seconds: float, frame_rate: int
    ) -> list[int]:
        if count <= 0:
            return []
        edges = [
            int(round(i * beat_len_seconds * frame_rate))
            for i in range(count + 1)
        ]
        per_beat: list[int] = []
        for i in range(count):
            beat_frames = edges[i + 1] - edges[i]
            per_beat.append(max(1, beat_frames))
        return per_beat

    frames_per_beat_list = _countdown_frames_per_beat(
        countdown_beats, beat_seconds, fps
    )
    average_frames_per_beat = (
        (sum(frames_per_beat_list) / len(frames_per_beat_list))
        if frames_per_beat_list
        else 0.0
    )
    frame_index = 0

    for beat_idx in range(countdown_beats):
        number_text = str(start_number - beat_idx)
        beat_frame_count = (
            frames_per_beat_list[beat_idx]
            if beat_idx < len(frames_per_beat_list)
            else 1
        )
        for frame_in_beat in range(beat_frame_count):
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

            if show_title:
                _draw_centered_text(
                    draw, title_text, title_region, title_style
                )
            _draw_chord_text_with_flat_fallback(
                draw,
                metadata_text,
                metadata_region,
                metadata_style,
                normalize_note_flats=False,
            )
            _draw_centered_text(
                draw, number_text, countdown_region, countdown_style
            )

            if first_chord_preview_text and preview_countdown_beats > 0:
                first_preview_beat_pos = max(
                    0.0, float(countdown_beats) - preview_countdown_beats
                )
                beat_pos = beat_idx + (
                    frame_in_beat / max(1, beat_frame_count)
                )
                if beat_pos >= first_preview_beat_pos:
                    slide_beats_raw = title_layout.get(
                        "first_chord_preview_slide_beats", 1.0
                    )
                    try:
                        slide_beats = max(0.01, float(slide_beats_raw))
                    except (TypeError, ValueError):
                        slide_beats = 1.0

                    slide_lead_beats_raw = title_layout.get(
                        "first_chord_preview_slide_lead_beats", 1.0
                    )
                    try:
                        slide_lead_beats = max(
                            0.0, float(slide_lead_beats_raw)
                        )
                    except (TypeError, ValueError):
                        slide_lead_beats = 1.0

                    slide_start_beat = (
                        first_preview_beat_pos - slide_lead_beats
                    )
                    slide_progress = max(
                        0.0,
                        min(1.0, (beat_pos - slide_start_beat) / slide_beats),
                    )

                    px, py, pw, ph = first_chord_preview_region
                    offscreen_distance = max(0, (width - px) + pw + 24)
                    slide_offset = int(
                        round((1.0 - slide_progress) * offscreen_distance)
                    )
                    preview_region = (px + slide_offset, py, pw, ph)

                    remaining_ratio = 0.0
                    if countdown_beats > 0:
                        remaining_ratio = max(
                            0.0,
                            min(
                                1.0,
                                (countdown_beats - beat_pos) / countdown_beats,
                            ),
                        )
                    _draw_phrase_countdown_ring(
                        img,
                        preview_region,
                        remaining_ratio=remaining_ratio,
                        color=first_chord_border_color,
                        ring_width_px=first_chord_border_px,
                        padding_px=first_chord_box_padding_px,
                        size_ratio=first_chord_ring_size_ratio,
                        dotted=first_chord_ring_dotted,
                        dash_degrees=first_chord_ring_dash_degrees,
                        gap_degrees=first_chord_ring_gap_degrees,
                    )
                    _draw_chord_text_with_flat_fallback(
                        draw,
                        first_chord_preview_text,
                        preview_region,
                        first_chord_preview_style,
                        padding_px=first_chord_box_padding_px,
                    )

            if frame_sink is None:
                frame_name = f"title_{frame_index:06d}.png"
                output_dir.mkdir(parents=True, exist_ok=True)
                img.save(output_dir / frame_name)
            else:
                frame_sink(img, frame_index)
            if frame_progress_callback is not None:
                frame_progress_callback()
            frame_index += 1

    return {
        "frame_count": frame_index,
        "fps": fps,
        "countdown_duration_seconds": frame_index / max(1, fps),
        "frames_per_beat": average_frames_per_beat,
        "frames_per_beat_sequence": frames_per_beat_list,
    }
