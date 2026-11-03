"""A measured lyric scroll with event-centred motion and clipped neighbours."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from copy import deepcopy
import math

from PIL import Image, ImageDraw

from praecentor.render import cue_frames as state
from praecentor.render.text_v2 import composite_element, draw_text, fit_text


def _ease(value):
    value = max(0.0, min(1.0, value))
    return value * value * (3 - 2 * value)


class LyricScroll:
    def __init__(
        self, element, events, starts, tail_seconds, colors, *, beat_seconds,
        beat_times=None, time_origin=0.0, anchors=None, meters=None, lead_in_beats=0.0,
        postlude_seconds=0.0, fps=24,
    ):
        self.element = deepcopy(element)
        self.settings = element["behavior"]
        self.beat_seconds = beat_seconds
        self.lead_in_beats = lead_in_beats
        self.frame_seconds = 1 / fps
        self.end_time = (starts[-1] + tail_seconds + postlude_seconds) if starts else 0
        self.blocks = []
        self.segments = []
        self.event_blocks = []
        self.breaks = {}
        windows = state._instruction_progress_windows(events, starts, tail_seconds + postlude_seconds)
        current = None
        segment = None
        cursor = 0.0
        for index, event in enumerate(events):
            window = windows[index]
            is_break = window is not None and state._instruction_progress_mode_for_event(
                window[0], window_start_seconds=window[1] + time_origin,
                window_end_seconds=window[2] + time_origin, beat_seconds=beat_seconds,
                beat_times_seconds=beat_times, beat_anchor_index=anchors[index] if anchors else 0,
                beats_per_bar=meters[index] if meters else 4,
            )
            if is_break:
                self.breaks[index] = window
                if segment is not None:
                    segment["end"] = window[1]
                current = None
                segment = None
                cursor = 0.0
            else:
                text = state._event_display_text(event)
                if text and (current is None or text != current["text"] or colors[index] != current["colors"]):
                    fitted = fit_text(text, element)
                    if fitted is not None:
                        size, _lines, top, bottom = fitted
                        if segment is None:
                            segment = {"blocks": [], "start": starts[index], "end": self.end_time}
                            self.segments.append(segment)
                        height = max(1, math.ceil(bottom - top))
                        current = {
                            "text": text, "colors": colors[index], "time": starts[index], "height": height,
                            "top": cursor, "bottom": cursor + height, "center": cursor + height / 2,
                            "size": size, "segment": segment, "row": len(segment["blocks"]),
                        }
                        self.blocks.append(current)
                        segment["blocks"].append(current)
                        cursor += height + self.settings["preview_margin_px"]
                    else:
                        current = None
            self.event_blocks.append(current)
        for segment in self.segments:
            segment["tops"] = [block["top"] for block in segment["blocks"]]
            segment["bottoms"] = [block["bottom"] for block in segment["blocks"]]

    def placements(self, now, event_index):
        if not self.blocks or event_index in self.breaks:
            return []
        if event_index is None or event_index < 0:
            current = self.blocks[0]
        else:
            current = self.event_blocks[min(event_index, len(self.event_blocks) - 1)]
        if current is None:
            return []
        element = self.element
        segment = current["segment"]
        rows = segment["blocks"]
        row = current["row"]
        offset = current["center"]
        transition = 0.0
        following = rows[row + 1] if row + 1 < len(rows) else None
        if following is not None:
            window = min(
                max(self.settings["scroll_preview_beats"], self.lead_in_beats) * self.beat_seconds,
                following["time"] - current["time"],
            )
            transition = _ease((now - following["time"] + window) / max(window, self.frame_seconds))
            offset += (following["center"] - current["center"]) * transition
        entrance = self.settings["entrance_beats"] * self.beat_seconds
        if row == 0 and entrance > 0 and now < current["time"] + entrance:
            distance = element["height"] / 2 + current["height"] / 2 + self.settings["preview_margin_px"]
            offset -= distance * (1 - _ease((now - current["time"]) / entrance))
        exiting = 0.0
        if following is None:
            duration = min(
                self.settings["exit_beats"] * self.beat_seconds,
                max(0, segment["end"] - current["time"]),
            )
            if duration > 0:
                interval = max(duration - self.frame_seconds, self.frame_seconds)
                exiting = _ease((now - segment["end"] + duration) / interval)
                offset += exiting * (element["height"] / 2 + current["height"] / 2
                                     + self.settings["preview_margin_px"])
        view_top = offset - element["height"] / 2
        view_bottom = view_top + element["height"]
        first = bisect_right(segment["bottoms"], view_top)
        last = bisect_left(segment["tops"], view_bottom)
        result = []
        for block in rows[first:last]:
            if block["row"] < row:
                alpha = self.settings["outgoing_opacity"]
            elif block["row"] == row:
                alpha = 1 - (1 - self.settings["outgoing_opacity"]) * max(transition, exiting)
            elif following is block:
                alpha = self.settings["preview_opacity"] + (1 - self.settings["preview_opacity"]) * transition
            else:
                alpha = self.settings["preview_opacity"]
            placed = {**element, "y": element["y"] + block["top"] - view_top, "height": block["height"],
                      "style": {**element["style"], "size_px": block["size"], "valign": "top"}}
            result.append((block, placed, alpha))
        return result

    def draw(self, image, now, event_index):
        if event_index in self.breaks:
            _key, start, end = self.breaks[event_index]
            element = self.element
            overlay = Image.new("RGBA", image.size)
            region = tuple(round(element[key]) for key in ("x", "y", "width", "height"))
            state._draw_progress_bar_at_y(
                ImageDraw.Draw(overlay), region,
                center_y=round(element["y"] + element["height"] / 2),
                progress=max(0, min(1, (now - start) / max(0.001, end - start))),
                width_ratio=0.6, height_px=max(1, min(48, round(element["height"] / 5))), border_width_px=3,
            )
            composite_element(image, overlay, element)
            return
        overlay = Image.new("RGBA", image.size)
        for block, placed, alpha in self.placements(now, event_index):
            draw_text(overlay, block["text"], placed, opacity=alpha, line_colors=block["colors"])
        composite_element(image, overlay, self.element)
