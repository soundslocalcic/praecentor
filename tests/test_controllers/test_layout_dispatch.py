from copy import deepcopy
from unittest.mock import Mock

import pytest

from praecentor.controllers import rendering
from praecentor.controllers.errors import ControllerError
from praecentor.io.song import SongConfigError
from praecentor.render import layout, v1, v2


@pytest.mark.parametrize("config", [{}, {"version": 1}])
def test_direct_cue_calls_use_frozen_v1(config, monkeypatch):
    render = Mock(return_value={"frame_count": 1})
    monkeypatch.setattr(v1, "render_cue_frames", render)
    assert layout.render_cue_frames(layout_data=config, width=1920) == {"frame_count": 1}
    render.assert_called_once_with(layout_data=config, width=1920)
    assert render.call_args.kwargs["layout_data"] is not config


def test_direct_v2_calls_receive_validated_separate_copies(monkeypatch):
    config = {"version": 2}
    original = deepcopy(config)
    render = Mock(return_value={"frame_count": 1})
    monkeypatch.setattr(v2, "render_cue_frames", render)
    layout.render_cue_frames(layout_data=config, width=1920)
    assert render.call_args.kwargs["layout_data"]["canvas"]["width"] == 1920
    assert config == original


def test_title_dispatch_preserves_legacy_subsection_calls(monkeypatch):
    render = Mock(return_value={"frame_count": 1})
    monkeypatch.setattr(v1, "render_title_countdown_frames", render)
    layout.render_title_countdown_frames(title_layout={"show_key": False}, width=1920)
    render.assert_called_once_with(title_layout={"show_key": False}, width=1920)


def test_title_dispatch_uses_v2_root_config(monkeypatch):
    render = Mock(return_value={"frame_count": 1})
    monkeypatch.setattr(v2, "render_title_countdown_frames", render)
    layout.render_title_countdown_frames(layout_data={"version": 2}, width=1920)
    assert render.call_args.kwargs["layout_data"]["version"] == 2
    with pytest.raises(SongConfigError, match="full layout_data"):
        layout.render_title_countdown_frames(layout_data={"version": 2}, title_layout={})


@pytest.mark.parametrize("call", [
    lambda: layout.render_cue_frames(layout_data={"version": 2}),
    lambda: layout.render_title_countdown_frames(layout_data={"version": 2}),
    lambda: layout.build_background_layer_renderer(layout_data={"version": 2}),
])
def test_v2_never_silently_renders_through_v1(call):
    with pytest.raises(ControllerError, match="not implemented yet"):
        call()


def test_controller_rejects_v2_before_audio_or_frame_work(tmp_path, monkeypatch):
    song = tmp_path / "song.yaml"
    song.write_text("events: []\n", encoding="utf-8")
    monkeypatch.setattr(rendering, "resolve_layout_path", lambda path: tmp_path / "layout.yaml")
    monkeypatch.setattr(rendering, "load_layout_config", lambda path: {"version": 2})
    with pytest.raises(ControllerError, match="not implemented yet"):
        rendering.render(song)


@pytest.mark.parametrize("version", [True, "2", 3, None])
def test_direct_calls_reject_unknown_versions(version):
    with pytest.raises(SongConfigError, match="layout.version"):
        layout.render_cue_frames(layout_data={"version": version})
