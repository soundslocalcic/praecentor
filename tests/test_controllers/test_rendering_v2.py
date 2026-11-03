from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image, ImageChops
import pytest
import yaml

from praecentor.controllers import rendering
from praecentor.io.layout_v2 import SONG_ELEMENTS, TITLE_ELEMENTS, validate_layout_config
from praecentor.io.song import SongConfigError
from praecentor.render import v2
from praecentor.render.text_v2 import draw_text, fit_text
from praecentor.render import text_v2
from praecentor.render import lyrics_v2


def text_element(**overrides):
    raw = {"x": 100, "y": 100, "width": 600, "height": 180,
           "style": {"size_px": 76}, **overrides}
    return validate_layout_config({"version": 2, "song": {"elements": {"chord_main": raw}}})[
        "song"]["elements"]["chord_main"]


def test_v2_text_never_grows_and_can_shrink_for_long_rows():
    element = text_element()
    assert fit_text("C", element)[0] == 76
    assert fit_text("Am, C, F, B♭, Dm, G, E, Am, C, F", element)[0] < 76
    moved = {**element, "x": 500, "y": -20}
    assert fit_text("Am, C, F", moved)[0] == fit_text("Am, C, F", element)[0]
    assert fit_text("C", text_element(width=1800, height=900))[0] == 76


@pytest.mark.parametrize("align", ["left", "center", "right"])
@pytest.mark.parametrize("valign", ["top", "center", "bottom"])
def test_v2_alignment_and_mixed_accidentals_stay_in_the_rectangle(align, valign):
    element = text_element(style={"size_px": 90, "align": align, "valign": valign})
    original = deepcopy(element)
    image = Image.new("RGBA", (1920, 1080))
    size = draw_text(image, "Bbm\nC#", element, normalize_flats=True)
    bounds = image.getbbox()
    assert size <= 90
    assert bounds is not None
    assert 100 <= bounds[0] < bounds[2] <= 700
    assert 100 <= bounds[1] < bounds[3] <= 280
    assert element == original


def test_v2_text_off_canvas_is_clipped_without_repositioning():
    image = Image.new("RGBA", (1920, 1080))
    element = text_element(x=-20, y=-10, width=200, height=120, style={"size_px": 90, "align": "left", "valign": "top"})
    draw_text(image, "Bbm", element, normalize_flats=True)
    assert image.getbbox() is not None
    assert image.getbbox()[0] == 0


def test_v2_transparent_text_preserves_opacity_once():
    image = Image.new("RGBA", (1920, 1080))
    draw_text(image, "C", text_element(), opacity=0.5)
    assert image.getchannel("A").getextrema()[1] == 128


def test_v2_unchanged_text_is_measured_and_drawn_once_for_moving_fading_frames(monkeypatch):
    text_v2._fit_cached.cache_clear()
    text_v2._sprite.cache_clear()
    measure = Mock(wraps=text_v2._layout)
    monkeypatch.setattr(text_v2, "_layout", measure)
    element = text_element()
    for index in range(12):
        moved = {**element, "y": element["y"] + index}
        draw_text(Image.new("RGBA", (1920, 1080)), "Stable chord text", moved, opacity=0.5 + index / 24)
    assert text_v2._sprite.cache_info().misses == 1
    assert text_v2._fit_cached.cache_info().misses == 1
    assert measure.call_count <= 8


def test_v2_beat_pie_sweeps_clockwise_and_its_elapsed_area_is_transparent():
    element = validate_layout_config({"version": 2})["song"]["elements"]["beat_indicator"]
    element["appearance"].update(pulse_expand_px=0, pulse_width_px=1)
    center_x = round(element["x"] + element["width"] / 2)
    center_y = round(element["y"] + element["height"] / 2)
    quarter = Image.new("RGBA", (1920, 1080))
    v2._beat(quarter, element, 1, 1, None, None, 0, 4)
    assert quarter.getpixel((center_x + 40, center_y - 40))[3] == 0
    assert quarter.getpixel((center_x - 40, center_y - 40))[3] == 255
    assert quarter.getpixel((center_x + 40, center_y + 40))[3] == 255
    later = Image.new("RGB", (1920, 1080), "#123456")
    v2._beat(later, element, 3, 1, None, None, 0, 4)
    assert later.getpixel((center_x + 40, center_y - 40)) == (18, 52, 86)
    assert later.getpixel((center_x - 40, center_y + 40)) == (18, 52, 86)
    assert later.getpixel((center_x - 40, center_y - 40)) != (18, 52, 86)


