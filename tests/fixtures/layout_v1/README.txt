V1 RENDERING REFERENCES
======================

Original source revision: fb846c77811ef8e53f4a1e98a505f9a423fe8677

The title, cue and background implementations were verified byte-for-byte
against that revision before these PNGs were generated. manifest.json records
the original source hashes, Inter font hashes, rendering-library versions and
1920x1080 region/font measurements. The original loader is preserved separately
in praecentor/io/layout_v1.py; the public loader currently forwards to it.

Samples use deterministic generated image layers at 50% opacity and a 2 fps
timeline. No full audio/video encode or external song/media files are needed.

title_0.png: title, metadata, hot-start label and first countdown number.
title_7.png: final countdown number and first-chord preview ring.
cue_0.png: current/next lyrics, short chords, warning and beat indicator.
cue_4.png: longer chord row and lyric transition.
cue_8.png: instruction/lyric lookahead, riff lanes, next chord and beat HUD.
cue_12.png: return from riff to chords with the final lyric.
cue_17.png: postlude lyric/chord persistence and beat clock.

Validation:
  PRAECENTOR_FONTS_DIR=/path/to/Inter/fonts python -m pytest tests/test_controllers/test_layout_v1.py -q

Required files: Inter_24pt-Bold.ttf and Inter_24pt-ExtraBold.ttf, with hashes
matching the manifest. Pin the recorded Pillow/FreeType versions for exact
pixel comparison. Missing fonts cause a skip, not a false visual pass.

These freeze legacy v1 behaviour. They are not the proposed or approved v2
default layout. Do not refresh them merely because an implementation changed.