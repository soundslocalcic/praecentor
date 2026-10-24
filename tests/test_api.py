from __future__ import annotations

from pathlib import Path

from praecentor import api as api_module
from praecentor.models.requests import (
    ConvertLyricsRequest,
    DiagnoseBeatsRequest,
    RenderRequest,
    TapBeatsRequest,
    TapLyricsRequest,
)


def test_api_wrappers_delegate_to_controllers(
    monkeypatch,
) -> None:
    render_calls: dict[str, object] = {}

    def fake_render(*args, **kwargs):
        render_calls["args"] = args
        render_calls["kwargs"] = kwargs
        return "rendered"

    monkeypatch.setattr(api_module.rendering, "render", fake_render)
    monkeypatch.setattr(
        api_module.diagnostics,
        "diagnose_beats",
        lambda *args, **kwargs: "diagnosed",
    )
    monkeypatch.setattr(
        api_module.tapping,
        "tap_lyrics",
        lambda *args, **kwargs: "tap-lyrics",
    )
    monkeypatch.setattr(
        api_module.tapping,
        "tap_beats",
        lambda *args, **kwargs: "tap-beats",
    )

    render_request = RenderRequest(song_file=Path("song.yaml"))
    assert api_module.render_video(render_request) == "rendered"
    assert render_calls["args"][0] == Path("song.yaml")

    assert (
        api_module.tap_lyrics(TapLyricsRequest(song_file=Path("song.yaml")))
        == "tap-lyrics"
    )
    assert (
        api_module.tap_beats(TapBeatsRequest(song_file=Path("song.yaml")))
        == "tap-beats"
    )
    assert api_module.convert_lyrics(ConvertLyricsRequest(text="hello")) == [
        {"lyric": "hello"}
    ]
    assert (
        api_module.diagnose_beats(
            DiagnoseBeatsRequest(song_file=Path("song.yaml"))
        )
        == "diagnosed"
    )