def test_v2_repeat_indicator_counts_phrases_until_the_distant_chord_change(tmp_path, monkeypatch):
    config = {"version": 2, "song": {"elements": {}}}
    only_song_elements(config, ("next_chord",))
    calls = []
    actual = v2._next_chord

    def record(image, element, token, remaining, **kwargs):
        calls.append((token, remaining, kwargs["loop"]))
        return actual(image, element, token, remaining, **kwargs)

    monkeypatch.setattr(v2, "_next_chord", record)
    v2.render_cue_frames(
        layout_data=config, output_dir=tmp_path, fps=1,
        song_events=[{"chords": "C"}, {"chords": "G"}], event_starts_seconds=[0, 100],
        tail_seconds=1, beat_seconds=1, default_beats_per_phrase=4,
        frame_sink=lambda image, index: None,
    )
    assert len(calls) == 101
    assert calls[0] == ("", 1, True)
    assert calls[1] == ("", 0.75, True)
    assert calls[4] == ("", 1, True)
    assert calls[95][2] is True
    assert calls[96] == ("G", 1, False)


def test_v2_phrase_clock_follows_tempo_changes_and_event_phrase_overrides(tmp_path, monkeypatch):
    config = {"version": 2, "song": {"elements": {}}}
    only_song_elements(config, ("next_chord",))
    calls = []
    monkeypatch.setattr(v2, "_next_chord", lambda image, element, token, remaining, **kwargs: calls.append(remaining))
    v2.render_cue_frames(
        layout_data=config, output_dir=tmp_path, fps=4,
        song_events=[{"chords": "C", "beats_per_phrase": 4}], event_starts_seconds=[0],
        tail_seconds=3, beat_seconds=0.5, beat_times_seconds=[0, 0.5, 1, 1.5, 2, 2.25, 2.5, 2.75, 3],
        default_beats_per_phrase=8, frame_sink=lambda image, index: None,
    )
    assert calls[4] == 0.5
    assert calls[8] == 1
    assert calls[10] == 0.5


def test_v2_repeat_countdown_applies_extra_beats_to_one_phrase_only(tmp_path, monkeypatch):
    config = {"version": 2, "song": {"elements": {}}}
    only_song_elements(config, ("next_chord",))
    calls = []
    monkeypatch.setattr(v2, "_next_chord", lambda image, element, token, remaining, **kwargs: calls.append(remaining))
    v2.render_cue_frames(
        layout_data=config, output_dir=tmp_path, fps=1,
        song_events=[{"chords": "C", "extra_beats": 2}], event_starts_seconds=[0],
        tail_seconds=11, beat_seconds=1, default_beats_per_phrase=4,
        frame_sink=lambda image, index: None,
    )
    assert calls[0] == 1
    assert calls[4] == pytest.approx(1 / 3)
    assert calls[6] == 1
    assert calls[7] == 0.75
    assert calls[10] == 1


def sample_config():
    return yaml.safe_load((Path(__file__).parents[1] / "fixtures" / "layout_v2" / "complete.yaml").read_text())


def only_song_elements(config, names):
    current = config["song"]["elements"]
    config["song"]["elements"] = {
        name: current.get(name, {}) if name in names else {"enabled": False} for name in SONG_ELEMENTS
    }


