from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os

import pytest
from PIL import Image, ImageChops, ImageDraw, __version__ as PILLOW_VERSION
from PIL import features

from praecentor.render import cue_frames, title_card, v1

REFERENCE_REVISION = "fb846c77811ef8e53f4a1e98a505f9a423fe8677"
REFERENCE_DIR = Path(__file__).parents[1] / "fixtures" / "layout_v1"


def reference_frames(tmp_path: Path, cue_renderer=None, title_renderer=None) -> dict[str, Image.Image]:
    cue_renderer = cue_renderer or v1.render_cue_frames
    title_renderer = title_renderer or v1.render_title_countdown_frames
    background = Image.new("RGB", (64, 36), "#253C56")
    background_draw = ImageDraw.Draw(background)
    background_draw.rectangle((32, 0, 63, 35), fill="#71373E")
    background_file = tmp_path / "background.png"
    background.save(background_file)
    renderer = v1.build_background_layer_renderer(
        background_config={
            "type": "layers", "color": "#101010",
            "layers": [{"type": "image", "file": str(background_file), "opacity": 0.5}],
        },
        layout_path=tmp_path / "layout.yaml", width=1920, height=1080,
    )
    frames: dict[str, Image.Image] = {}

    def title_sink(image: Image.Image, index: int) -> None:
        if index in {0, 7}:
            frames[f"title_{index}.png"] = image.copy()

    def cue_sink(image: Image.Image, index: int) -> None:
        if index in {0, 4, 8, 12, 17}:
            frames[f"cue_{index}.png"] = image.copy()

    try:
        title_renderer(
            output_dir=tmp_path, width=1920, height=1080, fps=2,
            background_color="#000000", title_text="V1 Reference",
            artist_text="Praecentor", key_text="C major", hot_start=True,
            countdown_beats=4, beat_seconds=1, title_layout={},
            first_chord_preview_text="C", first_chord_preview_lead_beats=1,
            background_frame_provider=renderer.next_frame, frame_sink=title_sink,
        )
        cue_renderer(
            output_dir=tmp_path, width=1920, height=1080, fps=2,
            background_color="#000000",
            song_events=[
                {"lyric": "First line\nSecond line", "chords": "C", "warning": "next"},
                {"lyric": "Upcoming line", "chords": "Am, C, F, Dm, G, E"},
                {"instruction": "Instrumental", "riff": "1 3 5"},
                {"lyric": "Final line", "chords": "G", "riff": ""},
            ],
            event_starts_seconds=[0, 2, 4, 6], tail_seconds=2, layout_data={},
            beat_seconds=1, beats_per_bar=4, default_beats_per_phrase=4,
            beat_anchor_index=0, postlude_seconds=1, keep_postlude_beat_clock=True,
            background_frame_provider=renderer.next_frame, frame_sink=cue_sink,
        )
    finally:
        renderer.close()
    return frames


def reference_measurements() -> dict:
    draw = ImageDraw.Draw(Image.new("RGB", (1920, 1080)))
    style = {"size_px": 76, "font_family": "Inter", "weight": 700}
    region = (345, 842, 729, 140)
    return {
        "regions": {
            "chord_main": list(region), "next_chord": [1267, 831, 230, 129],
            "beat_indicator": [1574, 788, 326, 259],
            "lyric_main": [20, 0, 1880, 1058], "riff": [54, 788, 1498, 259],
        },
        "chord_font_sizes": {
            text: cue_frames._fit_font_for_region(draw, text, style, region).size
            for text in ["C", "Am", "Am, C, F", "Am, C, F, Dm, G, E"]
        },
        "lyric_font_size": cue_frames._load_font({"size_px": 94, "weight": 700}).size,
    }


@pytest.mark.parametrize("version", ["", "version: 1\n"])
def test_v1_loader_preserves_partial_settings(tmp_path: Path, version: str) -> None:
    path = tmp_path / "layout.yaml"
    path.write_text(
        version + 'typography:\n  chord_main:\n    color: "#123456"\n',
        encoding="utf-8",
    )
    config = v1.load_layout_config(path)
    assert config["typography"]["chord_main"] == {"color": "#123456"}
    assert "regions" not in config
    assert "components" not in config


def test_v1_ignores_editor_component_allowlists(tmp_path: Path) -> None:
    frames = []
    v1.render_title_countdown_frames(
        output_dir=tmp_path, width=320, height=180, fps=1,
        background_color="#000000", title_text="Legacy title", artist_text="Artist",
        key_text="C major", hot_start=False, countdown_beats=1, beat_seconds=1,
        title_layout={"components": []},
        frame_sink=lambda image, index: frames.append(image.copy()),
    )
    assert frames[0].getbbox() is not None


@pytest.mark.parametrize("renderer", [cue_frames, title_card])
def test_v1_fits_chords_without_position_dependent_size_changes(renderer) -> None:
    draw = ImageDraw.Draw(Image.new("RGB", (1920, 1080)))
    style = {"size_px": 76, "font_family": "Inter", "weight": 700}
    sizes = [
        renderer._fit_font_for_region(draw, "Am, C, F", style, region).size
        for region in [(345, 842, 729, 140), (0, 0, 729, 140)]
    ]
    assert sizes[0] == sizes[1]
    assert 76 < sizes[0] <= 228
    assert renderer._fit_font_for_region(draw, "C", style, (0, 0, 1920, 1080)).size == 228


def test_v1_inter_reference_frames(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    font_dir = os.environ.get("PRAECENTOR_FONTS_DIR") or os.environ.get("PRAECENTOR_FONT_DIR")
    if not font_dir:
        pytest.skip("Set PRAECENTOR_FONTS_DIR to run the pinned Inter visual references")
    manifest = json.loads((REFERENCE_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["reference_revision"] == REFERENCE_REVISION
    for filename, digest in manifest["fonts"].items():
        font_file = Path(font_dir) / filename
        assert font_file.is_file(), f"Missing reference font: {font_file}"
        assert hashlib.sha256(font_file.read_bytes()).hexdigest() == digest
    monkeypatch.setenv("PRAECENTOR_FONTS_DIR", font_dir)
    monkeypatch.delenv("PRAECENTOR_FONT_DIR", raising=False)
    assert "Inter" in cue_frames._load_font({"weight": 700}).getname()[0]
    assert reference_measurements() == manifest["measurements"]
    if manifest["pillow"] != PILLOW_VERSION or manifest["freetype"] != features.version("freetype2"):
        pytest.skip("Pixel references require the recorded Pillow/FreeType versions; metrics verified")
    frames = reference_frames(tmp_path)
    assert sorted(frames) == sorted(manifest["frames"])
    for filename, frame in frames.items():
        with Image.open(REFERENCE_DIR / filename) as expected:
            assert frame.size == expected.size == (1920, 1080)
            assert ImageChops.difference(frame.convert("RGB"), expected.convert("RGB")).getbbox() is None, filename
