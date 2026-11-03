from copy import deepcopy

import pytest

from praecentor.io.layout import load_layout_overrides, resolve_layout_config
from praecentor.io.layout_migration import convert_v1_layout
from praecentor.io.song import SongConfigError
from praecentor.render import cue_frames


@pytest.fixture
def inter():
    if "Inter" not in cue_frames._load_font({"font_family": "Inter", "weight": 700}).getname()[0]:
        pytest.skip("Set PRAECENTOR_FONTS_DIR for Inter migration measurements")


def test_conversion_resolves_original_geometry_and_preserves_input(inter):
    source = {"version": 1, "video": {"background": {"type": "layers", "layers": [
        {"type": "image", "media": "upload-id", "opacity": 0.5}]}},
        "title": {"show_artist": False, "show_song_title": False}}
    original = deepcopy(source)
    converted = convert_v1_layout(source)
    assert converted.changed is True
    assert converted.warnings
    assert source == original
    result = resolve_layout_config(converted.spec)
    chord = result["song"]["elements"]["chord_main"]
    assert (chord["x"], chord["y"], chord["width"], chord["height"]) == (345, 842, 729, 140)
    assert chord["style"]["size_px"] > 76
    assert result["song"]["elements"]["lyric_main"]["x"] == 20
    assert result["background"]["layers"][0]["media"] == "upload-id"
    assert result["title"]["elements"]["beat_indicator"]["enabled"] is False
    assert result["title"]["elements"]["metadata_text"]["behavior"]["show_artist"] is False
    assert result["title"]["elements"]["title_text"]["enabled"] is False


def test_migration_uses_original_partial_region_semantics_not_editor_merging(inter):
    result = convert_v1_layout({"version": 1, "regions": {"chord_main": {"x": 0.3}}}).spec
    assert result["song"]["elements"]["chord_main"]["width"] == 1920
    assert result["song"]["elements"]["chord_main"]["height"] == 1080


def test_v2_is_validated_and_repeat_safe_without_rewriting():
    source = {"version": 2, "song": {"elements": {"riff": {"enabled": False}}}}
    result = convert_v1_layout(source)
    assert result.changed is False
    assert result.spec == source and result.spec is not source


def test_migration_rejects_noncanonical_canvases():
    with pytest.raises(SongConfigError, match="1920x1080"):
        convert_v1_layout({"version": 1, "video": {"width": 1280, "height": 720}})


def test_sparse_file_loader_stamps_explicit_legacy_and_never_expands_v2(tmp_path):
    source = tmp_path / "layout.yaml"
    source.write_text("video:\n  background:\n    type: solid\n")
    assert load_layout_overrides(source, version=1)["version"] == 1
    assert "version" not in source.read_text()
    source.write_text("version: 2\n")
    assert load_layout_overrides(source) == {"version": 2}
    source.write_text("version: 2\nsong:\n  elements:\n    riff: {}\n    riff: {}\n")
    with pytest.raises(SongConfigError, match="Duplicate"):
        load_layout_overrides(source)