def render_cues(tmp_path, config, **kwargs):
    frames = []
    arguments = dict(
        layout_data=config, output_dir=tmp_path, fps=2,
        song_events=[
            {"lyric": "[a] First line\n[b] Second line", "chords": "C", "warning": "next"},
            {"lyric": "Following line", "chords": "Am, C, F, Bb, Dm, G, E"},
            {"instruction": "Instrumental", "riff": "1 3 5"},
            {"lyric": "Final line", "chords": "G"},
        ],
        event_starts_seconds=[0, 1, 2, 3], tail_seconds=1, postlude_seconds=1,
        keep_postlude_beat_clock=True,
        frame_sink=lambda image, index: frames.append(image.copy()),
    )
    arguments.update(kwargs)
    result = v2.render_cue_frames(**arguments)
    return result, frames


def test_v2_renders_title_cues_riffs_warning_and_postlude(tmp_path, monkeypatch):
    config = sample_config()
    original = deepcopy(config)
    callback = Mock()
    actual_draw = v2.draw_text
    sizes = []

    def checked_draw(image, text, element, **kwargs):
        size = actual_draw(image, text, element, **kwargs)
        if size is not None:
            assert size <= element["style"]["size_px"]
            sizes.append(size)
        return size

    monkeypatch.setattr(v2, "draw_text", checked_draw)
    riff = Mock(wraps=v2.state._draw_riff_timeline)
    warning = Mock(wraps=v2.state._warning_icon_image)
    monkeypatch.setattr(v2.state, "_draw_riff_timeline", riff)
    monkeypatch.setattr(v2.state, "_warning_icon_image", warning)
    result, frames = render_cues(tmp_path, config, frame_progress_callback=callback)
    assert result["frame_count"] == len(frames) == callback.call_count == 10
    assert all(frame.size == (1920, 1080) for frame in frames)
    assert frames[0].tobytes() != frames[4].tobytes()
    assert frames[-1].getbbox() is not None
    assert riff.called and warning.called and sizes
    title_frames = []
    result = v2.render_title_countdown_frames(
        layout_data=config, output_dir=tmp_path, fps=2, countdown_beats=2,
        title_text="V2 title", artist_text="Artist", key_text="Bb major", hot_start=True,
        first_chord_preview_text="Bb", first_chord_preview_lead_beats=1,
        frame_sink=lambda image, index: title_frames.append(image.copy()),
    )
    assert result["frame_count"] == len(title_frames) == 4
    assert title_frames[0].tobytes() != title_frames[-1].tobytes()
    assert config == original


def test_v2_disabled_elements_keep_background_and_timing(tmp_path):
    config = {"version": 2, "background": {"color": "#123456"}, "song": {"enabled": False}}
    result, frames = render_cues(tmp_path, config)
    assert result["frame_count"] == 10
    assert all(frame.getextrema() == ((18, 18), (52, 52), (86, 86)) for frame in frames)


def test_v2_version_only_layout_renders_the_shared_defaults(tmp_path):
    from praecentor.io.layout_v2 import DEFAULT_LAYOUT_PATH
    sparse = {"version": 2}
    complete = yaml.safe_load(DEFAULT_LAYOUT_PATH.read_text())
    kwargs = {"song_events": [{"lyric": "Visible default lyrics", "chords": "C"}],
              "event_starts_seconds": [0], "postlude_seconds": 0}
    _result, before = render_cues(tmp_path, sparse, **kwargs)
    _result, after = render_cues(tmp_path, complete, **kwargs)
    assert ImageChops.difference(before[0], after[0]).getbbox() is None
    assert before[0].getextrema()[0][1] > 100
    assert sparse == {"version": 2}
    title_frames = []
    v2.render_title_countdown_frames(
        layout_data=sparse, output_dir=tmp_path, fps=1, countdown_beats=1, title_text="Default title",
        frame_sink=lambda image, index: title_frames.append(image.copy()),
    )
    assert title_frames[0].getextrema()[0][1] > 100


