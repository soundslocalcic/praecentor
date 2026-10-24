from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from praecentor.controllers.errors import ControllerError
from praecentor.io.song import (
    SongConfigError,
    beats_tap_config,
    load_yaml_file,
    lyrics_tap_config,
)
from praecentor.io.tap_map import (
    load_beat_tap_map,
    load_tap_map,
    save_beat_tap_map,
    save_tap_map,
)


def _effective_chords(events: list[dict[str, Any]], event_index: int) -> str:
    current = ""
    for idx in range(0, event_index + 1):
        chords = events[idx].get("chords")
        if chords is None:
            continue
        if not isinstance(chords, str):
            continue
        if chords == "":
            current = ""
        else:
            current = chords
    return current


def _event_text(event: dict[str, Any]) -> str:
    lyric = event.get("lyric")
    if isinstance(lyric, str) and lyric:
        return lyric
    instruction = event.get("instruction")
    if isinstance(instruction, str) and instruction:
        return f"({instruction})"
    return ""


def _start_audio_process(
    audio_path: Path, start_seconds: float
) -> tuple[subprocess.Popen[str] | None, float, bool]:
    ffplay = shutil.which("ffplay")
    if ffplay:
        cmd = [
            ffplay,
            "-nodisp",
            "-autoexit",
            "-loglevel",
            "quiet",
            "-ss",
            f"{start_seconds:.6f}",
            str(audio_path),
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return proc, start_seconds, True

    afplay = shutil.which("afplay")
    if afplay:
        cmd = [afplay, str(audio_path)]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return proc, 0.0, False

    return None, start_seconds, False


def _resolve_audio(
    *,
    audio_override: Path | None,
    fallback_audio_path: Path | None,
) -> tuple[Path, str]:
    if audio_override:
        resolved_audio_path = audio_override.resolve()
    else:
        if fallback_audio_path is None:
            raise ControllerError(
                "No playback audio available. Provide --audio or set audio.file in the song YAML."
            )
        resolved_audio_path = fallback_audio_path
    return resolved_audio_path, str(resolved_audio_path)


def tap_lyrics(
    song_file: Path,
    *,
    audio_override: Path | None,
    autosave_every: int,
    resume_preroll_seconds: float,
    dry_run: bool,
    emit: Callable[[str], None] | None = None,
    read_input: Callable[[str], str] | None = None,
) -> None:
    emit = emit or (lambda _line: None)
    read_input = read_input or input

    try:
        song_data = load_yaml_file(song_file)
        tap_cfg = lyrics_tap_config(song_data, song_file)
    except SongConfigError as exc:
        raise ControllerError(str(exc)) from exc

    events: list[dict[str, Any]] = tap_cfg["events"]
    resolved_audio_path, resolved_audio = _resolve_audio(
        audio_override=audio_override,
        fallback_audio_path=tap_cfg["fallback_audio_path"],
    )

    if not dry_run and not resolved_audio_path.exists():
        raise ControllerError(
            f"Playback audio not found: {resolved_audio_path}"
        )

    map_path: Path = tap_cfg["map_path"]
    try:
        tap_map = load_tap_map(map_path, event_count=len(events))
    except SongConfigError as exc:
        raise ControllerError(str(exc)) from exc

    event_map: dict[int, float] = tap_map["event_map"]
    first_missing_index = next(
        (i for i in range(len(events)) if i not in event_map), len(events)
    )

    resume_seconds = 0.0
    if first_missing_index > 0:
        previous_index = first_missing_index - 1
        resume_seconds = float(event_map.get(previous_index, 0.0))

    playback_start_seconds = max(0.0, resume_seconds - resume_preroll_seconds)

    if dry_run:
        emit(f"song_file={song_file}")
        emit(f"tap_map={map_path}")
        emit(f"audio={resolved_audio}")
        emit(f"base_offset_seconds={tap_cfg['base_offset_seconds']}")
        emit(f"autosave_every={autosave_every}")
        emit(f"event_count={len(events)}")
        emit(f"resume_index={first_missing_index}")
        emit(f"resume_seconds={resume_seconds:.6f}")
        emit(f"playback_start_seconds={playback_start_seconds:.6f}")
        return

    if first_missing_index >= len(events):
        emit(
            "All events are already stamped in the tap map. Nothing to capture."
        )
        return

    current_index = 0 if first_missing_index == 0 else first_missing_index - 1

    emit(
        f"Starting tap session near event {first_missing_index + 1}/{len(events)}"
    )
    emit(f"Audio: {resolved_audio}")
    emit(f"Playback start: {playback_start_seconds:.3f}s")
    emit("Controls: Enter = stamp NEXT cue and advance, q = quit and save")

    player, actual_start_seconds, used_seek = _start_audio_process(
        resolved_audio_path, playback_start_seconds
    )

    if player is None:
        emit(
            "No audio player found (ffplay/afplay). Start audio manually at the shown playback"
            " start time, then press Enter."
        )
        read_input("Press Enter to start capture... ")
    elif not used_seek and playback_start_seconds > 0:
        emit(
            "Warning: current player cannot seek to resume time automatically; playback started at"
            " 0.000s."
        )

    start_clock = time.perf_counter()
    captured_since_save = 0

    if first_missing_index == 0 and 0 not in event_map:
        event_map[0] = actual_start_seconds
        captured_since_save += 1
        emit(
            f"Auto-stamped cue 1/{len(events)} at t={actual_start_seconds:.3f}s"
        )

    try:
        while current_index < len(events):
            event = events[current_index]
            display_text = _event_text(event)
            effective_chords = _effective_chords(events, current_index)

            emit("")
            emit(f"[{current_index + 1}/{len(events)}]")
            emit(display_text)
            emit(
                f"Chords: {effective_chords if effective_chords else '(none)'}"
            )

            next_index = current_index + 1
            if next_index < len(events):
                next_text = _event_text(events[next_index]).splitlines()[0]
                emit(f"Next: {next_text}")
            else:
                emit("Next: (end)")

            command = read_input("Enter=next, q=quit > ").strip().lower()
            if command == "q":
                break
            if command != "":
                emit("Unknown input. Use Enter to advance or q to quit.")
                continue

            if next_index >= len(events):
                emit("Reached final cue.")
                break

            stamp = actual_start_seconds + (time.perf_counter() - start_clock)
            event_map[next_index] = stamp
            captured_since_save += 1
            emit(
                f"Stamped cue {next_index + 1}/{len(events)} at t={stamp:.3f}s"
            )
            current_index = next_index

            if captured_since_save >= autosave_every:
                save_tap_map(
                    map_path,
                    song_file=song_file.name,
                    reference_audio_used=resolved_audio,
                    event_map=event_map,
                )
                captured_since_save = 0
                emit(f"Autosaved: {map_path}")
    finally:
        if captured_since_save > 0 or not map_path.exists():
            save_tap_map(
                map_path,
                song_file=song_file.name,
                reference_audio_used=resolved_audio,
                event_map=event_map,
            )
            emit(f"Saved: {map_path}")

        if player is not None and player.poll() is None:
            player.terminate()
            try:
                player.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                player.kill()

    stamped_count = len(event_map)
    emit(f"Tap session ended. {stamped_count}/{len(events)} events stamped.")


def tap_beats(
    song_file: Path,
    *,
    audio_override: Path | None,
    autosave_every: int,
    resume_preroll_seconds: float,
    dry_run: bool,
    emit: Callable[[str], None] | None = None,
    read_input: Callable[[str], str] | None = None,
) -> None:
    emit = emit or (lambda _line: None)
    read_input = read_input or input

    try:
        song_data = load_yaml_file(song_file)
        beats_cfg = beats_tap_config(song_data, song_file)
    except SongConfigError as exc:
        raise ControllerError(str(exc)) from exc

    resolved_audio_path, resolved_audio = _resolve_audio(
        audio_override=audio_override,
        fallback_audio_path=beats_cfg["fallback_audio_path"],
    )

    if not dry_run and not resolved_audio_path.exists():
        raise ControllerError(
            f"Playback audio not found: {resolved_audio_path}"
        )

    map_path: Path = beats_cfg["map_path"]
    try:
        tap_map = load_beat_tap_map(map_path)
    except SongConfigError as exc:
        raise ControllerError(str(exc)) from exc

    event_map: dict[int, float] = tap_map["event_map"]
    existing_indices = sorted(event_map)
    first_missing_index = 0

    while first_missing_index in event_map:
        first_missing_index += 1

    resume_seconds = 0.0
    if first_missing_index > 0:
        resume_seconds = float(event_map.get(first_missing_index - 1, 0.0))

    playback_start_seconds = max(0.0, resume_seconds - resume_preroll_seconds)

    if dry_run:
        emit(f"song_file={song_file}")
        emit(f"beat_map={map_path}")
        emit(f"audio={resolved_audio}")
        emit(f"autosave_every={autosave_every}")
        emit(f"captured_beat_count={len(existing_indices)}")
        emit(f"resume_index={first_missing_index}")
        emit(f"resume_seconds={resume_seconds:.6f}")
        emit(f"playback_start_seconds={playback_start_seconds:.6f}")
        return

    emit(f"Starting beat tap session at beat index {first_missing_index}")
    emit(f"Audio: {resolved_audio}")
    emit(f"Playback start: {playback_start_seconds:.3f}s")
    emit("Controls: Enter = stamp NEXT beat, q = quit and save")

    player, actual_start_seconds, used_seek = _start_audio_process(
        resolved_audio_path, playback_start_seconds
    )

    if player is None:
        emit(
            "No audio player found (ffplay/afplay). Start audio manually at the shown playback"
            " start time, then press Enter."
        )
        read_input("Press Enter to start capture... ")
    elif not used_seek and playback_start_seconds > 0:
        emit(
            "Warning: current player cannot seek to resume time automatically; playback started at"
            " 0.000s."
        )

    start_clock = time.perf_counter()
    captured_since_save = 0

    if first_missing_index == 0 and 0 not in event_map:
        event_map[0] = actual_start_seconds
        first_missing_index = 1
        captured_since_save += 1
        emit(f"Auto-stamped beat 0 at t={actual_start_seconds:.3f}s")

    next_index = first_missing_index
    try:
        while True:
            command = (
                read_input(f"Beat {next_index}: Enter=stamp, q=quit > ")
                .strip()
                .lower()
            )
            if command == "q":
                break
            if command != "":
                emit("Unknown input. Use Enter to stamp or q to quit.")
                continue

            stamp = actual_start_seconds + (time.perf_counter() - start_clock)
            event_map[next_index] = stamp
            captured_since_save += 1
            emit(f"Stamped beat {next_index} at t={stamp:.3f}s")
            next_index += 1

            if captured_since_save >= autosave_every:
                save_beat_tap_map(
                    map_path,
                    song_file=song_file.name,
                    reference_audio_used=resolved_audio,
                    event_map=event_map,
                )
                captured_since_save = 0
                emit(f"Autosaved: {map_path}")
    finally:
        if captured_since_save > 0 or not map_path.exists():
            save_beat_tap_map(
                map_path,
                song_file=song_file.name,
                reference_audio_used=resolved_audio,
                event_map=event_map,
            )
            emit(f"Saved: {map_path}")

        if player is not None and player.poll() is None:
            player.terminate()
            try:
                player.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                player.kill()

    emit(f"Beat tap session ended. {len(event_map)} beats stamped.")
