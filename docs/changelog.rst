Changelog
=========

2026.3.0
--------

Added
^^^^^

- Optional explicit audio track IDs for named multitrack outputs in MP4, MOV, and M4V files. IDs are validated for uniqueness and range; track labels and handler names are retained.

Changed
^^^^^^^

- Reduced the default lyric lead-in from four beats to two.
- Improved beat and click-track analysis consistency by resampling audio to a standard analysis sample rate.

Fixed
^^^^^

- Corrected lookup of the bundled wash image asset during cue rendering.

2026.2.0
--------

This release follows the 2026.1.0 release and includes the layout and rendering adjustments shipped in the v2026.2.0 tag.

Added
^^^^^

- Optional multi-channel audio output configuration for render targets, allowing named output channels to map to multiple selected stems.

Changed
^^^^^^^

- Expanded the lyric display region so longer lines render more cleanly.
- Improved layout text controls with explicit alignment support and letter spacing options.
- Simplified the render pipeline around direct frame encoding and removed legacy background-layer handling.

Fixed
^^^^^

- Fixed background images being skewed when resized or composited.
- Corrected text alignment issues in rendered output.
- Removed duplicated or unused asset files and refreshed editor defaults.

