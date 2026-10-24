from __future__ import annotations

from fractions import Fraction
import re
import textwrap
from typing import Any

import yaml

_SECTION_RE = re.compile(r"^\[(.+)]$")
_NOTE_TOKEN_RE = re.compile(r"\b([A-Ga-g])([#b♯♭]?)(-?\d+)?\b")
_DURATION_FRACTION_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")
_DURATION_DECIMAL_RE = re.compile(r"^\s*-?\d+(?:\.\d+)?\s*$")

_NATURAL_PITCH_CLASS = {
    "C": 0,
    "D": 2,
    "E": 4,
    "F": 5,
    "G": 7,
    "A": 9,
    "B": 11,
}

_LETTER_INDEX = {
    "C": 0,
    "D": 1,
    "E": 2,
    "F": 3,
    "G": 4,
    "A": 5,
    "B": 6,
}


class _LiteralString(str):
    """YAML scalar that should always use literal block style."""


class _QuotedString(str):
    """YAML scalar that should always use double-quoted style."""


class _ConvertYamlDumper(yaml.SafeDumper):
    """Dumper with stable scalar styles for convert output."""


def _represent_literal_string(
    dumper: yaml.Dumper, data: _LiteralString
) -> yaml.ScalarNode:
    return dumper.represent_scalar(
        "tag:yaml.org,2002:str", str(data), style="|"
    )


def _represent_quoted_string(
    dumper: yaml.Dumper, data: _QuotedString
) -> yaml.ScalarNode:
    return dumper.represent_scalar(
        "tag:yaml.org,2002:str", str(data), style='"'
    )


_ConvertYamlDumper.add_representer(_LiteralString, _represent_literal_string)
_ConvertYamlDumper.add_representer(_QuotedString, _represent_quoted_string)


def _is_section_header(line: str) -> bool:
    return _SECTION_RE.fullmatch(line.strip()) is not None


def _section_name(line: str) -> str:
    match = _SECTION_RE.fullmatch(line.strip())
    if match is None:
        return ""
    return match.group(1).strip()


def _wrap_lyric_line(line: str, max_line_chars: int) -> str:
    wrapped = textwrap.wrap(
        line,
        width=max_line_chars,
        break_long_words=True,
        break_on_hyphens=False,
        replace_whitespace=False,
        drop_whitespace=True,
    )
    if not wrapped:
        return ""
    return "\n".join(wrapped)


