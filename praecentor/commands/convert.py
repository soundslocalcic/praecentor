from __future__ import annotations

from typing import Any

import click

from praecentor.controllers import conversion as conversion_controller


@click.group("convert")
def convert_cmd() -> None:
    """Convert plain-text authoring formats into song YAML snippets."""


@convert_cmd.command("lyrics")
@click.argument(
    "lyrics_file", type=click.File("r", encoding="utf-8"), required=False
)
@click.option(
    "--max-line-chars",
    type=click.IntRange(1),
    default=32,
    show_default=True,
    help="Maximum characters per rendered lyric line.",
)
def convert_lyrics_cmd(lyrics_file: Any, max_line_chars: int) -> None:
    text = (
        click.get_text_stream("stdin").read()
        if lyrics_file is None
        else lyrics_file.read()
    )
    events = conversion_controller.convert_lyrics_text_to_events(
        text, max_line_chars=max_line_chars
    )
    click.get_text_stream("stdout").write(
        conversion_controller.dump_events_yaml(events)
    )


@convert_cmd.command("riff")
@click.argument(
    "notes_file", type=click.File("r", encoding="utf-8"), required=False
)
@click.option(
    "--notation",
    type=click.Choice(["named", "degree", "legacy"], case_sensitive=False),
    default="named",
    show_default=True,
    help="Output style: named note+duration tokens, explicit degree tokens, or Feed-style legacy separators.",
)
@click.option(
    "--root-note",
    default="C",
    show_default=True,
    help="Reference root for degree/legacy notation (for example D makes G map to degree 4).",
)
@click.option(
    "--beats-per-bar",
    type=click.IntRange(1),
    default=4,
    show_default=True,
    help="Beats per bar when parsing Logic bar-beat-division-tick duration values.",
)
@click.option(
    "--divisions-per-beat",
    type=click.IntRange(1),
    default=4,
    show_default=True,
    help="Divisions per beat when parsing Logic bar-beat-division-tick duration values.",
)
@click.option(
    "--ticks-per-beat",
    type=click.IntRange(1),
    default=960,
    show_default=True,
    help="Ticks per beat when parsing Logic bar-beat-division-tick duration values.",
)
@click.option(
    "--keep-octave",
    is_flag=True,
    help="Keep octave numbers in named notation output.",
)
def convert_riff_cmd(
    notes_file: Any,
    notation: str,
    root_note: str,
    beats_per_bar: int,
    divisions_per_beat: int,
    ticks_per_beat: int,
    keep_octave: bool,
) -> None:
    text = (
        click.get_text_stream("stdin").read()
        if notes_file is None
        else notes_file.read()
    )

    try:
        riff = conversion_controller.convert_logic_text_to_riff(
            text,
            notation=notation.lower(),
            root_note=root_note,
            beats_per_bar=beats_per_bar,
            divisions_per_beat=divisions_per_beat,
            ticks_per_beat=ticks_per_beat,
            keep_octave=keep_octave,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(riff)
