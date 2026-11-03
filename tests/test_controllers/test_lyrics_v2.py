from PIL import Image
import pytest

from praecentor.io.layout_v2 import validate_layout_config
from praecentor.render.lyrics_v2 import LyricScroll, _edge_mask
from praecentor.render.text_v2 import composite_element


def make_scroll(events, starts, **kwargs):
    raw = {"x": 100, "y": 100, "width": 1000, "height": 680, "style": {"size_px": 80},
           "behavior": {"scroll_preview_beats": 2, "exit_beats": 0}, **kwargs.pop("element", {})}
    element = validate_layout_config({"version": 2, "song": {"elements": {"lyric_main": raw}}})[
        "song"]["elements"]["lyric_main"]
    return LyricScroll(element, events, starts, kwargs.pop("tail_seconds", 5), [{} for event in events],
                       beat_seconds=1, **kwargs)


def test_current_lyrics_are_centered_and_neighbours_keep_their_own_heights():
    scroll = make_scroll([{"lyric": "Current\nFour\nLine\nBlock"}, {"lyric": "Next\nHas\nFive\nWhole\nLines"}], [0, 10])
    placements = scroll.placements(2, 0)
    block, placed, alpha = next(item for item in placements if item[0]["row"] == 0)
    assert block["size"] == 80
    assert placed["y"] + placed["height"] / 2 == pytest.approx(440)
    assert alpha == 1
    upcoming = scroll.blocks[1]
    assert upcoming["size"] == 80
    assert upcoming["top"] - block["bottom"] == 141
    assert any(item[1]["y"] + item[1]["height"] > 780 for item in placements)


def test_scroll_and_opacity_are_continuous_at_the_event_boundary():
    scroll = make_scroll([{"lyric": "First"}, {"lyric": "Second"}, {"lyric": "Third"}], [0, 10, 20])
    halfway = scroll.placements(9, 0)
    assert next(item[2] for item in halfway if item[0]["row"] == 0) == pytest.approx(0.5)
    assert next(item[2] for item in halfway if item[0]["row"] == 1) == pytest.approx(0.75)
    before = scroll.placements(10 - 1e-6, 0)
    after = scroll.placements(10, 1)
    for row in (0, 1):
        previous = next(item for item in before if item[0]["row"] == row)
        current = next(item for item in after if item[0]["row"] == row)
        assert previous[1]["y"] == pytest.approx(current[1]["y"], abs=0.001)
        assert previous[2] == pytest.approx(current[2], abs=0.001)
    assert next(item[2] for item in after if item[0]["row"] == 1) == 1
    assert next(item[2] for item in after if item[0]["row"] == 0) == 0


def test_lead_in_beats_bring_the_next_lyric_in_before_its_event():
    scroll = make_scroll([{"lyric": "First"}, {"lyric": "Second"}, {"lyric": "Third"}], [0, 10, 20],
                         lead_in_beats=3)
    halfway = scroll.placements(6, 0)
    assert next(item[2] for item in halfway if item[0]["row"] == 1) == pytest.approx(0.75)
    arrived = scroll.placements(7, 0)
    block, placed, alpha = next(item for item in arrived if item[0]["row"] == 1)
    assert placed["y"] + placed["height"] / 2 == pytest.approx(440)
    assert alpha == 1
    before = scroll.placements(10 - 1e-6, 0)
    after = scroll.placements(10, 1)
    for row in (0, 1):
        previous = next(item for item in before if item[0]["row"] == row)
        current = next(item for item in after if item[0]["row"] == row)
        assert previous[1]["y"] == pytest.approx(current[1]["y"], abs=0.001)
        assert previous[2] == pytest.approx(current[2], abs=0.001)


def test_long_break_progress_is_a_centered_block_in_the_same_scroll():
    scroll = make_scroll([{"lyric": "Before"}, {"instruction": "Break"}, {"lyric": "After"}], [0, 5, 25])
    block, placed, alpha = next(item for item in scroll.placements(10, 1) if item[0]["kind"] == "progress")
    assert block["window"] == ("break", 5, 25)
    assert placed["y"] + placed["height"] / 2 == pytest.approx(440)
    assert alpha == 1
    assert len(scroll.segments) == 1
    image = Image.new("RGBA", (1920, 1080))
    scroll.draw(image, 10, 1)
    assert image.getbbox() is not None
    assert any(item[0]["text"] == "After" and item[2] == 1 for item in scroll.placements(25, 2))


