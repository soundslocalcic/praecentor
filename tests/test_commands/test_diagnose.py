from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from praecentor.cli import main
from praecentor.controllers.errors import ControllerError
import praecentor.commands.diagnose as diagnose_command


def _song_file(tmp_path: Path) -> Path:
    path = tmp_path / "song.yaml"
    path.write_text("events: []\n", encoding="utf-8")
    return path


def test_diagnose_beats_command_delegates_to_controller(
    tmp_path: Path, monkeypatch
) -> None:
    song_path = _song_file(tmp_path)
    seen: dict[str, object] = {}

    def fake_diagnose(song_file: Path, **kwargs):
        seen["song_file"] = song_file
        seen.update(kwargs)

    monkeypatch.setattr(
        diagnose_command.diagnostics_controller,
        "diagnose_beats",
        fake_diagnose,
    )

    result = CliRunner().invoke(main, ["diagnose", "beats", str(song_path)])

    assert result.exit_code == 0
    assert seen["song_file"] == song_path
    assert callable(seen["emit"])


def test_diagnose_beats_command_wraps_controller_error(
    tmp_path: Path, monkeypatch
) -> None:
    song_path = _song_file(tmp_path)

    def boom(*args, **kwargs):
        raise ControllerError("diagnose failed")

    monkeypatch.setattr(
        diagnose_command.diagnostics_controller,
        "diagnose_beats",
        boom,
    )

    result = CliRunner().invoke(main, ["diagnose", "beats", str(song_path)])

    assert result.exit_code != 0
    assert "diagnose failed" in result.output
