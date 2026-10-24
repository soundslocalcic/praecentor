from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from praecentor.cli import main


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> Path:
    return (FIXTURES_DIR / name).resolve()


def test_happy_birthday_validate_smoke() -> None:
    song_path = _fixture("happy_birthday_validate.yaml")

    result = CliRunner().invoke(main, ["validate", str(song_path)])

    assert result.exit_code == 0
    assert "OK:" in result.output
    assert "5 events" in result.output


def test_happy_birthday_validate_with_named_progressions_smoke() -> None:
    song_path = _fixture("happy_birthday_progressions.yaml")

    result = CliRunner().invoke(main, ["validate", str(song_path)])

    assert result.exit_code == 0
    assert "4 events" in result.output


def test_happy_birthday_timeline_smoke() -> None:
    song_path = _fixture("happy_birthday_validate.yaml")

    result = CliRunner().invoke(main, ["timeline", str(song_path)])

    assert result.exit_code == 0
    assert "Timeline command scaffold" in result.output


def test_happy_birthday_tap_lyrics_dry_run_smoke() -> None:
    song_path = _fixture("happy_birthday_tap.yaml")

    result = CliRunner().invoke(
        main,
        [
            "tap",
            "lyrics",
            str(song_path),
            "--dry-run",
            "--autosave-every",
            "2",
            "--resume-preroll-seconds",
            "0.75",
        ],
    )

    assert result.exit_code == 0
    assert "tap_map=" in result.output
    assert "audio=" in result.output
    assert "event_count=4" in result.output
    assert "playback_start_seconds=" in result.output


def test_happy_birthday_tap_beats_dry_run_smoke() -> None:
    song_path = _fixture("happy_birthday_tap.yaml")

    result = CliRunner().invoke(
        main,
        [
            "tap",
            "beats",
            str(song_path),
            "--dry-run",
            "--autosave-every",
            "3",
            "--resume-preroll-seconds",
            "1.25",
        ],
    )

    assert result.exit_code == 0
    assert "beat_map=" in result.output
    assert "audio=" in result.output
    assert "captured_beat_count=0" in result.output
    assert "playback_start_seconds=" in result.output


def test_happy_birthday_convert_lyrics_smoke() -> None:
    lyrics_path = _fixture("happy_birthday_lyrics.txt")

    result = CliRunner().invoke(
        main,
        ["convert", "lyrics", str(lyrics_path), "--max-line-chars", "32"],
    )

    assert result.exit_code == 0
    assert "marker: \"Verse 1\"" in result.output
    assert "marker: \"Verse 2\"" in result.output
    assert "lyric: |-" in result.output
