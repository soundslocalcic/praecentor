# Praecentor

Praecentor is a Python tool for building rehearsal and performance videos from song YAML definitions. It's designed around the way I work at the piano: a backing track, a visible lead sheet, chord or riff prompts, a countdown and beat grid, and lyrics that scroll with the song.

It's especially useful for live rehearsal and group work, where the video needs to show:

- the backing track and timing reference
- the current and upcoming chord or riff
- the lyric lines as they arrive
- the beat and bar structure for piano timing
- different output mixes for practice, performance, and session use

---

## What Praecentor does

Praecentor turns a structured YAML description of a song into a rendered video with:

- scrolling lyrics
- current chord or riff display
- next-chord lookahead
- beat/bar visual indicator (the “beat pie”)
- a progress bar or intro countdown during long breaks
- optional title card and countdown sequence before the track starts
- rendered output variants for different needs

Typical outputs include:

- Practice video: base track + guide vocals
- Performance video: backing and piano guide, without guide vocals
- Session video: only the backing mix, so the pianist can play over it live

---

## Project workflow

Praecentor expects a song to be defined in YAML and then rendered into a video. In practice the workflow is:

1. Author the song in a YAML file, including events, timing, and output layout.
2. Optionally add a timing map file for lyric/chord alignment.
3. Use the tap workflow to capture lyric and beat timing if needed.
4. Validate the song definition.
5. Render the final video(s).

The project supports both automatic timing and tap-based timing workflows, and it can use layout YAML overrides to control rendering details.

---

## Installation

Requirements:

- Python 3.8+
- ffmpeg installed and available on your PATH for video rendering
- Inter font files downloaded locally for rendering

Praecentor does not bundle the Inter font files in the repository. Before running a render, download Inter and place the `.ttf` files in a local fonts directory that is accessible to your machine, for example:

```bash
~/Library/Fonts/Inter/
```

or any directory you choose, then point Praecentor at it with either:

```bash
export PRAECENTOR_FONTS_DIR="$HOME/Library/Fonts/Inter"
```

or in the render/layout config:

```yaml
render:
  font_dir: "/Users/you/Library/Fonts/Inter"
```

Install from the repository:

```bash
cd /path/to/praecentor
python -m pip install -r requirements.txt
python -m pip install -e .
```

After installation, the CLI is available as:

```bash
praecentor --help
```

---

## CLI overview

Praecentor exposes the following commands:

```bash
praecentor validate SONG.yaml
praecentor tap lyrics SONG.yaml
praecentor tap beats SONG.yaml
praecentor timeline SONG.yaml
praecentor diagnose beats SONG.yaml
praecentor render SONG.yaml
praecentor convert lyrics
praecentor convert riff
```

The `render` command is also the default action when the CLI is invoked without a subcommand.

### Main commands

- `validate`: checks whether a song file is structurally valid
- `tap lyrics`: interactive lyric timing capture using the Enter key
- `tap beats`: interactive beat capture for timing and tempo analysis
- `timeline`: prints or inspects the cue timeline
- `diagnose beats`: helps inspect beat detection and timing issues
- `render`: generates MP4 output from the song definition
- `convert lyrics`: converts plain text into YAML events for lyric structure
- `convert riff`: converts note notation into riff strings

---

## Song YAML structure

A Praecentor song file is a single YAML document with a root structure similar to this:

```yaml
version: 1

meta:
  title: "Feed My Frankenstein"
  artist: "Alice Cooper"
  key: "D minor"

audio:
  stems:
    - group: "backing"
      label: "Music"
      volume: .9
      tracks:
        - name: "base"
          file: "Bounces/Backing.mp3"
          label: "Backing"
        - name: "guitar"
          file: "Bounces/Guitar.mp3"
    - group: "vocals"
      modal: true
      tracks:
        - name: "a"
          file: "Bounces/A Part.mp3"
          label: "A part"

video:
  layout:
    file: "/path/to/layout.yaml"
  output:
    - file: "Video/Performance.mp4"
      tracks:
        - backing.base

maestro:
  output:
    file: "Maestro.zip"

hot_start: true

timing:
  lyrics:
    source: tap
    map_file: "Bounces/Lyrics.yaml"
    base_offset_seconds: -.6
    max_line_chars: 36
    lead_in_beats: 2
  beats:
    source: auto
    beat_offset: -4
    quantize_factor: .25
    auto_dynamic_tempo: true
    auto_dynamic_tempo_sensitivity: .1
    click_track:
      file: "Bounces/Click.mp3"
  countdown_beats: 8

riffs:
  chorus1: "D1 F1 G0.5 F0.5 D1 D0.5 D0.5 F0.5 A0.5 G1 A1"

chords:
  verse: "Dm"
  prechorus: "Am"

events:
  - marker: "Verse 1"
    lyric: |
      Well, I ain't evil, I'm just
      good lookin'
    chords: "@verse"
```

### Key sections

- `meta`: title, artist, key, and other metadata
- `audio`: backing and vocal stems, with groups and track mappings
- `video`: output definitions and optional layout override
- `maestro`: export configuration for a zipped Maestro package
- `timing`: how lyrics and beats are measured and timed
- `riffs`: reusable riff patterns
- `chords`: reusable chord labels
- `events`: the actual song structure, including lyric lines, riffs, markers, and chord changes

