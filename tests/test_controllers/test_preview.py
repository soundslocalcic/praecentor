import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image, ImageChops, ImageDraw, ImageFont
import pytest
import yaml

from praecentor.controllers import preview, rendering
from praecentor.controllers.errors import ControllerError
from praecentor.io.layout_v2 import DEFAULT_LAYOUT_PATH


@pytest.fixture
def inter_directory():
    for path in preview.fonts._configured_font_dirs():
        if (path / "Inter_24pt-Bold.ttf").is_file() and (path / "Inter_24pt-ExtraBold.ttf").is_file():
            return path
    pytest.skip("Set PRAECENTOR_FONTS_DIR to run real Inter preview checks")


def test_preview_generates_all_renderer_samples_and_full_size_frames(tmp_path, inter_directory, monkeypatch):
    encoder = Mock(side_effect=AssertionError("Still previews must not encode a video"))
    monkeypatch.setattr(rendering, "render", encoder)
    result = preview.preview_layout(output=tmp_path / "sheet.png", font_dir=inter_directory)
    assert result["draft_default"] is True
    assert result["samples"] == list(preview.SAMPLES)
    assert len(result["frames"]) == len(preview.SAMPLES)
    assert all(report["role"] != "text" or "Inter" in report["family"] for report in result["fonts"])
    assert any(report["role"] == "musical_accidentals" for report in result["fonts"])
    for filename in result["frames"].values():
        with Image.open(filename) as image:
            assert image.size == (1920, 1080)
            assert image.getextrema()[0][1] > 100
    with Image.open(result["output"]) as sheet:
        assert sheet.size == (1280, 1960)
    report = json.loads(Path(result["metadata_file"]).read_text())
    assert report["layout_file"] == str(DEFAULT_LAYOUT_PATH)
    encoder.assert_not_called()


def test_single_preview_is_deterministic_and_guides_are_opt_in(tmp_path, inter_directory):
    first = preview.preview_layout(sample="lyrics", output=tmp_path / "first.png", font_dir=inter_directory)
    second = preview.preview_layout(sample="lyrics", output=tmp_path / "second.png", font_dir=inter_directory)
    guided = preview.preview_layout(
        sample="lyrics", output=tmp_path / "guided.png", font_dir=inter_directory, guides=True,
    )
    with Image.open(first["output"]) as before, Image.open(second["output"]) as after:
        assert before.size == (1920, 1080)
        assert ImageChops.difference(before, after).getbbox() is None
        with Image.open(guided["output"]) as marked:
            assert ImageChops.difference(before, marked).getbbox() is not None


def test_preview_uses_custom_layout_and_background_without_modifying_sources(tmp_path):
    background = tmp_path / "image.png"
    Image.new("RGB", (40, 40), "#123456").save(background)
    layout = tmp_path / "layout.yaml"
    layout.write_text(yaml.safe_dump({"version": 2}))
    original = layout.read_bytes()
    result = preview.preview_layout(layout, sample="lyrics", output=tmp_path / "preview.png", background=background)
    assert result["draft_default"] is False
    with Image.open(result["output"]) as image:
        assert image.getpixel((0, 0)) == (18, 52, 86)
    assert layout.read_bytes() == original
    assert result["fonts"] == []


def test_preview_reports_font_fallback_instead_of_silently_substituting(tmp_path, monkeypatch):
    monkeypatch.setattr(
        preview.fonts, "_load_font", lambda style: SimpleNamespace(getname=lambda: ("DejaVu Sans", "Bold")),
    )
    with pytest.raises(ControllerError, match="requires Inter but selected DejaVu"):
        preview.preview_layout(sample="lyrics", output=tmp_path / "preview.png")
    assert not (tmp_path / "preview.png").exists()


def test_preview_restores_font_environment(tmp_path, inter_directory, monkeypatch):
    monkeypatch.setenv("PRAECENTOR_FONTS_DIR", "previous-font-setting")
    preview.preview_layout(sample="chords-short", output=tmp_path / "preview.png", font_dir=inter_directory)
    assert os.environ["PRAECENTOR_FONTS_DIR"] == "previous-font-setting"


def test_preview_rejects_v1_and_output_collisions(tmp_path):
    legacy = tmp_path / "legacy.yaml"
    legacy.write_text("version: 1\n")
    with pytest.raises(ControllerError, match="explicit version: 2"):
        preview.preview_layout(legacy, output=tmp_path / "preview.png")
    config = tmp_path / "preview.json"
    config.write_text('{"version": 2}')
    with pytest.raises(ControllerError, match="overwrite the layout"):
        preview.preview_layout(config, output=tmp_path / "preview.png")
    assert config.read_text() == '{"version": 2}'


def test_preview_explains_when_a_song_file_is_passed_instead_of_its_layout(tmp_path):
    song = tmp_path / "Praecentor.yaml"
    song.write_text(yaml.safe_dump({
        "version": 1, "video": {"layout": {"file": "layouts/sea_v2.yaml"}}, "events": [],
    }))
    with pytest.raises(ControllerError, match="expects a layout YAML, not a song YAML") as failure:
        preview.preview_layout(song, output=tmp_path / "preview.png")
    assert str(tmp_path / "layouts" / "sea_v2.yaml") in str(failure.value)
    assert "omit the argument" in str(failure.value)
    assert not (tmp_path / "preview.png").exists()


def test_guide_labels_group_shared_rectangles_and_stay_on_canvas(monkeypatch):
    config = preview.load_layout_config(DEFAULT_LAYOUT_PATH)
    labels = []
    original = ImageDraw.ImageDraw.multiline_text

    def record(draw, position, text, **kwargs):
        bounds = draw.multiline_textbbox(position, text, font=kwargs["font"], spacing=kwargs["spacing"])
        assert bounds[0] >= 0 and bounds[1] >= 0 and bounds[2] <= 1920 and bounds[3] <= 1080
        labels.append(text)
        return original(draw, position, text, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "multiline_text", record)
    preview._guides(Image.new("RGB", (1920, 1080)), config, "song", ImageFont.load_default(size=20))
    assert len(labels) == 4
    assert any("chord_main / riff" in text for text in labels)
