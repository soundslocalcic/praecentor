from __future__ import annotations

from pathlib import Path

import click

from praecentor.controllers.errors import ControllerError
from praecentor.controllers import rendering as _impl

_visible_lyric_lines = _impl._visible_lyric_lines
_first_event_chord_preview = _impl._first_event_chord_preview


def _sync_module_overrides_into_controller() -> None:
    skip_names = {
        "_impl",
        "_sync_module_overrides_into_controller",
        "render_cmd",
        "_visible_lyric_lines",
        "_first_event_chord_preview",
        "__getattr__",
        "__all__",
        "__builtins__",
        "__doc__",
        "__file__",
        "__name__",
        "__package__",
        "__spec__",
    }
    for name, value in globals().items():
        if name in skip_names:
            continue
        if hasattr(_impl, name):
            setattr(_impl, name, value)


@click.command("render")
@click.argument(
    "song_file", type=click.Path(path_type=Path, exists=True, dir_okay=False)
)
@click.option(
    "--layout",
    "layout_file",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    required=False,
    help="Layout YAML file.",
)
@click.option(
    "--beat-seconds",
    type=click.FloatRange(min=0.001),
    required=False,
    help="Override beat duration in seconds for title-card countdown timing.",
)
@click.option(
    "--countdown-beats",
    type=click.IntRange(0),
    default=None,
    show_default=False,
    help="Number of beats shown on the title-card countdown before backing starts (0 disables countdown).",
)
@click.option(
    "--output-video",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Output MP4 path. Defaults to song.video.output.file, otherwise ~/song_stem.mp4.",
)
@click.option(
    "--from-seconds",
    type=str,
    default=None,
    help="Optional start time in MM:SS (or MM:SS.s) to render from within cue timeline.",
)
@click.option(
    "--to-seconds",
    type=str,
    default=None,
    help="Optional end time in MM:SS (or MM:SS.s) to render to within cue timeline.",
)
@click.option(
    "--maestro-only",
    is_flag=True,
    help="Write Maestro zip only and skip all video rendering/muxing.",
)
@click.option(
    "--video-encoder",
    type=click.Choice(
        ["libx264", "h264_videotoolbox", "hevc_videotoolbox"],
        case_sensitive=False,
    ),
    default=_impl.DEFAULT_VIDEO_ENCODER,
    show_default=True,
    help="Video encoder for ffmpeg. Use *_videotoolbox on macOS for GPU encoding.",
)
@click.option(
    "--video-preset",
    type=str,
    default=_impl.DEFAULT_VIDEO_PRESET,
    show_default=True,
    help="Encoder preset/speed setting (primarily used by libx264).",
)
@click.option(
    "--video-crf",
    type=click.IntRange(0, 51),
    default=None,
    show_default=False,
    help="Constant rate factor quality target for libx264 (lower is higher quality).",
)
@click.option(
    "--video-bitrate",
    type=str,
    default=None,
    show_default=False,
    help="Optional target bitrate (for example: 10M). Useful with hardware encoders.",
)
@click.option(
    "--background-hwaccel",
    type=str,
    default=None,
    show_default=False,
    help="Optional ffmpeg hwaccel mode for background video decode (for example: videotoolbox).",
)
@click.option("--frame-queue-size",
              type=click.IntRange(0),
              default=_impl.DEFAULT_FRAME_QUEUE_SIZE,
              show_default=True,
              help="Buffered frame queue depth before ffmpeg stdin writes (0 disables queue thread).",
              )
@click.option("--stem-duration-tolerance",
              type=click.FloatRange(min=0.0),
              default=_impl.DEFAULT_STEM_DURATION_TOLERANCE_SECONDS,
              show_default=True,
              help="Allow stem duration differences up to this many seconds before failing validation.",
              )
@click.option(
    "--ignore-stem-length-mismatch",
    is_flag=True,
    default=_impl.DEFAULT_IGNORE_STEM_LENGTH_MISMATCH,
    help="Skip the audio stem duration mismatch check entirely.",
)
@click.option("--apple-silicon-optimize/--no-apple-silicon-optimize",
              default=_impl.DEFAULT_APPLE_SILICON_OPTIMIZE,
              show_default=True,
              help="On macOS ARM, prefer videotoolbox encoder/decode defaults for faster renders.",
              )
def render_cmd(
    song_file: Path,
    layout_file: Path | None,
    beat_seconds: float | None,
    countdown_beats: int | None,
    output_video: Path | None,
    from_seconds: str | None,
    to_seconds: str | None,
    maestro_only: bool,
    video_encoder: str,
    video_preset: str,
    video_crf: int | None,
    video_bitrate: str | None,
    background_hwaccel: str | None,
    frame_queue_size: int,
    stem_duration_tolerance: float,
    ignore_stem_length_mismatch: bool,
    apple_silicon_optimize: bool,
) -> None:
    _sync_module_overrides_into_controller()
    try:
        _impl.render(
            song_file,
            layout_file,
            beat_seconds,
            countdown_beats,
            output_video,
            from_seconds,
            to_seconds,
            maestro_only,
            video_encoder,
            video_preset,
            video_crf,
            video_bitrate,
            background_hwaccel,
            frame_queue_size,
            stem_duration_tolerance,
            ignore_stem_length_mismatch,
            apple_silicon_optimize,
            emit=click.echo,
            progressbar=click.progressbar,
        )
    except ControllerError as exc:
        raise click.ClickException(str(exc)) from exc


def __getattr__(name: str):
    return getattr(_impl, name)


__all__ = ["render_cmd", "_visible_lyric_lines", "_first_event_chord_preview"]
