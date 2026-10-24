from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from praecentor.cli import main
from praecentor.controllers.errors import ControllerError
import praecentor.commands.validate as validate_command


def test_validate_command_invokes_controller(
    tmp_path: Path, monkeypatch
) -> None:
    song_path = tmp_path / "song.yaml"
    song_path.write_text("events: []\n", encoding="utf-8")
    seen: dict[str, object] = {}

    def fake_validate(path: Path, *, emit):
        seen["path"] = path
        emit("OK")

    monkeypatch.setattr(validate_command, "validate_song", fake_validate)

    result = CliRunner().invoke(main, ["validate", str(song_path)])

    assert result.exit_code == 0
    assert seen["path"] == song_path
    assert "OK" in result.output


def test_validate_command_wraps_controller_errors(
    tmp_path: Path, monkeypatch
) -> None:
    song_path = tmp_path / "song.yaml"
    song_path.write_text("events: []\n", encoding="utf-8")

    def boom(_path: Path, *, emit):
        raise ControllerError("bad config")

    monkeypatch.setattr(validate_command, "validate_song", boom)

    result = CliRunner().invoke(main, ["validate", str(song_path)])

    assert result.exit_code != 0
    assert "bad config" in result.output
