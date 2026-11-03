"""Explicit-rectangle v2 drawing; legacy helpers supply song state, not geometry."""

from __future__ import annotations

from bisect import bisect_right
from copy import deepcopy
import math
from pathlib import Path

from PIL import Image, ImageDraw

from praecentor.io.layout_v2 import CANVAS_HEIGHT, CANVAS_WIDTH, validate_layout_config
from praecentor.io.song import SongConfigError
from praecentor.render import background_layers, cue_frames as state
from praecentor.render.text_v2 import composite_element, draw_text, rectangle


def ensure_rendering_supported() -> None:
    return None


def _config(layout_data, width, height, fps=24):
    config = validate_layout_config(layout_data)
    if (width, height) != (CANVAS_WIDTH, CANVAS_HEIGHT):
        raise SongConfigError("v2 rendering requires a 1920x1080 canvas")
    if type(fps) is not int or fps <= 0:
        raise SongConfigError("render fps must be a positive integer")
    return config


def build_background_layer_renderer(*, layout_data, layout_path, width=1920, height=1080, **kwargs):
    config = _config(layout_data, width, height)
    background = config["background"]
    for layer in background["layers"]:
        if "media" in layer:
            raise SongConfigError("Resolve v2 background media IDs to file paths before rendering")
    return background_layers.build_background_layer_renderer(
        background_config={**background, "type": "layers" if background["layers"] else "solid"},
        layout_path=layout_path, width=width, height=height,
    )


def _frame(config, provider, transparent):
    if transparent:
        return Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT))
    if provider is not None:
        image = provider().convert("RGB")
        if image.size != (CANVAS_WIDTH, CANVAS_HEIGHT):
            image = image.resize((CANVAS_WIDTH, CANVAS_HEIGHT), Image.Resampling.LANCZOS)
        return image.copy()
    return Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), state._hex_to_rgb(config["background"]["color"]))


def _emit(image, index, prefix, output_dir, frame_sink, callback):
    if frame_sink is None:
        output_dir.mkdir(parents=True, exist_ok=True)
        image.save(output_dir / f"{prefix}_{index:06d}.png")
    else:
        frame_sink(image, index)
    if callback is not None:
        callback()


def _region(element):
    left, top, right, bottom = rectangle(element)
    return left, top, right - left, bottom - top


def _inset(element, padding):
    result = deepcopy(element)
    padding = max(0, min(padding, (min(element["width"], element["height"]) - 1) / 2))
    result.update(x=element["x"] + padding, y=element["y"] + padding,
                  width=element["width"] - padding * 2, height=element["height"] - padding * 2)
    return result


def _ring(image, element, settings, remaining):
    if not settings["enabled"] or remaining <= 0:
        return
    inner = _inset(element, settings["padding_px"])
    overlay = Image.new("RGBA", image.size)
    state._draw_phrase_countdown_ring(
        overlay, _region(inner), remaining_ratio=remaining,
        color=state._hex_to_rgb(settings["color"]), ring_width_px=max(1, round(settings["width_px"])),
        padding_px=0, size_ratio=1.0, dotted=settings["dotted"],
        dash_degrees=settings["dash_degrees"], gap_degrees=settings["gap_degrees"],
    )
    composite_element(image, overlay, element)


def _icon(image, element, icon, *, top=False, offset=0):
    overlay = Image.new("RGBA", image.size)
    left = round(element["x"] + (element["width"] - icon.width) / 2)
    vertical = element["y"] if top else element["y"] + (element["height"] - icon.height) / 2
    overlay.alpha_composite(icon, dest=(left, round(vertical + offset)))
    composite_element(image, overlay, element)


