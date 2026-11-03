from copy import deepcopy
from pathlib import Path

import pytest

from praecentor.io.layout_v2 import (
    DEFAULT_LAYOUT_PATH, SONG_ELEMENTS, TITLE_ELEMENTS, enabled_elements, validate_layout_config,
)
from praecentor.io.layout import load_layout_config
from praecentor.io.song import SongConfigError


def sample_layout():
    return {"version": 2, "song": {"elements": {
        "chord_main": {"x": 54, "y": 788, "width": 1498, "height": 259, "style": {"size_px": 144}},
    }}}


def test_packaged_draft_template_contains_every_element():
    result = load_layout_config(DEFAULT_LAYOUT_PATH)
    assert set(result["song"]["elements"]) == set(SONG_ELEMENTS)
    assert set(result["title"]["elements"]) == set(TITLE_ELEMENTS)
    assert result["song"]["elements"]["chord_main"]["width"] == 1236
    assert result["song"]["elements"]["riff"]["width"] == 1236
    assert result["title"]["enabled"] is True


def test_v2_sparse_specs_inherit_all_elements_and_partial_overrides():
    source = {"version": 2, "song": {"elements": {"chord_main": {"x": 300, "style": {"color": "#ABC"}}}}}
    original = deepcopy(source)
    result = validate_layout_config(source)
    assert set(enabled_elements(result, "song")) == set(SONG_ELEMENTS)
    assert set(enabled_elements(result, "title")) == set(TITLE_ELEMENTS)
    chord = result["song"]["elements"]["chord_main"]
    assert (chord["x"], chord["y"], chord["width"], chord["height"]) == (300, 788, 1236, 240)
    assert chord["style"]["size_px"] == 144
    assert chord["style"]["color"] == "#ABC"
    assert source == original


def test_v2_explicit_disabling_survives_repeated_resolution():
    result = validate_layout_config({"version": 2, "title": {"enabled": False},
                                     "song": {"elements": {"riff": {"enabled": False}}}})
    assert enabled_elements(result, "title") == {}
    assert "riff" not in enabled_elements(result, "song")
    assert "chord_main" in enabled_elements(result, "song")
    assert validate_layout_config(result) == result
    assert enabled_elements(validate_layout_config({"version": 2, "song": {"enabled": False}}), "song") == {}


def test_v2_empty_element_maps_do_not_clear_inherited_defaults():
    result = validate_layout_config({"version": 2, "title": {"elements": {}}, "song": {"elements": {}}})
    assert set(enabled_elements(result, "song")) == set(SONG_ELEMENTS)
    assert set(enabled_elements(result, "title")) == set(TITLE_ELEMENTS)


@pytest.mark.parametrize("filename", ["minimal.yaml", "complete.yaml"])
def test_documented_v2_examples_are_valid(filename):
    path = Path(__file__).parents[1] / "fixtures" / "layout_v2" / filename
    result = load_layout_config(path)
    assert result["version"] == 2
    assert result["canvas"] == {"width": 1920, "height": 1080}


def test_complete_example_exercises_all_supported_elements():
    path = Path(__file__).parents[1] / "fixtures" / "layout_v2" / "complete.yaml"
    result = load_layout_config(path)
    assert len(result["song"]["elements"]) == 5
    assert len(result["title"]["elements"]) == 5


def test_one_element_never_infers_geometry_from_another():
    source = sample_layout()
    source["song"]["elements"]["beat_indicator"] = {"x": 1, "y": 2, "width": 100, "height": 100}
    before = validate_layout_config(source)["song"]["elements"]["chord_main"]
    source["song"]["elements"]["beat_indicator"]["x"] = 1500
    after = validate_layout_config(source)["song"]["elements"]["chord_main"]
    assert before == after


def test_v2_resolves_defaults_and_styles_without_mutating_overrides():
    source = sample_layout()
    original = deepcopy(source)
    result = validate_layout_config(source)
    assert result["canvas"] == {"width": 1920, "height": 1080}
    assert set(result["title"]["elements"]) == set(TITLE_ELEMENTS)
    assert set(result["song"]["elements"]) == set(SONG_ELEMENTS)
    assert result["song"]["elements"]["chord_main"]["style"]["size_px"] == 144
    assert result["song"]["elements"]["chord_main"]["style"]["font_family"] == "Inter"
    result["song"]["elements"]["chord_main"]["x"] = 200
    assert source == original