### Event model

Each entry inside `events` is a single musical event. Typical fields include:

- `marker`: section label such as `Verse 1` or `Chorus`
- `lyric`: text to display for that section
- `riff`: a riff string to show and play
- `chords`: chord label or lookup reference like `@verse`
- `instruction`: a special action / section instruction when no lyric text is shown
- `root`: optional override for the root note used when interpreting a riff

A line may use lyric and riff together, or a separate instruction event may trigger a riff-only section.

---

## Timing and lyric map files

Lyrics and chord timing are usually tied to a timing file separate from the song YAML. The `timing.lyrics.map_file` entry tells Praecentor where to find the alignment data:

```yaml
timing:
  lyrics:
    source: tap
    map_file: "Bounces/Lyrics.yaml"
    base_offset_seconds: -.6
    max_line_chars: 36
    lead_in_beats: 2
```

This separate map is used to sync lyric phrases, section boundaries, and chord transitions with the audio track. In many songs, the lyrics and chords are connected in the event stream and adjusted together.

---

## Beat and lyric tapping

The tap workflow is designed for live alignment. It plays the song and lets you press Enter at each chord change or lyric boundary, creating a map that Praecentor can use to render the final timing.

```bash
praecentor tap lyrics Praecentor.yaml
praecentor tap beats Praecentor.yaml
```

This is especially useful when you are working at the keyboard and do not want to hand-edit all timing offsets. The CLI accepts optional overrides such as `--audio` and `--autosave-every`, and the `--dry-run` mode validates paths and config without starting capture.

A typical beat/lyrics flow is:

1. open the song file
2. play the backing track
3. tap when the next chord change or lyric event occurs
4. save the map and adjust the offsets if needed
5. render the final video

---

## Layout overrides

Praecentor can accept an optional layout YAML file that overrides rendering details for the video output. This is useful when you want custom styling, sizing, or behavior without rewriting the song structure.

```yaml
video:
  layout:
    file: "/path/to/layout.yaml"
```

The layout file is optional, and if provided it may override advanced rendering settings such as the visual layout, title card presentation, and common video display options. This allows the same song file to be rendered in different visual styles for different contexts.

---

## Rendering videos

Render a song into its configured outputs:

```bash
praecentor render Praecentor.yaml
```

Optional render flags:

```bash
praecentor render Praecentor.yaml \
  --layout /path/to/layout.yaml \
  --output-video /path/to/output.mp4 \
  --from-seconds 00:30 \
  --to-seconds 03:45
```

Useful rendering options include:

- `--layout`: provide a layout override file
- `--beat-seconds`: override beat duration for title-card countdown timing
- `--countdown-beats`: set how many beats are shown before the backing starts
- `--maestro-only`: write the Maestro zip without muxing full video output
- `--video-encoder`: choose the FFmpeg encoder
- `--video-preset`, `--video-crf`, `--video-bitrate`: tweak output quality
- `--ignore-stem-length-mismatch`: skip audio stem validation if needed

---

## Output variants

The song configuration can describe multiple outputs. A common pattern is:

- session output: just the backing track
- practice output: base parts + guide vocals
- performance output: backing/music plus piano arrangement, no guide vocals

This is controlled by the `video.output` section and the chosen stem groups and tracks.

Example:

```yaml
video:
  output:
    - file: "Video/Session.mp4"
      tracks:
        - backing.base
    - file: "Video/Performance.mp4"
      tracks:
        - backing.*
    - file: "Video/Practice.mp4"
      tracks:
        - backing.base
        - vocals.*
```

This gives you multiple ready-to-use mixes from one song definition without duplicating the musical content.

---

## Validation and debugging

Before rendering, it helps to validate the song file:

```bash
praecentor validate Praecentor.yaml
```

Inspect and debug the timeline:

```bash
praecentor timeline Praecentor.yaml
praecentor diagnose beats Praecentor.yaml
```

These inspections help check that the event structure, timing offsets, and beat detection look sane before video generation begins.

---

## Example: working with the attached song file

The provided example is a real-world use case: a song defined with backing stems, vocal stems, event-based lyric structure, dynamic timing, and multiple output videos.

It includes all the major ingredients:

- `audio.stems` for track groupings
- `video.output` for exporting several versions
- `timing.lyrics` and `timing.beats` for tempo and lyric alignment
- `riffs` and `chords` for reusable musical components
- `events` for section markers, lyric lines, and riffs

This is the core authoring model Praecentor is built around.

---

## Typical use case

Praecentor is ideal for any live music setup where the pianist or accompanist needs:

- a clear visual timeline
- chord and riff prompts
- lyric synchronisation
- rehearsal-ready backing tracks
- multiple audience- and practice-specific render modes

It's designed to support the workflow of a singer/pianist or small ensemble rehearsing a song in real time, with the visual output serving as a conductor, lyric prompter, and timing guide.

---

## Notes

- Song authoring is YAML-based and intentionally structured for fast iteration.
- Timing can be either automatic or captured interactively by tapping.
- Rendering depends on ffmpeg and working media file paths.
- The project is best used as a local tool for rehearsal and performance prep.

---

## License

This project is intended for local music production and rehearsal use. Please check the repository for the current license details before redistributing or publishing generated media.