def test_v2_moving_the_beat_indicator_does_not_change_lyric_pixels(tmp_path):
    config = sample_config()
    only_song_elements(config, ("lyric_main", "beat_indicator"))
    kwargs = {"song_events": [{"lyric": "Unchanged lyrics"}], "event_starts_seconds": [0], "postlude_seconds": 0}
    _result, before = render_cues(tmp_path, config, **kwargs)
    config["song"]["elements"]["beat_indicator"].update(x=1600, y=700)
    _result, after = render_cues(tmp_path, config, **kwargs)
    crop = (20, 0, 1540, 760)
    assert ImageChops.difference(before[0].crop(crop), after[0].crop(crop)).getbbox() is None


def test_v2_disabled_elements_do_not_draw_or_suppress_chords(tmp_path, monkeypatch):
    config = {"version": 2, "song": {"elements": {"chord_main": {
        "x": 300, "y": 700, "width": 600, "height": 200,
    }}}}
    only_song_elements(config, ("chord_main",))
    riff = Mock()
    monkeypatch.setattr(v2.state, "_draw_riff_timeline", riff)
    result, frames = render_cues(tmp_path, config, song_events=[{"chords": "C", "riff": "1 3 5"}],
                                 event_starts_seconds=[0], postlude_seconds=0, background_is_transparent=True)
    assert result["frame_count"] == 2
    bounds = frames[0].getbbox()
    assert bounds is not None and 300 <= bounds[0] < bounds[2] <= 900
    assert 700 <= bounds[1] < bounds[3] <= 900
    riff.assert_not_called()


@pytest.mark.parametrize("name", ["beat_indicator", "next_chord", "riff"])
def test_v2_decorations_and_riffs_never_paint_outside_their_rectangle(tmp_path, name):
    config = sample_config()
    element = config["song"]["elements"][name]
    only_song_elements(config, (name,))
    _result, frames = render_cues(tmp_path, config, background_is_transparent=True)
    bounds = [frame.getbbox() for frame in frames if frame.getbbox() is not None]
    assert bounds
    for left, top, right, bottom in bounds:
        assert element["x"] <= left < right <= element["x"] + element["width"]
        assert element["y"] <= top < bottom <= element["y"] + element["height"]


def test_v2_rendering_rejects_noncanonical_output_dimensions(tmp_path):
    with pytest.raises(SongConfigError, match="1920x1080"):
        v2.render_title_countdown_frames(layout_data={"version": 2}, output_dir=tmp_path, width=1280, height=720)


def test_v2_background_layers_keep_order_opacity_and_paths(tmp_path):
    Image.new("RGB", (40, 40), "#FF0000").save(tmp_path / "red.png")
    Image.new("RGBA", (40, 40), (0, 0, 255, 255)).save(tmp_path / "blue.png")
    config = {"version": 2, "background": {"layers": [
        {"type": "image", "file": "red.png"},
        {"type": "image", "file": "blue.png", "opacity": 0.5},
    ]}}
    original = deepcopy(config)
    provider = v2.build_background_layer_renderer(layout_data=config, layout_path=tmp_path / "layout.yaml")
    try:
        assert provider.next_frame().getpixel((0, 0)) == (127, 0, 128)
    finally:
        provider.close()
    assert config == original
    with pytest.raises(SongConfigError, match="Resolve v2 background media"):
        v2.build_background_layer_renderer(layout_data={"version": 2, "background": {"layers": [
            {"type": "image", "media": "upload-id"}]}}, layout_path=tmp_path / "layout.yaml")


def test_v2_lookahead_preserves_explicit_silence(tmp_path, monkeypatch):
    config = sample_config()
    preview = Mock(wraps=v2._next_chord)
    monkeypatch.setattr(v2, "_next_chord", preview)
    render_cues(tmp_path, config, song_events=[{"chords": "C"}, {"chords": ""}, {"chords": "G"}],
                event_starts_seconds=[0, 1, 2], postlude_seconds=0)
    assert preview.call_args_list[0].args[2] == ""
    assert any(call.args[2] == "G" for call in preview.call_args_list)


