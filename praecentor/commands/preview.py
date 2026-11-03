from pathlib import Path

import click
from PIL import Image

from praecentor.controllers.errors import ControllerError
from praecentor.controllers.preview import SAMPLES, preview_layout


@click.command("preview-layout")
@click.argument("layout_file", required=False, type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option("--sample", type=click.Choice(["all", *SAMPLES]), default="all", show_default=True)
@click.option(
    "--output", type=click.Path(path_type=Path, dir_okay=False), default="layout-preview.png", show_default=True,
)
@click.option("--font-dir", type=click.Path(path_type=Path, exists=True, file_okay=False), help="Inter font directory.")
@click.option(
    "--background", type=click.Path(path_type=Path, exists=True, dir_okay=False), help="Override the preview image.",
)
@click.option("--guides", is_flag=True, help="Draw element rectangles and pixel coordinates.")
@click.option("--show", is_flag=True, help="Open the saved PNG in a local image viewer using Pillow.")
def preview_cmd(layout_file, sample, output, font_dir, background, guides, show):
    """Preview LAYOUT_FILE itself, not a song YAML; omit it for the packaged draft."""
    try:
        result = preview_layout(
            layout_file, sample=sample, output=output, font_dir=font_dir, background=background, guides=guides,
        )
        if result["draft_default"]:
            click.echo("Using the approved all-elements v2 default template.")
        for font in result["fonts"]:
            click.echo(f"Font [{font['role']}]: {font['family']} {font['variant']} ({font['file']})")
        click.echo(f"Preview: {result['output']}")
        for name, filename in result["frames"].items():
            click.echo(f"  {name}: {filename}")
        click.echo(f"Report: {result['metadata_file']}")
        if show:
            with Image.open(result["output"]) as image:
                image.show(title="Praecentor layout preview")
    except (ControllerError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
