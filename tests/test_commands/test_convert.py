from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from praecentor.cli import main
import praecentor.commands.convert as convert_command


def test_convert_lyrics_reads_file_and_writes_yaml(
    tmp_path: Path, monkeypatch
) -> None:
    lyrics_path = tmp_path / "lyrics.txt"
    lyrics_path.write_text("Hello world\n", encoding="utf-8")

    monkeypatch.setattr(
        convert_command.conversion_controller,
        "convert_lyrics_text_to_events",
        lambda text, max_line_chars: [{"lyric": text.strip(), "mlc": max_line_chars}],
    )
    monkeypatch.setattr(
        convert_command.conversion_controller,
        "dump_events_yaml",
        lambda events: f"events={len(events)}\n",
    )

    result = CliRunner().invoke(
        main,
        ["convert", "lyrics", str(lyrics_path), "--max-line-chars", "20"],
    )

    assert result.exit_code == 0
    assert result.output == "events=1\n"


def test_convert_lyrics_reads_from_stdin(monkeypatch) -> None:
    monkeypatch.setattr(
        convert_command.conversion_controller,
        "convert_lyrics_text_to_events",
        lambda text, max_line_chars: [{"lyric": text, "mlc": max_line_chars}],
    )
    monkeypatch.setattr(
        convert_command.conversion_controller,
        "dump_events_yaml",
        lambda events: "ok\n",
    )

    result = CliRunner().invoke(main, ["convert", "lyrics"], input="Line\n")

    assert result.exit_code == 0
    assert result.output == "ok\n"


def test_convert_riff_wraps_value_errors_as_click_exceptions(monkeypatch) -> None:
    monkeypatch.setattr(
        convert_command.conversion_controller,
        "convert_logic_text_to_riff",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("invalid notes")),
    )

    result = CliRunner().invoke(main, ["convert", "riff"], input="bad")

    assert result.exit_code != 0
    assert "invalid notes" in result.output