def test_v2_postlude_keeps_last_visible_lyric(tmp_path, monkeypatch):
    draw = Mock(wraps=lyrics_v2.draw_text)
    monkeypatch.setattr(lyrics_v2, "draw_text", draw)
    config = sample_config()
    config["song"]["elements"]["lyric_main"]["behavior"]["exit_beats"] = 0
    render_cues(tmp_path, config, song_events=[{"lyric": "Retained line"}, {"chords": "G"}],
                event_starts_seconds=[0, 1])
    assert draw.call_args.args[1] == "Retained line"


def test_v2_warning_flashing_does_not_change_text_geometry(monkeypatch):
    element = validate_layout_config(sample_config())["song"]["elements"]["next_chord"]
    draw = Mock(wraps=v2.draw_text)
    monkeypatch.setattr(v2, "draw_text", draw)
    for now in (0, 0.3):
        v2._next_chord(Image.new("RGBA", (1920, 1080)), element, "Bb", 0.5, warning=True, at_seconds=now)
    assert draw.call_args_list[0].args[2] == draw.call_args_list[1].args[2]


def test_v2_default_loop_icon_is_half_the_ring_footprint(monkeypatch):
    element = validate_layout_config({"version": 2})["song"]["elements"]["next_chord"]
    icon = Mock(wraps=v2.state._loop_icon_image)
    monkeypatch.setattr(v2.state, "_loop_icon_image", icon)
    v2._next_chord(Image.new("RGBA", (1920, 1080)), element, "", 1, loop=True)
    assert 115 <= icon.call_args.args[0] <= 125
    assert element["width"] == element["height"] == 240


def test_v2_default_lyrics_are_centered_above_an_aligned_bottom_row():
    config = validate_layout_config({"version": 2})
    elements = config["song"]["elements"]
    lyric = elements["lyric_main"]
    assert lyric["x"] + lyric["width"] / 2 == 960
    assert lyric["style"]["align"] == "center"
    for name in ("chord_main", "riff", "next_chord"):
        element = elements[name]
        assert element["y"] == 788
        assert element["height"] == 240
        assert element["y"] + element["height"] / 2 == 908
    assert elements["chord_main"]["x"] + elements["chord_main"]["width"] < elements["next_chord"]["x"]
    assert elements["next_chord"]["x"] + elements["next_chord"]["width"] < elements["beat_indicator"]["x"]
    assert lyric["y"] == 0
    assert lyric["y"] + lyric["height"] == elements["chord_main"]["y"] + elements["chord_main"]["height"] / 2
    assert elements["chord_main"]["style"]["align"] == "left"
    beat = elements["beat_indicator"]
    assert beat["width"] == beat["height"] == 300
    assert beat["y"] + beat["height"] / 2 == 908
    assert config["title"]["elements"]["beat_indicator"] == beat


def test_v2_settled_title_lookahead_matches_event_lookahead_pixels(tmp_path):
    raw = {"version": 2, "title": {"elements": {
        name: {"enabled": name == "first_chord_preview"} for name in TITLE_ELEMENTS
    }}}
    config = validate_layout_config(raw)
    title = config["title"]["elements"]["first_chord_preview"]
    event = config["song"]["elements"]["next_chord"]
    for key in ("x", "y", "width", "height", "style"):
        assert title[key] == event[key]
    assert title["appearance"]["ring"] == event["appearance"]["ring"]
    frames = []
    v2.render_title_countdown_frames(
        layout_data=raw, output_dir=tmp_path, fps=1, countdown_beats=4,
        first_chord_preview_text="Bb", first_chord_preview_lead_beats=1,
        background_is_transparent=True, frame_sink=lambda image, index: frames.append(image.copy()),
    )
    expected = Image.new("RGBA", (1920, 1080))
    v2._next_chord(expected, event, "Bb", 0.25)
    assert ImageChops.difference(frames[-1], expected).getbbox(alpha_only=False) is None


