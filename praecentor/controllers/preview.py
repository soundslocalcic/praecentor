"""Deterministic still previews through the actual v2 frame renderer."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from praecentor.controllers.errors import ControllerError
from praecentor.io.layout import layout_version, load_layout_config
from praecentor.io.layout_v2 import DEFAULT_LAYOUT_PATH, enabled_elements
from praecentor.io.song import SongConfigError
from praecentor.render import cue_frames as fonts, v2

SAMPLES = (
    "title", "countdown", "lyrics", "instruction", "chords-short",
    "chords-long", "accidentals", "riff", "warning", "beat",
)


class _FrameCaptured(Exception):
    pass


@contextmanager
def _font_directory(directory):
    previous = os.environ.get("PRAECENTOR_FONTS_DIR")
    if directory is not None:
        os.environ["PRAECENTOR_FONTS_DIR"] = str(Path(directory).resolve())
    try:
        yield
    finally:
        if directory is not None:
            if previous is None:
                os.environ.pop("PRAECENTOR_FONTS_DIR", None)
            else:
                os.environ["PRAECENTOR_FONTS_DIR"] = previous


def _font_report(config):
    reports = {}
    for section in ("title", "song"):
        for element in enabled_elements(config, section).values():
            style = element.get("style")
            if style is None:
                continue
            font = fonts._load_font(style)
            family, variant = font.getname()
            if "inter" not in family.lower():
                raise ControllerError(
                    f"Preview requires Inter but selected {family} {variant}. "
                    "Set --font-dir or PRAECENTOR_FONTS_DIR to the Inter font directory."
                )
            filename = str(font.path)
            reports[(filename, style["weight"])] = {
                "family": family, "variant": variant, "weight": style["weight"], "file": filename, "role": "text",
            }
    if reports:
        symbol_font = fonts._load_flat_symbol_font(48)
        family, variant = symbol_font.getname()
        filename = str(getattr(symbol_font, "path", "Pillow default"))
        reports[(filename, "symbols")] = {
            "family": family, "variant": variant, "file": filename, "role": "musical_accidentals",
        }
    return list(reports.values())


def _sample_inputs(sample):
    current = {"lyric": "We gather in the evening\nAnd sing another song", "chords": "Am, C, F, Bb"}
    upcoming = {"lyric": "The voices rise together\nThe rhythm carries on", "chords": "G"}
    if sample == "instruction":
        current = {"instruction": "Instrumental", "chords": "C"}
    elif sample == "chords-short":
        current["chords"] = "C"
    elif sample == "chords-long":
        current["chords"] = "Am, C, F, Bbm, Dm, G, E, Am"
    elif sample == "accidentals":
        current["chords"] = "Bbm, Db, F#, Ab"
        current["lyric"] = "[a] We gather in the evening\n[b] And sing another song\n[c] Three voices join together"
        upcoming["chords"] = "Bb"
    elif sample == "riff":
        current = {"instruction": "Instrumental", "riff": "1 3 5"}
    elif sample == "warning":
        current["warning"] = "next"
    return [current, upcoming]


def _guides(image, config, section, label_font):
    result = image.copy()
    draw = ImageDraw.Draw(result)
    groups = {}
    for name, element in enabled_elements(config, section).items():
        key = tuple(element[field] for field in ("x", "y", "width", "height"))
        groups.setdefault(key, []).append(name)
    for (left, top, width, height), names in groups.items():
        right, bottom = left + width - 1, top + height - 1
        draw.rectangle((left, top, right, bottom), outline="#58DECE", width=2)
        label = f"{' / '.join(names)}\n({left:g}, {top:g}; {width:g} x {height:g})"
        bounds = draw.multiline_textbbox((0, 0), label, font=label_font, spacing=2)
        label_width, label_height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        position = (max(4, min(left + 4, image.width - label_width - 4)) - bounds[0],
                    max(4, min(top + 4, image.height - label_height - 4)) - bounds[1])
        box = draw.multiline_textbbox(position, label, font=label_font, spacing=2)
        draw.rectangle((box[0] - 3, box[1] - 3, box[2] + 3, box[3] + 3), fill="#17342F")
        draw.multiline_text(position, label, font=label_font, fill="#FFFFFF", spacing=2)
    return result


def _render_sample(config, layout_path, sample):
    captured = []
    target = 6 if sample == "countdown" else 3 if sample == "beat" else 0 if sample in ("title", "warning") else 1

    def sink(image, index):
        if index == target:
            captured.append(image.copy())
            raise _FrameCaptured()

    provider = v2.build_background_layer_renderer(layout_data=config, layout_path=layout_path)
    common = dict(
        layout_data=config, output_dir=layout_path.parent, fps=4,
        beat_seconds=0.5, frame_sink=sink,
        background_frame_provider=provider.next_frame if provider is not None else None,
    )
    try:
        if sample in ("title", "countdown"):
            if not config["title"]["enabled"]:
                return v2._frame(config, common["background_frame_provider"], False)
            v2.render_title_countdown_frames(
                **common, title_text="Evening Rehearsal", artist_text="Sample Ensemble",
                key_text="Bb major", hot_start=True, countdown_beats=4,
                first_chord_preview_text="Bb", first_chord_preview_lead_beats=1,
            )
        else:
            v2.render_cue_frames(
                **common, song_events=_sample_inputs(sample), event_starts_seconds=[0, 2],
                tail_seconds=2, beats_per_bar=4, default_beats_per_phrase=4,
                beat_anchor_index=0,
            )
    except _FrameCaptured:
        pass
    finally:
        if provider is not None:
            provider.close()
    if not captured:
        raise ControllerError(f"Sample {sample!r} produced no frame; check whether its canvas is enabled")
    return captured[0]


def preview_layout(
    layout_file: Path | None = None, *, sample="all", output: Path = Path("layout-preview.png"),
    font_dir: Path | None = None, background: Path | None = None, guides=False,
) -> dict[str, Any]:
    if sample != "all" and sample not in SAMPLES:
        raise ControllerError(f"Unknown preview sample {sample!r}")
    output = Path(output).resolve()
    if output.suffix.lower() != ".png":
        raise ControllerError("Preview output must be a .png file")
    layout_path = Path(layout_file or DEFAULT_LAYOUT_PATH).resolve()
    try:
        config = load_layout_config(layout_path)
        if layout_version(config) != 2:
            video = config.get("video")
            if "events" in config and isinstance(video, dict):
                reference = video.get("layout")
                filename = reference.get("file") if isinstance(reference, dict) else None
                target = (
                    str((layout_path.parent / filename).resolve()) if isinstance(filename, str) else "the layout YAML"
                )
                raise ControllerError(
                    "preview-layout expects a layout YAML, not a song YAML. "
                    f"Pass the file referenced by video.layout.file ({target}), "
                    "or omit the argument to preview the all-elements draft template."
                )
            raise ControllerError("preview-layout requires an explicit version: 2 layout")
        if background is not None:
            config["background"]["layers"] = [{"type": "image", "file": str(Path(background).resolve()), "opacity": 1}]
        selected = list(SAMPLES) if sample == "all" else [sample]
        metadata_file = output.with_suffix(".json")
        frame_dir = output.parent / f"{output.stem}-frames"
        inputs = {layout_path}
        for layer in config["background"]["layers"]:
            if "file" in layer:
                inputs.add((layout_path.parent / layer["file"]).resolve())
        destinations = {output, metadata_file}
        if sample == "all":
            destinations.update(frame_dir / f"{name}.png" for name in selected)
        if inputs & destinations:
            raise ControllerError(
                "Preview output would overwrite the layout or a background image; choose another output path"
            )
        with _font_directory(font_dir):
            report = _font_report(config)
            label_font = ImageFont.truetype(report[0]["file"], size=20) if report else ImageFont.load_default(size=20)
            frames = {}
            for name in selected:
                image = _render_sample(config, layout_path, name)
                if guides:
                    image = _guides(image, config, "title" if name in ("title", "countdown") else "song", label_font)
                frames[name] = image
            output.parent.mkdir(parents=True, exist_ok=True)
            saved_frames = {}
            if sample == "all":
                frame_dir.mkdir(parents=True, exist_ok=True)
                columns, tile_width, tile_height, label_height = 2, 640, 360, 32
                rows = (len(frames) + columns - 1) // columns
                sheet = Image.new("RGB", (columns * tile_width, rows * (tile_height + label_height)), "#202020")
                draw = ImageDraw.Draw(sheet)
                for index, (name, image) in enumerate(frames.items()):
                    filename = frame_dir / f"{name}.png"
                    image.save(filename)
                    saved_frames[name] = str(filename)
                    left = index % columns * tile_width
                    top = index // columns * (tile_height + label_height)
                    draw.text((left + 10, top + 4), name, font=label_font, fill="#FFFFFF")
                    thumbnail = image.resize((tile_width, tile_height), Image.Resampling.LANCZOS)
                    sheet.paste(thumbnail, (left, top + label_height))
                sheet.save(output)
            else:
                frames[sample].save(output)
                saved_frames[sample] = str(output)
            metadata = {
                "layout_file": str(layout_path), "draft_default": layout_file is None,
                "default_layout_file": str(DEFAULT_LAYOUT_PATH),
                "canvas": config["canvas"], "output": str(output), "samples": selected,
                "frames": saved_frames, "fonts": report, "guides": bool(guides),
            }
            metadata_file.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
            return {**metadata, "metadata_file": str(metadata_file)}
    except (OSError, SongConfigError) as exc:
        raise ControllerError(str(exc)) from exc
