from PIL import Image
import pytest

from praecentor.io.layout_v2 import validate_layout_config
from praecentor.render.lyrics_v2 import LyricScroll


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
    assert upcoming["top"] - block["bottom"] == 24
    assert any(item[1]["y"] + item[1]["height"] > 780 for item in placements)


def test_scroll_and_opacity_are_continuous_at_the_event_boundary():
    scroll = make_scroll([{"lyric": "First"}, {"lyric": "Second"}, {"lyric": "Third"}], [0, 10, 20])
    halfway = scroll.placements(9, 0)
    assert next(item[2] for item in halfway if item[0]["row"] == 0) == pytest.approx(0.625)
    assert next(item[2] for item in halfway if item[0]["row"] == 1) == pytest.approx(0.75)
    before = scroll.placements(10 - 1e-6, 0)
    after = scroll.placements(10, 1)
    for row in (0, 1):
        previous = next(item for item in before if item[0]["row"] == row)
        current = next(item for item in after if item[0]["row"] == row)
        assert previous[1]["y"] == pytest.approx(current[1]["y"], abs=0.001)
        assert previous[2] == pytest.approx(current[2], abs=0.001)
    assert next(item[2] for item in after if item[0]["row"] == 1) == 1
    assert next(item[2] for item in after if item[0]["row"] == 0) == 0.25


def test_long_break_uses_progress_instead_of_the_scroll():
    scroll = make_scroll([{"lyric": "Before"}, {"instruction": "Break"}, {"lyric": "After"}], [0, 5, 25])
    assert scroll.placements(10, 1) == []
    assert len(scroll.segments) == 2
    image = Image.new("RGBA", (1920, 1080))
    scroll.draw(image, 10, 1)
    assert image.getbbox() is not None
    assert scroll.placements(25, 2)[0][0]["text"] == "After"


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
    assert scroll.placements(20, 1) == []


def test_unrenderable_new_block_does_not_leave_previous_words_active():
    scroll = make_scroll([{"lyric": "A"}, {"lyric": "x" * 1000}], [0, 5],
                         element={"width": 20, "height": 10})
    assert scroll.event_blocks[0] is not None
    assert scroll.event_blocks[1] is None
    assert scroll.placements(6, 1) == []
