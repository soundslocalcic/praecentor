from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from praecentor.cli import main
from praecentor.controllers.errors import ControllerError
import praecentor.commands.tap as tap_command


def _song_file(tmp_path: Path) -> Path:
    path = tmp_path / "song.yaml"
    path.write_text("events: []\n", encoding="utf-8")
    return path


def test_tap_lyrics_command_passes_options(
    tmp_path: Path, monkeypatch
) -> None:
    song_path = _song_file(tmp_path)
    audio_path = tmp_path / "audio.wav"
    seen: dict[str, object] = {}

    def fake_tap_lyrics(song_file: Path, **kwargs):
        seen["song_file"] = song_file
        seen.update(kwargs)

    monkeypatch.setattr(tap_command.tapping_controller, "tap_lyrics", fake_tap_lyrics)

    result = CliRunner().invoke(
        main,
        [
            "tap",
            "lyrics",
            str(song_path),
            "--audio",
            str(audio_path),
            "--autosave-every",
            "3",
            "--resume-preroll-seconds",
            "1.25",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert seen["song_file"] == song_path
    assert seen["audio_override"] == audio_path
    assert seen["autosave_every"] == 3
    assert seen["resume_preroll_seconds"] == 1.25
    assert seen["dry_run"] is True
    assert callable(seen["emit"])
    assert callable(seen["read_input"])


def test_tap_beats_command_wraps_controller_error(
    tmp_path: Path, monkeypatch
) -> None:
    song_path = _song_file(tmp_path)

    def boom(*args, **kwargs):
        raise ControllerError("tap failed")

    monkeypatch.setattr(tap_command.tapping_controller, "tap_beats", boom)

    result = CliRunner().invoke(main, ["tap", "beats", str(song_path)])

    assert result.exit_code != 0
    assert "tap failed" in result.output