def test_v2_title_beat_indicator_uses_the_same_appearance_and_clock(tmp_path):
    config = {"version": 2, "title": {"elements": {
        name: {"enabled": name == "beat_indicator"} for name in TITLE_ELEMENTS
    }}}
    frames = []
    v2.render_title_countdown_frames(
        layout_data=config, output_dir=tmp_path, fps=2, countdown_beats=1, beats_per_bar=3,
        background_is_transparent=True, frame_sink=lambda image, index: frames.append(image.copy()),
    )
    element = validate_layout_config(config)["song"]["elements"]["beat_indicator"]
    expected = Image.new("RGBA", (1920, 1080))
    v2._beat(expected, element, 0.5, 1, None, None, 0, 3)
    assert ImageChops.difference(frames[1], expected).getbbox(alpha_only=False) is None
    assert frames[0].tobytes() != frames[1].tobytes()


def test_v2_oversized_warning_gap_cannot_draw_text_outside_the_element():
    element = validate_layout_config(sample_config())["song"]["elements"]["next_chord"]
    element["appearance"]["warning"]["gap_px"] = 1000
    image = Image.new("RGBA", (1920, 1080))
    v2._next_chord(image, element, "Bb", 0.5, warning=True)
    left, top, right, bottom = image.getbbox()
    assert element["x"] <= left < right <= element["x"] + element["width"]
    assert element["y"] <= top < bottom <= element["y"] + element["height"]


def test_v2_zero_riff_postroll_switches_to_chords_at_the_event_boundary(tmp_path, monkeypatch):
    config = sample_config()
    only_song_elements(config, ("chord_main", "riff"))
    config["song"]["elements"]["riff"]["appearance"].update(pre_roll_beats=0, post_roll_beats=0)
    frames = []
    calls = []
    actual_draw = v2.draw_text

    def capture(image, text, element, **kwargs):
        calls.append((len(frames), text))
        return actual_draw(image, text, element, **kwargs)

    monkeypatch.setattr(v2, "draw_text", capture)
    render_cues(tmp_path, config, song_events=[{"riff": "1 3 5"}, {"chords": "C"}],
                event_starts_seconds=[0, 1], postlude_seconds=0,
                frame_sink=lambda image, index: frames.append(index))
    assert (2, "C") in calls


def test_controller_renders_v2_with_its_own_canvas_and_background(tmp_path, monkeypatch):
    layout_file = tmp_path / "layout.yaml"
    layout_file.write_text(yaml.safe_dump({"version": 2, "background": {"color": "#123456"},
                                          "title": {"enabled": False}}))
    (tmp_path / "audio.wav").write_bytes(b"audio")
    (tmp_path / "taps.yaml").write_text("version: 1\nevents:\n  - index: 0\n    at_seconds: 0\n")
    song_file = tmp_path / "song.yaml"
    song_file.write_text(yaml.safe_dump({
        "version": 1, "audio": {"file": "audio.wav"},
        "video": {"layout": {"file": str(layout_file)}, "output": {"file": "output.mp4"}},
        "timing": {"countdown_beats": 0, "lyrics": {"source": "tap", "map_file": "taps.yaml"},
                   "beats": {"source": "manual_grid", "bpm": 120}}, "events": [{"lyric": "Line"}],
    }))
    captured = []

    class Encoder:
        def __init__(self, **kwargs):
            pass

        def frame_sink(self, image, index):
            if not captured:
                captured.append(image.copy())

        def finish_writing(self):
            pass

        def wait_and_validate(self, **kwargs):
            pass

    monkeypatch.setattr(rendering, "_DirectFrameEncodeSession", Encoder)
    monkeypatch.setattr(rendering, "_probe_audio_duration_seconds", lambda *args: 1.0)
    monkeypatch.setattr(rendering.shutil, "which", lambda binary: "/usr/bin/ffmpeg")
    monkeypatch.setattr(rendering.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(rendering, "_run_mux_for_target", lambda **kwargs: (1, kwargs["target"]["file"], True, "", 0.0))
    rendering.render(song_file)
    assert captured[0].size == (1920, 1080)
    assert captured[0].getpixel((0, 0)) == (18, 52, 86)
