from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from praecentor.controllers import tapping
from praecentor.controllers.errors import ControllerError
from praecentor.io.song import SongConfigError


class _FakePlayer:
    def __init__(self, *, wait_raises_timeout: bool = False) -> None:
        self.terminated = False
        self.killed = False
        self.wait_raises_timeout = wait_raises_timeout

    def poll(self):
        return None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float) -> None:
        if self.wait_raises_timeout:
            raise subprocess.TimeoutExpired("fake-player", timeout)

    def kill(self) -> None:
        self.killed = True


class _CapturedPopen:
    def __init__(self, cmd, **kwargs) -> None:
        self.cmd = cmd
        self.kwargs = kwargs


def _song_path(tmp_path: Path) -> Path:
    song = tmp_path / "song.yaml"
    song.write_text("events: []\n", encoding="utf-8")
    return song


def test_effective_chords_tracks_last_nonempty_value() -> None:
    events = [
        {"lyric": "a", "chords": "C"},
        {"lyric": "b", "chords": 42},
        {"lyric": "c", "chords": ""},
        {"lyric": "d", "chords": "G"},
    ]

    assert tapping._effective_chords(events, 1) == "C"
    assert tapping._effective_chords(events, 2) == ""
    assert tapping._effective_chords(events, 3) == "G"


def test_event_text_prefers_lyric_then_instruction() -> None:
    assert tapping._event_text({"lyric": "Line"}) == "Line"
    assert tapping._event_text({"instruction": "Bridge"}) == "(Bridge)"
    assert tapping._event_text({"instruction": ""}) == ""


def test_resolve_audio_uses_override(tmp_path: Path) -> None:
    override = tmp_path / "override.wav"

    path, text = tapping._resolve_audio(
        audio_override=override,
        fallback_audio_path=tmp_path / "fallback.wav",
    )

    assert path == override.resolve()
    assert text == str(override.resolve())


def test_resolve_audio_requires_any_source() -> None:
    with pytest.raises(ControllerError, match="No playback audio available"):
        tapping._resolve_audio(audio_override=None, fallback_audio_path=None)


def test_start_audio_process_uses_ffplay_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        tapping.shutil,
        "which",
        lambda name: "/usr/bin/ffplay" if name == "ffplay" else None,
    )

    def _fake_popen(cmd, **kwargs):
        captured["proc"] = _CapturedPopen(cmd, **kwargs)
        return captured["proc"]

    monkeypatch.setattr(tapping.subprocess, "Popen", _fake_popen)

    proc, start, used_seek = tapping._start_audio_process(
        tmp_path / "song.wav", 1.5
    )

    assert used_seek is True
    assert start == 1.5
    assert proc is captured["proc"]
    assert "-ss" in proc.cmd


def test_start_audio_process_falls_back_to_afplay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        tapping.shutil,
        "which",
        lambda name: "/usr/bin/afplay" if name == "afplay" else None,
    )

    def _fake_popen(cmd, **kwargs):
        captured["proc"] = _CapturedPopen(cmd, **kwargs)
        return captured["proc"]

    monkeypatch.setattr(tapping.subprocess, "Popen", _fake_popen)

    proc, start, used_seek = tapping._start_audio_process(
        tmp_path / "song.wav", 9.0
    )

    assert used_seek is False
    assert start == 0.0
    assert proc is captured["proc"]
    assert proc.cmd[0] == "/usr/bin/afplay"


def test_start_audio_process_returns_none_without_players(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tapping.shutil, "which", lambda _name: None)

    proc, start, used_seek = tapping._start_audio_process(
        tmp_path / "song.wav", 2.0
    )

    assert proc is None
    assert start == 2.0
    assert used_seek is False


