from __future__ import annotations

import pytest

from praecentor.controllers import conversion


def test_convert_lyrics_text_to_events_tracks_section_markers() -> None:
    events = conversion.convert_lyrics_text_to_events(
        "[Verse]\nThis is a longer lyric line",
        max_line_chars=8,
    )

    assert len(events) == 1
    assert events[0]["marker"] == "Verse"
    lyric_lines = events[0]["lyric"].splitlines()
    assert lyric_lines[0] == "This is"
    assert lyric_lines[-1] == "line"


def test_convert_lyrics_text_to_events_emits_instruction_for_lonely_section() -> None:
    events = conversion.convert_lyrics_text_to_events("[Bridge]\n\n[Outro]")

    assert events == [{"instruction": "Bridge"}, {"instruction": "Outro"}]


def test_dump_events_yaml_uses_stable_scalar_styles() -> None:
    events = conversion.convert_lyrics_text_to_events("[Verse]\nOne two three")

    dumped = conversion.dump_events_yaml(events)

    assert "marker: \"Verse\"" in dumped
    assert "lyric: |-" in dumped


def test_convert_logic_text_to_riff_supports_named_and_degree() -> None:
    text = "Note\tLength\nC4\t1\nD#4\t1/2\n"

    named = conversion.convert_logic_text_to_riff(text, notation="named")
    degree = conversion.convert_logic_text_to_riff(
        text, notation="degree", root_note="C"
    )

    assert named == "C1 D#0.5"
    assert degree == "1:1 2.5:0.5"


def test_convert_logic_text_to_riff_rejects_unknown_notation() -> None:
    text = "Note\tLength\nC4\t1\n"

    with pytest.raises(ValueError, match="Unsupported notation"):
        conversion.convert_logic_text_to_riff(text, notation="invalid")