def _next_chord(image, element, token, remaining, warning=False, loop=False, at_seconds=0):
    appearance = element["appearance"]
    _ring(image, element, appearance["ring"], remaining)
    text_box = _inset(element, appearance["ring"]["padding_px"])
    if loop:
        size = max(1, round(min(element["width"], element["height"],
                                element["style"]["size_px"] * appearance["loop"]["icon_scale"])))
        icon = state._loop_icon_image(size, state._hex_to_rgb(element["style"]["color"]))
        _icon(image, element, icon, offset=appearance["loop"]["y_offset_px"])
    else:
        warning_style = appearance["warning"]
        if warning and warning_style["enabled"]:
            size = max(1, round(min(element["width"], element["height"] / 3,
                                    element["style"]["size_px"] * warning_style["icon_scale"])))
            icon = state._warning_icon_image(size, state._hex_to_rgb(warning_style["color"]))
            if state._flash_is_on(
                at_seconds, flash_hz=warning_style["flash_hz"], duty_cycle=warning_style["flash_duty_cycle"]
            ):
                _icon(image, element, icon, top=True)
            text_box["y"] += icon.height + warning_style["gap_px"]
            text_box["height"] = element["y"] + element["height"] - text_box["y"]
        if text_box["height"] > 0:
            draw_text(image, token, text_box, normalize_flats=True)


