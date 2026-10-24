from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import yaml

from praecentor.io.song import SongConfigError


def _to_event_map(events_raw: list[Any], event_count: int) -> dict[int, float]:
    event_map: dict[int, float] = {}
    for idx, row in enumerate(events_raw):
        if not isinstance(row, dict):
            raise SongConfigError(f"Tap map events[{idx}] must be a mapping")

        index = row.get("index")
        if not isinstance(index, int):
            raise SongConfigError(
                f"Tap map events[{idx}].index must be an integer"
            )
        if index < 0 or index >= event_count:
            raise SongConfigError(
                f"Tap map events[{idx}].index={index} out of range for {event_count} song events"
            )

        at_seconds_raw = row.get("at_seconds")
        try:
            at_seconds = float(at_seconds_raw)
        except (TypeError, ValueError) as exc:
            raise SongConfigError(
                f"Tap map events[{idx}].at_seconds must be numeric"
            ) from exc

        if at_seconds < 0:
            raise SongConfigError(
                f"Tap map events[{idx}].at_seconds cannot be negative"
            )

        if index in event_map:
            raise SongConfigError(f"Tap map has duplicate index {index}")

        event_map[index] = at_seconds

    return event_map


def _to_unbounded_event_map(events_raw: list[Any]) -> dict[int, float]:
    event_map: dict[int, float] = {}
    for idx, row in enumerate(events_raw):
        if not isinstance(row, dict):
            raise SongConfigError(f"Tap map events[{idx}] must be a mapping")

        index = row.get("index")
        if not isinstance(index, int):
            raise SongConfigError(
                f"Tap map events[{idx}].index must be an integer"
            )
        if index < 0:
            raise SongConfigError(f"Tap map events[{idx}].index must be >= 0")

        at_seconds_raw = row.get("at_seconds")
        try:
            at_seconds = float(at_seconds_raw)
        except (TypeError, ValueError) as exc:
            raise SongConfigError(
                f"Tap map events[{idx}].at_seconds must be numeric"
            ) from exc

        if at_seconds < 0:
            raise SongConfigError(
                f"Tap map events[{idx}].at_seconds cannot be negative"
            )

        if index in event_map:
            raise SongConfigError(f"Tap map has duplicate index {index}")

        event_map[index] = at_seconds

    return event_map


def load_tap_map(path: Path, event_count: int) -> dict[str, Any]:
    if not path.exists():
        return {
            "version": 1,
            "events": [],
            "event_map": {},
        }

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    if not isinstance(data, dict):
        raise SongConfigError("Tap map root must be a mapping")

    events_raw = data.get("events", [])
    if not isinstance(events_raw, list):
        raise SongConfigError("Tap map events must be a list")

    event_map = _to_event_map(events_raw, event_count)

    # Validate non-decreasing time order by event index.
    times_by_index = [event_map[i] for i in sorted(event_map)]
    if any(b < a for a, b in zip(times_by_index, times_by_index[1:])):
        raise SongConfigError(
            "Tap map at_seconds values must be non-decreasing by event index"
        )

    data["event_map"] = event_map
    return data


def load_beat_tap_map(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "version": 1,
            "events": [],
            "event_map": {},
        }

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    if not isinstance(data, dict):
        raise SongConfigError("Tap map root must be a mapping")

    events_raw = data.get("events", [])
    if not isinstance(events_raw, list):
        raise SongConfigError("Tap map events must be a list")

    event_map = _to_unbounded_event_map(events_raw)
    times_by_index = [event_map[i] for i in sorted(event_map)]
    if any(b < a for a, b in zip(times_by_index, times_by_index[1:])):
        raise SongConfigError(
            "Tap map at_seconds values must be non-decreasing by event index"
        )

    data["event_map"] = event_map
    return data


def save_tap_map(
    path: Path,
    *,
    song_file: str,
    reference_audio_used: str,
    event_map: dict[int, float],
) -> None:
    events = [
        {"index": idx, "at_seconds": round(event_map[idx], 6)}
        for idx in sorted(event_map)
    ]

    payload = {
        "version": 1,
        "song_file": song_file,
        "captured_at_utc": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "reference_audio_used": reference_audio_used,
        "events": events,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=path.parent, suffix=".tmp"
    ) as tmp:
        yaml.safe_dump(payload, tmp, sort_keys=False, allow_unicode=True)
        tmp_path = Path(tmp.name)

    tmp_path.replace(path)


def save_beat_tap_map(
    path: Path,
    *,
    song_file: str,
    reference_audio_used: str,
    event_map: dict[int, float],
) -> None:
    save_tap_map(
        path,
        song_file=song_file,
        reference_audio_used=reference_audio_used,
        event_map=event_map,
    )
