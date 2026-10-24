from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class SongConfigError(ValueError):
    """Raised when song YAML does not satisfy required tap config rules."""


def _resolve_named_progressions(song_data: dict[str, Any]) -> None:
    events = song_data.get("events")
    if not isinstance(events, list):
        return

    root_riffs = song_data.get("riffs")
    root_chords = song_data.get("chords")

    for idx, event in enumerate(events):
        if not isinstance(event, dict):
            continue

        for key, root_name, root_values in (
            ("riff", "riffs", root_riffs),
            ("chords", "chords", root_chords),
        ):
            raw = event.get(key)
            if not isinstance(raw, str) or not raw.startswith("@"):
                continue

            label = raw[1:].strip()
            if not label:
                raise SongConfigError(
                    f"events[{idx}].{key} placeholder '{raw}' must include a label"
                )

            if not isinstance(root_values, dict):
                raise SongConfigError(
                    f"events[{idx}].{key} references @{label}, but root '{root_name}' mapping is missing"
                )

            if label not in root_values:
                raise SongConfigError(
                    f"events[{idx}].{key} references @{label}, but '{root_name}.{label}' was not found"
                )

            resolved = root_values[label]
            if not isinstance(resolved, str):
                raise SongConfigError(f"{root_name}.{label} must be a string")

            event[key] = resolved


def load_yaml_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    if not isinstance(data, dict):
        raise SongConfigError(f"Expected a mapping at root of {path}")

    _resolve_named_progressions(data)

    return data


def _validate_events(song_data: dict[str, Any]) -> list[dict[str, Any]]:
    events = song_data.get("events")
    if not isinstance(events, list):
        raise SongConfigError("Song file must contain an events list")

    for idx, event in enumerate(events):
        if not isinstance(event, dict):
            raise SongConfigError(f"events[{idx}] must be a mapping")
        has_lyric = (
            isinstance(event.get("lyric"), str)
            and event.get("lyric", "") != ""
        )
        has_instruction = (
            isinstance(event.get("instruction"), str)
            and event.get("instruction", "") != ""
        )
        if has_lyric == has_instruction:
            raise SongConfigError(
                f"events[{idx}] must have exactly one of 'lyric' or 'instruction'"
            )

    return events


def _resolve_fallback_audio(
    song_data: dict[str, Any], song_path: Path
) -> tuple[str | None, Path | None]:
    fallback_audio = song_data.get("audio", {}).get("file")
    if fallback_audio is not None and not isinstance(fallback_audio, str):
        raise SongConfigError("audio.file must be a string when provided")

    fallback_audio_path = None
    if isinstance(fallback_audio, str) and fallback_audio.strip():
        fallback_audio_path = (song_path.parent / fallback_audio).resolve()

    return fallback_audio, fallback_audio_path


def lyrics_tap_config(
    song_data: dict[str, Any], song_path: Path
) -> dict[str, Any]:
    timing = song_data.get("timing")
    if not isinstance(timing, dict):
        raise SongConfigError("Missing required timing mapping")

    lyrics = timing.get("lyrics")
    if not isinstance(lyrics, dict):
        raise SongConfigError("Missing required timing.lyrics mapping")

    source = lyrics.get("source")
    if source != "tap":
        raise SongConfigError(
            "timing.lyrics.source must be 'tap' for tap lyrics command"
        )

    map_file = lyrics.get("map_file")
    if not isinstance(map_file, str) or not map_file.strip():
        raise SongConfigError("Missing required timing.lyrics.map_file string")

    base_offset_seconds = lyrics.get("base_offset_seconds", 0.0)
    try:
        base_offset_seconds = float(base_offset_seconds)
    except (TypeError, ValueError) as exc:
        raise SongConfigError(
            "timing.lyrics.base_offset_seconds must be numeric"
        ) from exc

    map_path = (song_path.parent / map_file).resolve()
    fallback_audio, fallback_audio_path = _resolve_fallback_audio(
        song_data, song_path
    )
    events = _validate_events(song_data)

    return {
        "map_path": map_path,
        "base_offset_seconds": base_offset_seconds,
        "fallback_audio": fallback_audio,
        "fallback_audio_path": fallback_audio_path,
        "events": events,
    }


def beats_tap_config(
    song_data: dict[str, Any], song_path: Path
) -> dict[str, Any]:
    timing = song_data.get("timing")
    if not isinstance(timing, dict):
        raise SongConfigError("Missing required timing mapping")

    beats = timing.get("beats")
    if not isinstance(beats, dict):
        raise SongConfigError("Missing required timing.beats mapping")

    source = beats.get("source")
    if source != "tap":
        raise SongConfigError(
            "timing.beats.source must be 'tap' for tap beats command"
        )

    map_file = beats.get("map_file")
    if not isinstance(map_file, str) or not map_file.strip():
        raise SongConfigError("Missing required timing.beats.map_file string")

    map_path = (song_path.parent / map_file).resolve()
    fallback_audio, fallback_audio_path = _resolve_fallback_audio(
        song_data, song_path
    )

    return {
        "map_path": map_path,
        "fallback_audio": fallback_audio,
        "fallback_audio_path": fallback_audio_path,
    }