def test_short_instructions_remain_in_the_scroll_and_chord_only_events_do_not_clear_it():
    scroll = make_scroll([{"lyric": "Same line"}, {"chords": "G"}, {"lyric": "Same line"},
                          {"instruction": "Instrumental"}], [0, 2, 4, 6])
    assert len(scroll.blocks) == 2
    assert scroll.event_blocks[0] is scroll.event_blocks[1] is scroll.event_blocks[2]
    assert scroll.blocks[1]["text"] == "(Instrumental)"


def test_optional_entrance_and_final_exit_clear_the_viewport():
    scroll = make_scroll([{"lyric": "One final line"}], [0], tail_seconds=5,
                         element={"behavior": {"entrance_beats": 1, "exit_beats": 1}}, fps=4)
    assert scroll.placements(0, 0) == []
    assert scroll.placements(1, 0)
    assert scroll.placements(4.75, 0) == []


def test_long_outro_includes_the_postlude_in_its_progress_window():
    scroll = make_scroll([{"lyric": "Final line"}, {"instruction": "Outro"}], [0, 5],
                         tail_seconds=2, postlude_seconds=20)
    assert scroll.breaks[1] == ("outro", 5, 27)
    assert any(item[0]["kind"] == "progress" and item[2] == 1 for item in scroll.placements(20, 1))


def test_unrenderable_new_block_does_not_leave_previous_words_active():
    scroll = make_scroll([{"lyric": "A"}, {"lyric": "x" * 1000}], [0, 5],
                         element={"width": 20, "height": 10})
    assert scroll.event_blocks[0] is not None
    assert scroll.event_blocks[1] is None
    assert scroll.placements(6, 1) == []


def test_default_line_height_and_rem_gap_use_the_fixed_canvas_reference():
    config = validate_layout_config({"version": 2})
    lyric = config["song"]["elements"]["lyric_main"]
    assert lyric["style"]["line_height"] == 1.2
    assert config["song"]["elements"]["chord_main"]["style"]["line_height"] == 1
    scroll = LyricScroll(lyric, [{"lyric": "First"}, {"instruction": "Next"}], [0, 5], 5, [{}, {}], beat_seconds=1)
    assert scroll.margin == 141
    assert scroll.blocks[1]["top"] - scroll.blocks[0]["bottom"] == 141
    assert scroll.blocks[1]["size"] == 94
    smaller = make_scroll([{"lyric": "First"}, {"lyric": "Second"}], [0, 5])
    assert smaller.margin == 141


def test_explicit_em_gap_still_scales_with_the_lyric_font():
    scroll = make_scroll([{"lyric": "First"}, {"lyric": "Second"}], [0, 5],
                         element={"behavior": {"event_margin_em": 0.5}})
    assert scroll.margin == 40


def test_explicit_pixel_gap_takes_precedence_over_em_gap():
    scroll = make_scroll([{"lyric": "First"}, {"lyric": "Second"}], [0, 5],
                         element={"behavior": {"preview_margin_px": 12, "event_margin_em": 2}})
    assert scroll.margin == 12
    assert scroll.blocks[1]["top"] - scroll.blocks[0]["bottom"] == 12


def test_progress_blocks_scroll_and_fade_continuously_at_both_break_boundaries():
    scroll = make_scroll([{"lyric": "Before"}, {"instruction": "Break"}, {"lyric": "After"}], [0, 5, 25])
    assert [block["kind"] for block in scroll.blocks] == ["text", "progress", "text"]
    for boundary, before_index, after_index in ((5, 0, 1), (25, 1, 2)):
        before = scroll.placements(boundary - 1e-6, before_index)
        after = scroll.placements(boundary, after_index)
        for row in (before_index, after_index):
            previous = next(item for item in before if item[0]["row"] == row)
            current = next(item for item in after if item[0]["row"] == row)
            assert previous[1]["y"] == pytest.approx(current[1]["y"], abs=0.001)
            assert previous[2] == pytest.approx(current[2], abs=0.001)
    halfway_in = next(item for item in scroll.placements(4, 0) if item[0]["kind"] == "progress")
    assert halfway_in[2] == pytest.approx(0.75)
    outgoing = next(item for item in scroll.placements(25, 2) if item[0]["kind"] == "progress")
    assert outgoing[2] == 0