def _beat(image, element, beat_time, beat_seconds, beat_times, bar_times, anchor, beats_per_bar):
    into, length = state._bar_cycle_state_at_time(
        beat_time, beat_seconds=beat_seconds, beat_times_seconds=beat_times,
        beat_anchor_index=anchor, bar_start_times_seconds=bar_times, fallback_beats_per_bar=beats_per_bar,
    )
    appearance = element["appearance"]
    remaining = max(0, min(1, 1 - (into % 1)))
    expansion = min(appearance["pulse_expand_px"], min(element["width"], element["height"]) / 4)
    inner = _inset(element, expansion + appearance["pulse_width_px"])
    overlay = Image.new("RGBA", image.size)
    draw = ImageDraw.Draw(overlay)
    left, top, width, height = _region(inner)
    size = min(width, height)
    bounds = (left + (width - size) // 2, top + (height - size) // 2)
    bbox = (*bounds, bounds[0] + size - 1, bounds[1] + size - 1)
    draw.ellipse(bbox, fill="#000000")
    ratio = max(0, min(1, 1 - into / max(1, length)))
    draw.pieslice(bbox, start=-90, end=-90 + ratio * 360, fill=appearance["fill_color"])
    state._draw_beat_pulse_ring(
        overlay, _region(inner), beat_remaining_ratio=remaining,
        color=state._hex_to_rgb(appearance["pulse_first_color"] if int(into) % length == 0
                                else appearance["pulse_other_color"]),
        max_expand_px=round(expansion), ring_width_px=max(1, round(appearance["pulse_width_px"])),
    )
    composite_element(image, overlay, element)


def _lyrics(image, element, current, upcoming, progress, colors, upcoming_colors):
    if not upcoming:
        draw_text(image, current, element, line_colors=colors)
        return
    settings = element["behavior"]
    gap = min(settings["preview_margin_px"], max(0, element["height"] - 2))
    row_height = (element["height"] - gap) / 2
    shift = (row_height + gap) * progress
    overlay = Image.new("RGBA", image.size)
    first = {**element, "y": element["y"] - shift, "height": row_height}
    second = {**element, "y": element["y"] + row_height + gap - shift, "height": row_height}
    draw_text(overlay, current, first, line_colors=colors)
    alpha = settings["preview_opacity"] + (1 - settings["preview_opacity"]) * progress
    draw_text(overlay, upcoming, second, opacity=alpha, line_colors=upcoming_colors)
    composite_element(image, overlay, element)


def render_title_countdown_frames(
    *, layout_data, output_dir, width=1920, height=1080, fps=24, title_text="", artist_text="",
    key_text="", hot_start=False, countdown_beats=4, beat_seconds=1.0, countdown_start_number=None,
    first_chord_preview_text=None, first_chord_preview_lead_beats=None, background_frame_provider=None,
    background_is_transparent=False, frame_sink=None, frame_progress_callback=None, **kwargs,
):
    config = _config(layout_data, width, height, fps)
    if not math.isfinite(beat_seconds) or beat_seconds <= 0:
        raise SongConfigError("beat_seconds must be finite and greater than zero")
    if type(countdown_beats) is not int or countdown_beats < 0:
        raise SongConfigError("countdown_beats must be a non-negative integer")
    elements = config["title"]["elements"]
    count = max(0, round(countdown_beats * beat_seconds * fps)) if config["title"]["enabled"] else 0
    start_number = countdown_beats if countdown_start_number is None else countdown_start_number
    lead = min(countdown_beats, first_chord_preview_lead_beats if first_chord_preview_lead_beats is not None else 4)
    for index in range(count):
        image = _frame(config, background_frame_provider, background_is_transparent)
        beat_position = index / fps / beat_seconds
        for name, element in elements.items():
            if name == "title_text":
                draw_text(image, title_text, element)
            elif name == "metadata_text":
                flags = element["behavior"]
                lines = [artist_text if flags["show_artist"] else "",
                         f"Key: {state._normalize_chord_flats(key_text)}" if key_text and flags["show_key"] else "",
                         "HOT START" if hot_start and flags["show_hot_start"] else ""]
                draw_text(image, "\n".join(line for line in lines if line), element)
            elif name == "countdown_text":
                draw_text(image, str(start_number - int(beat_position)), element)
            elif name == "first_chord_preview" and first_chord_preview_text and beat_position >= countdown_beats - lead:
                behavior = element["behavior"]
                slide_start = countdown_beats - lead - behavior["slide_lead_beats"]
                progress = max(0, min(1, (beat_position - slide_start) / behavior["slide_beats"]))
                moved = {**element, "x": element["x"] + (1 - progress) * element["width"]}
                overlay = Image.new("RGBA", image.size)
                _ring(overlay, moved, element["appearance"]["ring"],
                      max(0, (countdown_beats - beat_position) / max(1, countdown_beats)))
                draw_text(overlay, first_chord_preview_text,
                          _inset(moved, element["appearance"]["padding_px"]), normalize_flats=True)
                composite_element(image, overlay, element)
        _emit(image, index, "title", Path(output_dir), frame_sink, frame_progress_callback)
    return {"frame_count": count, "frames_per_beat": beat_seconds * fps if count else 0}


def render_cue_frames(
    *, layout_data, output_dir, song_events, event_starts_seconds, tail_seconds,
    width=1920, height=1080, fps=24, beat_seconds=1.0, beats_per_bar=4,
    default_beats_per_phrase=4, beat_anchor_index=0, event_beats_per_bar=None,
    beat_times_seconds=None, bar_start_times_seconds=None, beat_time_offsets_seconds=None,
    time_origin_seconds=0.0, postlude_seconds=0.0, keep_postlude_beat_clock=False,
    lyric_lead_in_beats=0.0, riff_key_text="", background_frame_provider=None,
    background_is_transparent=False, frame_sink=None, frame_progress_callback=None, **kwargs,
):
    config = _config(layout_data, width, height, fps)
    if not math.isfinite(beat_seconds) or beat_seconds <= 0:
        raise SongConfigError("beat_seconds must be finite and greater than zero")
    if len(song_events) != len(event_starts_seconds):
        raise SongConfigError("Cue events and event starts must have matching lengths")
    if any(not isinstance(event, dict) for event in song_events):
        raise SongConfigError("Cue events must be mappings")
    starts = list(event_starts_seconds)
    if any(not math.isfinite(value) or value < 0 for value in starts) or starts != sorted(starts):
        raise SongConfigError("Event starts must be finite, non-negative and monotonic")
    if any(not math.isfinite(value) or value < 0 for value in (tail_seconds, postlude_seconds)):
        raise SongConfigError("Cue tail and postlude durations must be finite and non-negative")
    elements = config["song"]["elements"]
    meters = list(event_beats_per_bar or [beats_per_bar] * len(song_events))
    offsets = list(beat_time_offsets_seconds or [0.0] * len(song_events))
    if len(meters) != len(song_events) or len(offsets) != len(song_events):
        raise SongConfigError("Per-event meter and beat offsets must match the cue events")
    anchors = state._resolve_event_beat_anchor_indices(
        song_events=song_events, event_starts_seconds=[value + time_origin_seconds for value in starts],
        beat_seconds=beat_seconds, beat_times_seconds=beat_times_seconds,
        default_beat_anchor_index=beat_anchor_index, event_beats_per_bar=meters,
    )
    chords = state._effective_chords_for_all(song_events)
    riffs = state._effective_riffs_for_all(song_events)
    displays = state._display_texts_for_all(song_events)
    last_display = next((index for index in range(len(displays) - 1, -1, -1) if displays[index]), None)
    next_displays = state._next_display_change_indices(displays)
    next_chords = state._next_non_empty_raw_chord_event_indices(song_events)
    next_lookahead = state._next_lookahead_event_indices(song_events, chords)
    next_riffs = state._next_riff_indices(riffs)
    lyric = elements.get("lyric_main")
    colors = state._lyric_line_override_colors_for_all(
        song_events, state._hex_to_rgb(lyric["style"]["split_b_color"]),
        split_a_color=state._hex_to_rgb(lyric["style"]["split_a_color"]),
        split_c_color=state._hex_to_rgb(lyric["style"]["split_c_color"]),
    ) if lyric else [{} for _event in song_events]
    notes, pitch_min, pitch_max = state._build_riff_timeline(
        song_events=song_events, effective_riffs=riffs, event_starts_seconds=starts,
        tail_seconds=tail_seconds, time_origin_seconds=time_origin_seconds, beat_seconds=beat_seconds,
        beat_times_seconds=beat_times_seconds, beat_time_offsets_seconds=offsets,
        event_beat_anchor_indices=anchors, riff_key_text=riff_key_text,
    ) if "riff" in elements else ([], None, None)
    duration = starts[-1] + tail_seconds if starts else 0
    body_frames = max(1, round(duration * fps)) if starts else 0
    postlude_frames = max(1, round(postlude_seconds * fps)) if postlude_seconds > 0 else 0
    for frame_index in range(body_frames + postlude_frames):
        postlude = frame_index >= body_frames
        now = duration + (frame_index - body_frames) / fps if postlude else frame_index / fps
        image = _frame(config, background_frame_provider, background_is_transparent)
        index = bisect_right(starts, now) - 1
        if index >= 0:
            index = min(index, len(song_events) - 1)
            beat_time = now + time_origin_seconds + offsets[index]
            raw_beat, remaining, _until = state._beat_state_at_time(
                beat_time, beat_seconds=beat_seconds, beat_times_seconds=beat_times_seconds,
                beat_anchor_index=anchors[index],
            )
            position = raw_beat + 1 - remaining
            riff_mode = False
            riff = elements.get("riff")
            if riff and notes and not postlude:
                appearance = riff["appearance"]
                riff_mode = bool(riffs[index])
                upcoming_riff = next_riffs[index]
                if not riff_mode and upcoming_riff is not None:
                    until = state._beats_until_target_time(
                        current_time=beat_time,
                        target_time=starts[upcoming_riff] + time_origin_seconds + offsets[upcoming_riff],
                        beat_seconds=beat_seconds, beat_times_seconds=beat_times_seconds,
                        beat_anchor_index=anchors[index],
                    )
                    riff_mode = 0 <= until <= appearance["pre_roll_beats"]
                if not riff_mode and index > 0 and riffs[index - 1] and appearance["post_roll_beats"] > 0:
                    riff_mode = (now - starts[index]) / beat_seconds <= appearance["post_roll_beats"]
                view_start, view_end = state._riff_view_window(
                    now_beat=position, viewport_beats=appearance["viewport_beats"],
                    center_fraction=appearance["center_fraction"],
                )
                riff_mode = riff_mode and state._riff_window_has_visible_notes(
                    notes, view_start=view_start, view_end=view_end
                )
            for name, element in elements.items():
                if name == "lyric_main":
                    display_index = last_display if postlude else index
                    next_index = None if postlude else next_displays[index]
                    upcoming = displays[next_index] if next_index is not None else ""
                    window = element["behavior"]["scroll_preview_beats"] * beat_seconds
                    trigger = (starts[next_index] - lyric_lead_in_beats * beat_seconds
                               if next_index is not None else now + window)
                    progress = max(0, min(1, (now - trigger + window) / window)) if upcoming else 0
                    _lyrics(
                        image, element, displays[display_index] if display_index is not None else "",
                        upcoming, progress, colors[display_index] if display_index is not None else {},
                        colors[next_index] if next_index is not None else {},
                    )
                elif name == "chord_main" and not riff_mode:
                    draw_text(image, chords[index], element, normalize_flats=True)
                elif name == "riff" and riff_mode and pitch_min is not None and pitch_max is not None:
                    appearance = element["appearance"]
                    overlay = Image.new("RGBA", image.size)
                    state._draw_riff_timeline(
                        ImageDraw.Draw(overlay), region=_region(element), notes=notes, now_beat=position,
                        current_event_index=index, pitch_min=pitch_min, pitch_max=pitch_max,
                        viewport_beats=appearance["viewport_beats"], center_fraction=appearance["center_fraction"],
                        lane_padding_px=0, lane_gap_px=round(appearance["lane_gap_px"]),
                        note_side_margin_px=round(appearance["note_side_margin_px"]),
                        note_height_ratio=appearance["note_height_ratio"],
                        lane_color=state._hex_to_rgb(appearance["lane_color"]),
                        note_color=state._hex_to_rgb(appearance["note_color"]),
                        active_note_color=state._hex_to_rgb(appearance["active_note_color"]),
                        playhead_color=state._hex_to_rgb(appearance["playhead_color"]),
                        playhead_width_px=max(1, round(appearance["playhead_width_px"])),
                    )
                    composite_element(image, overlay, element)
                elif name == "beat_indicator" and (not postlude or keep_postlude_beat_clock):
                    _beat(image, element, beat_time, beat_seconds, beat_times_seconds,
                          bar_start_times_seconds, anchors[index], meters[index])
                elif name == "next_chord" and not postlude:
                    target = next_chords[index] if riff_mode else next_lookahead[index]
                    if target is not None:
                        target_time = starts[target] + time_origin_seconds + offsets[target]
                        until = state._beats_until_target_time(
                            current_time=beat_time, target_time=target_time, beat_seconds=beat_seconds,
                            beat_times_seconds=beat_times_seconds, beat_anchor_index=anchors[index],
                        )
                        if until <= element["behavior"]["lookahead_beats"]:
                            ratio = state._beat_cycle_remaining_ratio(
                                current_time=beat_time,
                                cycle_start=starts[index] + time_origin_seconds + offsets[index],
                                cycle_end=target_time, beat_seconds=beat_seconds,
                                beat_times_seconds=beat_times_seconds, beat_anchor_index=anchors[index],
                            )
                            loop = state._should_show_loop_indicator(chords[index], chords[target]) and not riff_mode
                            _next_chord(
                                image, element, state._next_chord_preview_token(chords[target]), ratio,
                                warning=state._event_has_next_warning(song_events[index]),
                                loop=loop, at_seconds=beat_time,
                            )
        _emit(image, frame_index, "cue", Path(output_dir), frame_sink, frame_progress_callback)
    return {"frame_count": body_frames + postlude_frames, "fps": fps}