def test_tap_lyrics_dry_run_emits_resume_information(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_path(tmp_path)
    audio_path = tmp_path / "happy.wav"
    map_path = tmp_path / "lyrics-map.yaml"

    monkeypatch.setattr(tapping, "load_yaml_file", lambda _path: {})
    monkeypatch.setattr(
        tapping,
        "lyrics_tap_config",
        lambda _song_data, _song_path: {
            "events": [{"lyric": "a"}, {"lyric": "b"}],
            "fallback_audio_path": audio_path,
            "map_path": map_path,
            "base_offset_seconds": 0.0,
        },
    )
    monkeypatch.setattr(
        tapping,
        "load_tap_map",
        lambda _map_path, event_count: {"event_map": {0: 3.5}},
    )

    emitted: list[str] = []
    tapping.tap_lyrics(
        song_file,
        audio_override=None,
        autosave_every=2,
        resume_preroll_seconds=1.25,
        dry_run=True,
        emit=emitted.append,
    )

    assert any("resume_index=1" in line for line in emitted)
    assert any("resume_seconds=3.500000" in line for line in emitted)
    assert any("playback_start_seconds=2.250000" in line for line in emitted)


def test_tap_lyrics_missing_audio_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_path(tmp_path)

    monkeypatch.setattr(tapping, "load_yaml_file", lambda _path: {})
    monkeypatch.setattr(
        tapping,
        "lyrics_tap_config",
        lambda _song_data, _song_path: {
            "events": [{"lyric": "a"}],
            "fallback_audio_path": tmp_path / "missing.wav",
            "map_path": tmp_path / "lyrics-map.yaml",
            "base_offset_seconds": 0.0,
        },
    )
    monkeypatch.setattr(
        tapping,
        "load_tap_map",
        lambda _map_path, event_count: {"event_map": {}},
    )

    with pytest.raises(ControllerError, match="Playback audio not found"):
        tapping.tap_lyrics(
            song_file,
            audio_override=None,
            autosave_every=1,
            resume_preroll_seconds=0.0,
            dry_run=False,
        )


def test_tap_lyrics_returns_when_all_events_stamped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_path(tmp_path)
    audio_path = tmp_path / "happy.wav"
    audio_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(tapping, "load_yaml_file", lambda _path: {})
    monkeypatch.setattr(
        tapping,
        "lyrics_tap_config",
        lambda _song_data, _song_path: {
            "events": [{"lyric": "a"}, {"lyric": "b"}],
            "fallback_audio_path": audio_path,
            "map_path": tmp_path / "lyrics-map.yaml",
            "base_offset_seconds": 0.0,
        },
    )
    monkeypatch.setattr(
        tapping,
        "load_tap_map",
        lambda _map_path, event_count: {"event_map": {0: 0.0, 1: 1.0}},
    )

    emitted: list[str] = []
    tapping.tap_lyrics(
        song_file,
        audio_override=None,
        autosave_every=1,
        resume_preroll_seconds=0.0,
        dry_run=False,
        emit=emitted.append,
    )

    assert any("Nothing to capture" in line for line in emitted)


def test_tap_lyrics_interactive_autostamps_and_saves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_path(tmp_path)
    audio_path = tmp_path / "happy.wav"
    audio_path.write_text("", encoding="utf-8")
    map_path = tmp_path / "lyrics-map.yaml"
    saved: list[dict[int, float]] = []

    monkeypatch.setattr(tapping, "load_yaml_file", lambda _path: {})
    monkeypatch.setattr(
        tapping,
        "lyrics_tap_config",
        lambda _song_data, _song_path: {
            "events": [{"lyric": "a"}, {"lyric": "b"}],
            "fallback_audio_path": audio_path,
            "map_path": map_path,
            "base_offset_seconds": 0.0,
        },
    )
    monkeypatch.setattr(
        tapping,
        "load_tap_map",
        lambda _map_path, event_count: {"event_map": {}},
    )
    monkeypatch.setattr(
        tapping,
        "_start_audio_process",
        lambda _audio_path, _start: (None, 1.25, False),
    )
    monkeypatch.setattr(
        tapping,
        "save_tap_map",
        lambda _path, **kwargs: saved.append(dict(kwargs["event_map"])),
    )
    monkeypatch.setattr(tapping.time, "perf_counter", lambda: 10.0)

    answers = iter(["", "q"])
    emitted: list[str] = []
    tapping.tap_lyrics(
        song_file,
        audio_override=None,
        autosave_every=2,
        resume_preroll_seconds=0.0,
        dry_run=False,
        emit=emitted.append,
        read_input=lambda _prompt: next(answers),
    )

    assert saved
    assert saved[-1][0] == 1.25
    assert any("Auto-stamped cue 1/2" in line for line in emitted)


def test_tap_lyrics_wraps_tap_map_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_path(tmp_path)
    audio_path = tmp_path / "happy.wav"

    monkeypatch.setattr(tapping, "load_yaml_file", lambda _path: {})
    monkeypatch.setattr(
        tapping,
        "lyrics_tap_config",
        lambda _song_data, _song_path: {
            "events": [{"lyric": "a"}],
            "fallback_audio_path": audio_path,
            "map_path": tmp_path / "lyrics-map.yaml",
            "base_offset_seconds": 0.0,
        },
    )

    def _raise(_map_path: Path, event_count: int):
        raise SongConfigError("bad map")

    monkeypatch.setattr(tapping, "load_tap_map", _raise)

    with pytest.raises(ControllerError, match="bad map"):
        tapping.tap_lyrics(
            song_file,
            audio_override=None,
            autosave_every=1,
            resume_preroll_seconds=0.0,
            dry_run=True,
        )


def test_tap_lyrics_warning_unknown_input_autosave_and_final_cue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_path(tmp_path)
    audio_path = tmp_path / "happy.wav"
    audio_path.write_text("", encoding="utf-8")
    map_path = tmp_path / "lyrics-map.yaml"
    map_path.write_text("existing\n", encoding="utf-8")
    emitted: list[str] = []
    saves: list[dict[int, float]] = []

    monkeypatch.setattr(tapping, "load_yaml_file", lambda _path: {})
    monkeypatch.setattr(
        tapping,
        "lyrics_tap_config",
        lambda _song_data, _song_path: {
            "events": [{"lyric": "a"}, {"lyric": "b"}],
            "fallback_audio_path": audio_path,
            "map_path": map_path,
            "base_offset_seconds": 0.0,
        },
    )
    monkeypatch.setattr(
        tapping,
        "load_tap_map",
        lambda _map_path, event_count: {"event_map": {0: 1.0}},
    )
    monkeypatch.setattr(
        tapping,
        "_start_audio_process",
        lambda _audio_path, _start: (_FakePlayer(), 0.0, False),
    )
    monkeypatch.setattr(
        tapping,
        "save_tap_map",
        lambda _path, **kwargs: saves.append(dict(kwargs["event_map"])),
    )

    ticks = iter([100.0, 100.2, 100.4])
    monkeypatch.setattr(tapping.time, "perf_counter", lambda: next(ticks))
    answers = iter(["x", "", "", "q"])
    tapping.tap_lyrics(
        song_file,
        audio_override=None,
        autosave_every=1,
        resume_preroll_seconds=0.5,
        dry_run=False,
        emit=emitted.append,
        read_input=lambda _prompt: next(answers),
    )

    assert any("Warning: current player cannot seek" in line for line in emitted)
    assert any("Unknown input. Use Enter to advance" in line for line in emitted)
    assert any("Autosaved:" in line for line in emitted)
    assert any("Reached final cue." in line for line in emitted)
    assert any("Next: (end)" in line for line in emitted)
    assert saves


def test_tap_lyrics_kills_player_after_wait_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_path(tmp_path)
    audio_path = tmp_path / "happy.wav"
    audio_path.write_text("", encoding="utf-8")
    map_path = tmp_path / "lyrics-map.yaml"
    map_path.write_text("existing\n", encoding="utf-8")
    player = _FakePlayer(wait_raises_timeout=True)

    monkeypatch.setattr(tapping, "load_yaml_file", lambda _path: {})
    monkeypatch.setattr(
        tapping,
        "lyrics_tap_config",
        lambda _song_data, _song_path: {
            "events": [{"lyric": "a"}, {"lyric": "b"}],
            "fallback_audio_path": audio_path,
            "map_path": map_path,
            "base_offset_seconds": 0.0,
        },
    )
    monkeypatch.setattr(
        tapping,
        "load_tap_map",
        lambda _map_path, event_count: {"event_map": {0: 0.0}},
    )
    monkeypatch.setattr(
        tapping,
        "_start_audio_process",
        lambda _audio_path, _start: (player, 0.0, True),
    )
    monkeypatch.setattr(tapping, "save_tap_map", lambda _path, **kwargs: None)
    monkeypatch.setattr(tapping.time, "perf_counter", lambda: 10.0)

    tapping.tap_lyrics(
        song_file,
        audio_override=None,
        autosave_every=10,
        resume_preroll_seconds=0.0,
        dry_run=False,
        read_input=lambda _prompt: "q",
    )

    assert player.terminated is True
    assert player.killed is True


def test_tap_lyrics_wraps_song_config_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_path(tmp_path)

    def _raise(_path: Path):
        raise SongConfigError("bad yaml")

    monkeypatch.setattr(tapping, "load_yaml_file", _raise)

    with pytest.raises(ControllerError, match="bad yaml"):
        tapping.tap_lyrics(
            song_file,
            audio_override=None,
            autosave_every=1,
            resume_preroll_seconds=0.0,
            dry_run=True,
        )


def test_tap_beats_dry_run_reports_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_path(tmp_path)
    audio_path = tmp_path / "beats.wav"

    monkeypatch.setattr(tapping, "load_yaml_file", lambda _path: {})
    monkeypatch.setattr(
        tapping,
        "beats_tap_config",
        lambda _song_data, _song_path: {
            "fallback_audio_path": audio_path,
            "map_path": tmp_path / "beats-map.yaml",
        },
    )
    monkeypatch.setattr(
        tapping,
        "load_beat_tap_map",
        lambda _map_path: {"event_map": {0: 0.5, 1: 1.0, 3: 2.0}},
    )

    emitted: list[str] = []
    tapping.tap_beats(
        song_file,
        audio_override=None,
        autosave_every=3,
        resume_preroll_seconds=0.25,
        dry_run=True,
        emit=emitted.append,
    )

    assert any("captured_beat_count=3" in line for line in emitted)
    assert any("resume_index=2" in line for line in emitted)
    assert any("playback_start_seconds=0.750000" in line for line in emitted)


def test_tap_beats_interactive_autosaves_and_terminates_player(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_path(tmp_path)
    audio_path = tmp_path / "beats.wav"
    audio_path.write_text("", encoding="utf-8")
    map_path = tmp_path / "beats-map.yaml"
    saved: list[dict[int, float]] = []
    player = _FakePlayer()

    monkeypatch.setattr(tapping, "load_yaml_file", lambda _path: {})
    monkeypatch.setattr(
        tapping,
        "beats_tap_config",
        lambda _song_data, _song_path: {
            "fallback_audio_path": audio_path,
            "map_path": map_path,
        },
    )
    monkeypatch.setattr(
        tapping,
        "load_beat_tap_map",
        lambda _map_path: {"event_map": {}},
    )
    monkeypatch.setattr(
        tapping,
        "_start_audio_process",
        lambda _audio_path, _start: (player, 2.0, True),
    )
    monkeypatch.setattr(
        tapping,
        "save_beat_tap_map",
        lambda _path, **kwargs: saved.append(dict(kwargs["event_map"])),
    )

    ticks = iter([100.0, 100.25])
    monkeypatch.setattr(tapping.time, "perf_counter", lambda: next(ticks))
    answers = iter(["", "q"])

    tapping.tap_beats(
        song_file,
        audio_override=None,
        autosave_every=1,
        resume_preroll_seconds=0.0,
        dry_run=False,
        read_input=lambda _prompt: next(answers),
    )

    assert saved
    assert 0 in saved[0]
    assert 1 in saved[0]
    assert player.terminated is True


def test_tap_beats_missing_audio_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_path(tmp_path)

    monkeypatch.setattr(tapping, "load_yaml_file", lambda _path: {})
    monkeypatch.setattr(
        tapping,
        "beats_tap_config",
        lambda _song_data, _song_path: {
            "fallback_audio_path": tmp_path / "missing.wav",
            "map_path": tmp_path / "beats-map.yaml",
        },
    )
    monkeypatch.setattr(
        tapping,
        "load_beat_tap_map",
        lambda _map_path: {"event_map": {}},
    )

    with pytest.raises(ControllerError, match="Playback audio not found"):
        tapping.tap_beats(
            song_file,
            audio_override=None,
            autosave_every=1,
            resume_preroll_seconds=0.0,
            dry_run=False,
        )


def test_tap_beats_wraps_tap_map_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_path(tmp_path)

    monkeypatch.setattr(tapping, "load_yaml_file", lambda _path: {})
    monkeypatch.setattr(
        tapping,
        "beats_tap_config",
        lambda _song_data, _song_path: {
            "fallback_audio_path": tmp_path / "audio.wav",
            "map_path": tmp_path / "beats-map.yaml",
        },
    )

    def _raise(_map_path: Path):
        raise SongConfigError("bad beat map")

    monkeypatch.setattr(tapping, "load_beat_tap_map", _raise)

    with pytest.raises(ControllerError, match="bad beat map"):
        tapping.tap_beats(
            song_file,
            audio_override=tmp_path / "audio.wav",
            autosave_every=1,
            resume_preroll_seconds=0.0,
            dry_run=True,
        )


def test_tap_beats_player_absent_and_unknown_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_path(tmp_path)
    audio_path = tmp_path / "beats.wav"
    audio_path.write_text("", encoding="utf-8")
    map_path = tmp_path / "beats-map.yaml"
    emitted: list[str] = []

    monkeypatch.setattr(tapping, "load_yaml_file", lambda _path: {})
    monkeypatch.setattr(
        tapping,
        "beats_tap_config",
        lambda _song_data, _song_path: {
            "fallback_audio_path": audio_path,
            "map_path": map_path,
        },
    )
    monkeypatch.setattr(
        tapping,
        "load_beat_tap_map",
        lambda _map_path: {"event_map": {0: 0.5}},
    )
    monkeypatch.setattr(
        tapping,
        "_start_audio_process",
        lambda _audio_path, _start: (None, 0.5, False),
    )
    monkeypatch.setattr(tapping, "save_beat_tap_map", lambda _path, **kwargs: None)
    monkeypatch.setattr(tapping.time, "perf_counter", lambda: 20.0)

    answers = iter(["", "x", "q"])
    tapping.tap_beats(
        song_file,
        audio_override=None,
        autosave_every=2,
        resume_preroll_seconds=0.25,
        dry_run=False,
        emit=emitted.append,
        read_input=lambda _prompt: next(answers),
    )

    assert any("No audio player found" in line for line in emitted)
    assert any("Unknown input. Use Enter to stamp" in line for line in emitted)


def test_tap_beats_warns_when_player_cannot_seek(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_path(tmp_path)
    audio_path = tmp_path / "beats.wav"
    audio_path.write_text("", encoding="utf-8")
    map_path = tmp_path / "beats-map.yaml"
    emitted: list[str] = []

    monkeypatch.setattr(tapping, "load_yaml_file", lambda _path: {})
    monkeypatch.setattr(
        tapping,
        "beats_tap_config",
        lambda _song_data, _song_path: {
            "fallback_audio_path": audio_path,
            "map_path": map_path,
        },
    )
    monkeypatch.setattr(
        tapping,
        "load_beat_tap_map",
        lambda _map_path: {"event_map": {0: 1.5}},
    )
    monkeypatch.setattr(
        tapping,
        "_start_audio_process",
        lambda _audio_path, _start: (_FakePlayer(), 0.0, False),
    )
    monkeypatch.setattr(tapping, "save_beat_tap_map", lambda _path, **kwargs: None)
    monkeypatch.setattr(tapping.time, "perf_counter", lambda: 30.0)

    tapping.tap_beats(
        song_file,
        audio_override=None,
        autosave_every=5,
        resume_preroll_seconds=0.25,
        dry_run=False,
        emit=emitted.append,
        read_input=lambda _prompt: "q",
    )

    assert any("Warning: current player cannot seek" in line for line in emitted)


def test_tap_beats_kills_player_after_wait_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_path(tmp_path)
    audio_path = tmp_path / "beats.wav"
    audio_path.write_text("", encoding="utf-8")
    map_path = tmp_path / "beats-map.yaml"
    player = _FakePlayer(wait_raises_timeout=True)

    monkeypatch.setattr(tapping, "load_yaml_file", lambda _path: {})
    monkeypatch.setattr(
        tapping,
        "beats_tap_config",
        lambda _song_data, _song_path: {
            "fallback_audio_path": audio_path,
            "map_path": map_path,
        },
    )
    monkeypatch.setattr(
        tapping,
        "load_beat_tap_map",
        lambda _map_path: {"event_map": {0: 0.0}},
    )
    monkeypatch.setattr(
        tapping,
        "_start_audio_process",
        lambda _audio_path, _start: (player, 0.0, True),
    )
    monkeypatch.setattr(tapping, "save_beat_tap_map", lambda _path, **kwargs: None)
    monkeypatch.setattr(tapping.time, "perf_counter", lambda: 50.0)

    tapping.tap_beats(
        song_file,
        audio_override=None,
        autosave_every=10,
        resume_preroll_seconds=0.0,
        dry_run=False,
        read_input=lambda _prompt: "q",
    )

    assert player.terminated is True
    assert player.killed is True


def test_tap_beats_wraps_song_config_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    song_file = _song_path(tmp_path)

    def _raise(_path: Path):
        raise SongConfigError("bad yaml")

    monkeypatch.setattr(tapping, "load_yaml_file", _raise)

    with pytest.raises(ControllerError, match="bad yaml"):
        tapping.tap_beats(
            song_file,
            audio_override=None,
            autosave_every=1,
            resume_preroll_seconds=0.0,
            dry_run=True,
        )
