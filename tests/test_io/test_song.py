from __future__ import annotations

from pathlib import Path

import pytest

from praecentor.io.song import (
    SongConfigError,
    beats_tap_config,
    load_yaml_file,
    lyrics_tap_config,
)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_load_yaml_file_resolves_named_progressions(tmp_path: Path) -> None:
    song_path = _write(
        tmp_path / "song.yaml",
        "riffs:\n"
        "  intro: C1 D1\n"
        "events:\n"
        "  - lyric: Hello\n"
        "    riff: '@intro'\n",
    )

    data = load_yaml_file(song_path)

    assert data["events"][0]["riff"] == "C1 D1"


def test_load_yaml_file_rejects_empty_placeholder_label(tmp_path: Path) -> None:
    song_path = _write(
        tmp_path / "song.yaml",
        "riffs: { intro: C1 }\n"
        "events:\n"
        "  - lyric: Hello\n"
        "    riff: '@'\n",
    )

    with pytest.raises(SongConfigError, match="must include a label"):
        load_yaml_file(song_path)


def test_lyrics_tap_config_parses_and_validates_required_fields(tmp_path: Path) -> None:
    song_path = _write(
        tmp_path / "song.yaml",
        "audio:\n"
        "  file: backing.wav\n"
        "timing:\n"
        "  lyrics:\n"
        "    source: tap\n"
        "    map_file: maps/lyrics.yaml\n"
        "    base_offset_seconds: '0.25'\n"
        "events:\n"
        "  - lyric: Hello\n",
    )

    cfg = lyrics_tap_config(load_yaml_file(song_path), song_path)

    assert cfg["map_path"] == (tmp_path / "maps/lyrics.yaml").resolve()
    assert cfg["fallback_audio"] == "backing.wav"
    assert cfg["base_offset_seconds"] == 0.25
    assert len(cfg["events"]) == 1


def test_lyrics_tap_config_requires_exactly_one_text_field(tmp_path: Path) -> None:
    song_path = _write(
        tmp_path / "song.yaml",
        "timing:\n"
        "  lyrics:\n"
        "    source: tap\n"
        "    map_file: lyrics.yaml\n"
        "events:\n"
        "  - lyric: Hi\n"
        "    instruction: Both\n",
    )

    with pytest.raises(SongConfigError, match="exactly one"):
        lyrics_tap_config(load_yaml_file(song_path), song_path)


def test_beats_tap_config_requires_tap_source(tmp_path: Path) -> None:
    song_path = _write(
        tmp_path / "song.yaml",
        "timing:\n"
        "  beats:\n"
        "    source: auto\n"
        "    map_file: beats.yaml\n"
        "events:\n"
        "  - lyric: Hello\n",
    )

    with pytest.raises(SongConfigError, match="must be 'tap'"):
        beats_tap_config(load_yaml_file(song_path), song_path)
