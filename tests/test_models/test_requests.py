from __future__ import annotations

from pathlib import Path

import pytest

from praecentor.models.requests import RenderRequest, TapLyricsRequest


def test_render_request_defaults() -> None:
    request = RenderRequest(song_file=Path("song.yaml"))

    assert request.video_encoder == "libx264"
    assert request.video_preset == "medium"
    assert request.frame_queue_size == 0
    assert request.ignore_stem_length_mismatch is False


def test_request_dataclasses_use_slots() -> None:
    request = TapLyricsRequest(song_file=Path("song.yaml"))

    with pytest.raises(AttributeError):
        request.new_field = "not-allowed"
