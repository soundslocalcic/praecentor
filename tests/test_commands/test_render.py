from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from praecentor.cli import main
from praecentor.controllers.errors import ControllerError
import praecentor.commands.render as render_command


def _song_file(tmp_path: Path) -> Path:
    path = tmp_path / "song.yaml"
    path.write_text("events: []\n", encoding="utf-8")
    return path


def test_render_command_delegates_to_controller(
    tmp_path: Path, monkeypatch
) -> None:
    song_path = _song_file(tmp_path)
    seen: dict[str, object] = {}

    def fake_render(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs

    monkeypatch.setattr(render_command._impl, "render", fake_render)

    result = CliRunner().invoke(main, ["render", str(song_path), "--maestro-only"])

    assert result.exit_code == 0
    assert seen["args"][0] == song_path
    assert seen["args"][7] is True
    assert callable(seen["kwargs"]["emit"])
    assert callable(seen["kwargs"]["progressbar"])


def test_render_command_wraps_controller_error(
    tmp_path: Path, monkeypatch
) -> None:
    song_path = _song_file(tmp_path)

    def boom(*args, **kwargs):
        raise ControllerError("render failed")

    monkeypatch.setattr(render_command._impl, "render", boom)

    result = CliRunner().invoke(main, ["render", str(song_path)])

    assert result.exit_code != 0
    assert "render failed" in result.output
