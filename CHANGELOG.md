# Changelog

All notable changes to Praecentor are documented here.

## [Unreleased]

### Changed
- Freeze the original v1 layout path behind `praecentor.render.v1`, preserving legacy loading, inferred geometry and text fitting while v2 is developed separately.

### Added
- Deterministic Inter reference frames, region/font measurements and legacy layout regression checks.
- Strict v2 layout validation for explicit 1920x1080 pixel rectangles, unique elements, style/appearance settings and immutable normalization, with minimal/complete examples.
- Version-aware loading and direct frame entry points; unversioned layouts still use frozen v1 during rollout.
- V2 title/cue/postlude rendering with explicit pixel geometry, shrink-only glyph fitting, clipped decorations, background layers, and shared song/beat timing. Includes mixed accidentals, lyric previews, riff transitions and real encoder integration checks.
- `preview-layout` generates deterministic renderer-backed sample frames, contact sheets, optional guides and font reports, with opt-in Pillow viewer launching and strict Inter checks.
- A packaged all-elements v2 draft template for collaborative visual review; final default-layout approval is still pending.

### Fixed
- Extend the default lyric scroll from the video top to the chord-row centre and fade its edges with a cached alpha-only mask; current chords are left-aligned.
- Enlarge the default beat pie by 25% around its existing centre without moving lookahead, and include the same pie on the title canvas with the song meter.
- Default v2 outgoing scroll blocks fade to zero, with a 1.5rem/141px canvas-reference event gap and preserved explicit em/pixel overrides.
- Centre the lyric viewport above aligned bottom-row chord/riff, lookahead and beat-pie boxes; match title/event lookahead geometry and appearance by default.
- Default v2 lyric/instruction line height is 1.2; explicit relative/pixel event-gap overrides remain supported.
- Long-break progress bars are timed blocks within the continuous scroll, sharing its movement, fades, centring and clipping rather than replacing it.
- Default v2 repeat icons are approximately half their former size while the dotted-ring footprint is unchanged.
- V2 lyrics use one measured event-centred scroll with partially clipped neighbours, fading 100%/50%/25% active/upcoming/outgoing text, long-break progress bars and optional entrance/final-exit motion. Adds a `lyrics-long` preview state.
- Sparse v2 specs now inherit the shared all-elements template; element/canvas `enabled: false` explicitly opts out without expanding stored YAML or changing v1 semantics.
- V2 rendering reuses bounded fitted-text/sprite caches instead of measuring and rasterising unchanged text every frame.
- V2 beat pies sweep clockwise and leave the elapsed area transparent. The next-chord indicator retains its repeat symbol between changes and counts down each phrase, including tempo/phrase overrides and one-shot extra beats.

## [2026.3.0] - 2026-11-02

### Added
- Optional explicit audio track IDs for named multitrack outputs in MP4, MOV, and M4V files. IDs are validated for uniqueness and range; track labels and handler names are retained.

### Changed
- Reduced the default lyric lead-in from four beats to two.
- Improved beat and click-track analysis consistency by resampling audio to a standard analysis sample rate.

### Fixed
- Corrected lookup of the bundled wash image asset during cue rendering.

## [2026.2.0] - 2026-10-30

This release follows the 2026.1.0 release and brings the major layout and rendering refinements delivered in the v2026.2.0 tag.

### Added
- Optional multi-channel audio output configuration for render targets, allowing named output channels to map to multiple selected stems.

### Changed
- Expanded the lyric display region so longer lines render more cleanly.
- Improved layout text controls with explicit alignment support and letter spacing options.
- Simplified the render pipeline around direct frame encoding and removed legacy background-layer handling.

### Fixed
- Fixed background images being skewed when resized or composited.
- Corrected text alignment issues in rendered output.
- Removed duplicated or unused asset files and refreshed editor defaults.

---