def convert_lyrics_text_to_events(
    text: str, *, max_line_chars: int = 32
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    pending_marker: str | None = None
    lines = text.splitlines()

    for idx, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not stripped:
            continue

        if _is_section_header(stripped):
            section = _section_name(stripped)
            next_line = (
                lines[idx + 1].strip() if (idx + 1) < len(lines) else ""
            )

            if next_line and not _is_section_header(next_line):
                pending_marker = section
            else:
                events.append({"instruction": _QuotedString(section)})
                pending_marker = None
            continue

        lyric_text = _wrap_lyric_line(stripped, max_line_chars=max_line_chars)
        event: dict[str, Any] = {"lyric": _LiteralString(lyric_text)}
        if pending_marker is not None:
            event["marker"] = _QuotedString(pending_marker)
            pending_marker = None
        events.append(event)

    return events


def dump_events_yaml(events: list[dict[str, Any]]) -> str:
    return yaml.dump(
        events,
        Dumper=_ConvertYamlDumper,
        sort_keys=False,
        allow_unicode=True,
    )


def _parse_note_token(raw: str) -> tuple[str, str, int | None]:
    token = raw.strip()
    match = _NOTE_TOKEN_RE.fullmatch(token)
    if match is None:
        raise ValueError(f"Unable to parse note token '{raw}'")

    letter = match.group(1).upper()
    accidental = match.group(2) or ""
    accidental = accidental.replace("♯", "#").replace("♭", "b")
    octave_raw = match.group(3)
    octave = int(octave_raw) if octave_raw is not None else None
    return letter, accidental, octave


def _note_pitch_class(letter: str, accidental: str) -> int:
    value = _NATURAL_PITCH_CLASS[letter]
    if accidental == "#":
        value += 1
    elif accidental == "b":
        value -= 1
    return value % 12


def _format_duration_beats(value: float) -> str:
    if value <= 0:
        raise ValueError("Note duration must be positive")

    rounded = round(value)
    if abs(value - rounded) < 1e-6:
        return str(int(rounded))

    frac = Fraction(value).limit_denominator(24)
    if abs(float(frac) - value) < 1e-4:
        value = float(frac)

    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text


def _parse_duration_token(raw: str) -> float | None:
    text = raw.strip()
    if not text:
        return None

    frac_match = _DURATION_FRACTION_RE.fullmatch(text)
    if frac_match is not None:
        numerator = int(frac_match.group(1))
        denominator = int(frac_match.group(2))
        if denominator == 0:
            raise ValueError("Duration fraction denominator cannot be zero")
        value = numerator / denominator
        if value <= 0:
            raise ValueError("Duration must be positive")
        return value

    if _DURATION_DECIMAL_RE.fullmatch(text) is not None:
        value = float(text)
        if value <= 0:
            raise ValueError("Duration must be positive")
        return value

    return None


def _parse_duration_quartet(
    raw: str,
    *,
    beats_per_bar: int,
    divisions_per_beat: int,
    ticks_per_beat: int,
) -> float | None:
    numbers = re.findall(r"-?\d+", raw)
    if len(numbers) != 4:
        return None

    bars, beats, divisions, ticks = (int(part) for part in numbers)
    value = (bars * beats_per_bar) + beats
    value += divisions / max(1, divisions_per_beat)
    value += ticks / max(1, ticks_per_beat)
    if value <= 0:
        return None
    return value


def _extract_rows_from_tabular_text(text: str) -> list[tuple[str, str, int]]:
    rows: list[tuple[str, str, int]] = []
    lines = text.splitlines()
    header_index = -1
    note_index = -1
    duration_index = -1

    for idx, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line or "\t" not in raw_line:
            continue

        columns = [part.strip().lower() for part in raw_line.split("\t")]
        if "note" in columns:
            note_index = columns.index("note")
        else:
            for col_idx, col in enumerate(columns):
                if "note" in col or "pitch" in col:
                    note_index = col_idx
                    break

        for col_idx, col in enumerate(columns):
            if (
                col in {"length", "duration", "dur"}
                or "length" in col
                or "dur" in col
            ):
                duration_index = col_idx
                break

        if note_index >= 0 and duration_index >= 0:
            header_index = idx
            break

    if header_index < 0:
        return rows

    for row_idx in range(header_index + 1, len(lines)):
        raw_line = lines[row_idx]
        if not raw_line.strip():
            continue
        parts = [part.strip() for part in raw_line.split("\t")]
        if max(note_index, duration_index) >= len(parts):
            continue
        note_token = parts[note_index]
        duration_raw = parts[duration_index]
        if note_token:
            rows.append((note_token, duration_raw, row_idx + 1))

    return rows


def _extract_rows_from_dense_lines(text: str) -> list[tuple[str, str, int]]:
    rows: list[tuple[str, str, int]] = []
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if "rel vel" in line.lower():
            continue

        note_match = _NOTE_TOKEN_RE.search(raw_line)
        if note_match is None:
            continue

        note_token = note_match.group(0)

        before = raw_line[: note_match.start()]
        tail = raw_line[note_match.end():]

        numbers_after = re.findall(r"-?\d+", tail)
        numbers_before = re.findall(r"-?\d+", before)

        duration_raw = ""
        if len(numbers_after) >= 4:
            duration_raw = " ".join(numbers_after[-4:])
        elif len(numbers_before) >= 4:
            duration_raw = " ".join(numbers_before[-4:])
        else:
            duration_match = re.search(r"\d+\s*/\s*\d+|-?\d+(?:\.\d+)?", tail)
            duration_raw = (
                duration_match.group(0) if duration_match is not None else ""
            )

        rows.append((note_token, duration_raw, line_no))

    return rows


def _parse_logic_rows(
    text: str,
    *,
    beats_per_bar: int,
    divisions_per_beat: int,
    ticks_per_beat: int,
) -> list[tuple[str, str, int | None, float]]:
    rows = _extract_rows_from_tabular_text(text)
    if not rows:
        rows = _extract_rows_from_dense_lines(text)

    parsed: list[tuple[str, str, int | None, float]] = []
    for note_token, duration_raw, line_no in rows:
        letter, accidental, octave = _parse_note_token(note_token)

        duration = _parse_duration_quartet(
            duration_raw,
            beats_per_bar=beats_per_bar,
            divisions_per_beat=divisions_per_beat,
            ticks_per_beat=ticks_per_beat,
        )
        if duration is None:
            duration = _parse_duration_token(duration_raw)

        if duration is None:
            raise ValueError(
                f"Unable to parse note duration on line {line_no}: '{duration_raw}'"
            )

        parsed.append((letter, accidental, octave, duration))

    if not parsed:
        raise ValueError("No note rows were detected in the provided text")

    return parsed


def _parse_root_note(root_note: str) -> tuple[str, str]:
    token = root_note.strip()
    if not token:
        raise ValueError("Root note cannot be empty")
    letter, accidental, _octave = _parse_note_token(token)
    return letter, accidental


def _degree_pitch_for_note(
    *,
    letter: str,
    accidental: str,
    root_letter: str,
) -> float:
    root_idx = _LETTER_INDEX[root_letter]
    note_idx = _LETTER_INDEX[letter]
    degree = ((note_idx - root_idx) % 7) + 1

    natural_pc = _note_pitch_class(letter, "")
    actual_pc = _note_pitch_class(letter, accidental)
    semitone_delta = (actual_pc - natural_pc) % 12
    if semitone_delta > 6:
        semitone_delta -= 12

    if semitone_delta not in (-1, 0, 1):
        raise ValueError(
            f"Accidental on note {letter}{accidental} is outside supported range"
        )

    return degree + (0.5 * semitone_delta)


def _format_degree_pitch(value: float) -> str:
    rounded = round(value)
    if abs(value - rounded) < 1e-6:
        return str(int(rounded))
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _is_close(value: float, target: float, tolerance: float = 1e-3) -> bool:
    return abs(value - target) <= tolerance


def _build_legacy_riff(degree_notes: list[tuple[str, float]]) -> str:
    chunks: list[str] = []
    total = len(degree_notes)

    for idx, (pitch, duration) in enumerate(degree_notes):
        is_last = idx == (total - 1)
        if is_last:
            chunks.append(f"{pitch}:{_format_duration_beats(duration)}")
            continue

        if _is_close(duration, 1.0):
            chunks.append(f"{pitch} ")
        elif _is_close(duration, 0.5):
            chunks.append(f"{pitch}-")
        elif _is_close(duration, 0.25):
            chunks.append(pitch)
        else:
            chunks.append(f"{pitch}:{_format_duration_beats(duration)} ")

    return "".join(chunks).strip()


def convert_logic_text_to_riff(
    text: str,
    *,
    notation: str = "named",
    root_note: str = "C",
    beats_per_bar: int = 4,
    divisions_per_beat: int = 4,
    ticks_per_beat: int = 960,
    keep_octave: bool = False,
) -> str:
    parsed = _parse_logic_rows(
        text,
        beats_per_bar=beats_per_bar,
        divisions_per_beat=divisions_per_beat,
        ticks_per_beat=ticks_per_beat,
    )

    if notation == "named":
        tokens: list[str] = []
        for letter, accidental, octave, duration in parsed:
            pitch = f"{letter}{accidental}"
            if keep_octave and octave is not None:
                pitch = f"{pitch}{octave}"
            tokens.append(f"{pitch}{_format_duration_beats(duration)}")
        return " ".join(tokens)

    root_letter, _root_accidental = _parse_root_note(root_note)
    degree_notes: list[tuple[str, float]] = []
    for letter, accidental, _octave, duration in parsed:
        pitch_value = _degree_pitch_for_note(
            letter=letter,
            accidental=accidental,
            root_letter=root_letter,
        )
        degree_notes.append((_format_degree_pitch(pitch_value), duration))

    if notation == "degree":
        return " ".join(
            f"{pitch}:{_format_duration_beats(duration)}"
            for pitch, duration in degree_notes
        )

    if notation == "legacy":
        return _build_legacy_riff(degree_notes)

    raise ValueError(f"Unsupported notation '{notation}'")