def test_v2_preserves_off_canvas_pixels_without_percentage_inference():
    source = sample_layout()
    source["song"]["elements"]["chord_main"].update(x=-30, y=0.5, width=2200)
    element = validate_layout_config(source)["song"]["elements"]["chord_main"]
    assert (element["x"], element["y"], element["width"]) == (-30, 0.5, 2200)


@pytest.mark.parametrize("key", ["x", "y", "width", "height"])
def test_v2_inherits_missing_rectangle_fields_from_the_shared_template(key):
    source = sample_layout()
    del source["song"]["elements"]["chord_main"][key]
    default = load_layout_config(DEFAULT_LAYOUT_PATH)["song"]["elements"]["chord_main"][key]
    assert validate_layout_config(source)["song"]["elements"]["chord_main"][key] == default


@pytest.mark.parametrize("value", [True, None, "30%", float("inf"), float("nan"), 10 ** 1000])
def test_v2_rejects_nonfinite_or_nonnumeric_coordinates(value):
    source = sample_layout()
    source["song"]["elements"]["chord_main"]["x"] = value
    with pytest.raises(SongConfigError, match="finite number"):
        validate_layout_config(source)


@pytest.mark.parametrize("field,value", [
    ("width", 0), ("height", -1), ("style", None),
    ("style", {"size_px": 0}), ("style", {"size_px": True}),
    ("style", {"weight": 450}), ("style", {"font_family": "Arial"}),
    ("style", {"color": "red"}), ("style", {"align": "justify"}),
    ("style", {"valign": "baseline"}), ("style", {"unexpected": 1}),
    ("enabled", "no"),
    ("appearance", {}),
])
def test_v2_rejects_invalid_element_settings(field, value):
    source = sample_layout()
    source["song"]["elements"]["chord_main"][field] = value
    with pytest.raises(SongConfigError):
        validate_layout_config(source)


@pytest.mark.parametrize("source", [
    {"version": 2, "regions": {}}, {"version": 2, "video": {}},
    {"version": 2, "canvas": {"width": 1280}},
    {"version": 2, "canvas": {"height": True}},
    {"version": 2, "song": {"elements": []}},
    {"version": 2, "song": {"elements": {"unknown": {}}}},
    {"version": 2, "title": {"enabled": "yes"}},
    {"version": 2, "title": {"countdown_beats": True}},
])
def test_v2_rejects_invalid_structure(source):
    with pytest.raises(SongConfigError):
        validate_layout_config(source)


def test_v2_accepts_empty_override_maps_and_nested_appearance_overrides():
    source = {"version": 2, "song": {"elements": {
        "next_chord": {"x": 1600, "y": 700, "width": 200, "height": 200,
                       "appearance": {"ring": {"color": "#ABC"}}},
    }}}
    result = validate_layout_config(source)
    assert set(enabled_elements(result, "title")) == set(TITLE_ELEMENTS)
    assert result["song"]["elements"]["next_chord"]["appearance"]["ring"]["width_px"] == 6
    assert set(enabled_elements(validate_layout_config({"version": 2}), "song")) == set(SONG_ELEMENTS)


def test_v2_metadata_visibility_is_explicit_and_boolean():
    element = {"x": 192, "y": 432, "width": 1536, "height": 238,
               "behavior": {"show_artist": False}}
    source = {"version": 2, "title": {"elements": {"metadata_text": element}}}
    behavior = validate_layout_config(source)["title"]["elements"]["metadata_text"]["behavior"]
    assert behavior == {"show_artist": False, "show_key": True, "show_hot_start": True}
    element["behavior"]["show_artist"] = "no"
    with pytest.raises(SongConfigError, match="must be a boolean"):
        validate_layout_config(source)


@pytest.mark.parametrize("layer", [
    {"type": "video", "file": "image.png"}, {"type": "image"},
    {"type": "image", "file": ""}, {"type": "image", "file": "image.png", "media": "id"},
    {"type": "image", "file": "image.png", "opacity": 1.1},
])
def test_v2_validates_background_layers(layer):
    with pytest.raises(SongConfigError):
        validate_layout_config({"version": 2, "background": {"layers": [layer]}})


def test_v2_preserves_file_and_stored_media_references():
    source = {"version": 2, "background": {"layers": [
        {"type": "image", "file": "background.png", "opacity": 0.5},
        {"type": "image", "media": "upload-id"},
    ]}}
    layers = validate_layout_config(source)["background"]["layers"]
    assert layers[0]["file"] == "background.png"
    assert layers[1]["media"] == "upload-id"
    assert layers[1]["opacity"] == 1.0
