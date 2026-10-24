from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from praecentor.cli import main
import praecentor.commands.timeline as timeline_command


def test_timeline_command_delegates_to_controller(
    tmp_path: Path, monkeypatch
) -> None:
    song_path = tmp_path / "song.yaml"
    song_path.write_text("events: []\n", encoding="utf-8")
    seen: dict[str, object] = {}

    def fake_timeline(path: Path, *, emit):
        seen["path"] = path
        emit("timeline ok")

    monkeypatch.setattr(timeline_command, "inspect_timeline", fake_timeline)

    result = CliRunner().invoke(main, ["timeline", str(song_path)])

    assert result.exit_code == 0
    assert seen["path"] == song_path
    assert "timeline ok" in result.output
