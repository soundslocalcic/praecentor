"""V2 rendering boundary; frame drawing is introduced in roadmap phase 3."""

from praecentor.controllers.errors import ControllerError


def ensure_rendering_supported() -> None:
    raise ControllerError(
        "Layout version 2 is valid, but v2 frame rendering is not implemented yet. "
        "Use an explicit version: 1 legacy layout until the v2 renderer is available."
    )


def render_cue_frames(**kwargs):
    ensure_rendering_supported()


def render_title_countdown_frames(**kwargs):
    ensure_rendering_supported()


def build_background_layer_renderer(**kwargs):
    ensure_rendering_supported()