def test_consecutive_break_events_share_one_progress_block_and_drawing_is_clipped():
    scroll = make_scroll([{"lyric": "Before"}, {"instruction": "Break"}, {"instruction": "Break"},
                          {"lyric": "After"}], [0, 5, 10, 25])
    assert len(scroll.blocks) == 3
    assert scroll.event_blocks[1] is scroll.event_blocks[2]
    for now, index in ((4.5, 0), (15, 2), (25, 3)):
        image = Image.new("RGBA", (1920, 1080))
        scroll.draw(image, now, index)
        left, top, right, bottom = image.getbbox()
        assert 100 <= left < right <= 1100
        assert 100 <= top < bottom <= 780


def test_chord_only_events_do_not_shorten_a_scrolling_progress_window():
    scroll = make_scroll([{"lyric": "Before"}, {"instruction": "Break"}, {"chords": "G"},
                          {"lyric": "After"}], [0, 5, 10, 25])
    assert len(scroll.blocks) == 3
    assert scroll.blocks[1]["kind"] == "progress"
    assert scroll.blocks[1]["window"] == ("break", 5, 25)
    assert scroll.event_blocks[1] is scroll.event_blocks[2]


def test_scroll_edge_mask_fades_alpha_instead_of_painting_background_colours():
    element = {"x": 0, "y": 0, "width": 100, "height": 200}
    mask = _edge_mask(100, 200, 40, 0, 200)
    overlay = Image.new("RGBA", (100, 200), (255, 255, 255, 128))
    transparent = Image.new("RGBA", (100, 200))
    composite_element(transparent, overlay, element, alpha_mask=mask)
    assert transparent.getpixel((50, 0))[3] == 0
    assert 60 <= transparent.getpixel((50, 20))[3] <= 65
    assert transparent.getpixel((50, 100))[3] == 128
    assert transparent.getpixel((50, 199))[3] == 0
    red = Image.new("RGB", (100, 200), "#990000")
    composite_element(red, overlay, element, alpha_mask=mask)
    assert red.getpixel((50, 0)) == (153, 0, 0)
    assert red.getpixel((50, 199)) == (153, 0, 0)
    assert red.getpixel((50, 20))[1] < red.getpixel((50, 100))[1]


def test_scroll_draws_with_the_viewport_edge_mask_and_can_disable_it(monkeypatch):
    from praecentor.render import lyrics_v2
    actual = lyrics_v2.composite_element
    masks = []

    def record(image, overlay, element, **kwargs):
        masks.append(kwargs["alpha_mask"])
        return actual(image, overlay, element, **kwargs)

    monkeypatch.setattr(lyrics_v2, "composite_element", record)
    scroll = make_scroll([{"lyric": "Visible"}], [0])
    scroll.draw(Image.new("RGBA", (1920, 1080)), 1, 0)
    assert masks[-1].getpixel((0, 0)) == 0
    assert masks[-1].getpixel((0, masks[-1].height // 2)) == 255
    scroll = make_scroll([{"lyric": "Visible"}], [0], element={"behavior": {"edge_fade_px": 0}})
    scroll.draw(Image.new("RGBA", (1920, 1080)), 1, 0)
    assert masks[-1] is None


@pytest.mark.parametrize("viewport_y", [0, 172])
def test_default_mask_has_double_depth_for_top_or_bottom_lyric_viewports(viewport_y):
    config = validate_layout_config({"version": 2})
    depth = config["song"]["elements"]["lyric_main"]["behavior"]["edge_fade_px"]
    assert depth == 282
    element = {"x": 0, "y": viewport_y, "width": 100, "height": 908}
    mask = _edge_mask(100, 908, depth, 0, 908)
    assert mask.getpixel((50, 0)) == 0
    assert mask.getpixel((50, 141)) == 128
    assert mask.getpixel((50, 282)) == 255
    assert mask.getpixel((50, 907 - 141)) == 128
    assert mask.getpixel((50, 907)) == 0
    image = Image.new("RGB", (100, 1080), "#990000")
    overlay = Image.new("RGBA", image.size, (255, 255, 255, 255))
    composite_element(image, overlay, element, alpha_mask=mask)
    assert image.getpixel((50, viewport_y)) == (153, 0, 0)
    assert image.getpixel((50, viewport_y + 907)) == (153, 0, 0)
    assert image.getpixel((50, viewport_y + 141))[1] == 128
    assert image.getpixel((50, viewport_y + 454)) == (255, 255, 255)
