Rendering
=========

The render workflow produces the final MP4 output files described in the song
configuration. The command is the main path for generation:

.. code-block:: bash

   praecentor render Praecentor.yaml

Optional render flags
---------------------

Several CLI options are available when you need more control over export:

.. code-block:: bash

   praecentor render Praecentor.yaml \
     --layout /path/to/layout.yaml \
     --output-video /path/to/output.mp4 \
     --from-seconds 00:30 \
     --to-seconds 03:45

Useful options include:

- ``--layout`` for a custom rendering layout override
- ``--beat-seconds`` for manual beat timing override
- ``--countdown-beats`` to set the pre-roll countdown length
- ``--maestro-only`` to export just the Maestro package
- ``--video-encoder`` to choose an FFmpeg encoder
- ``--video-preset`` and related output quality settings
- ``--ignore-stem-length-mismatch`` when you intentionally want to bypass a
  strict audio-duration check

Output variants
---------------

A single song YAML can emit more than one final render depending on the
``video.output`` entries. A common workflow is:

- Session version: backing only
- Practice version: backing plus guide vocals
- Performance version: arrangement with piano context and no guide vocal stem

Example:

.. code-block:: yaml

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

This keeps the authoring structure centralized while allowing multiple output
mixes from the same song.

Frozen v1 layout path
---------------------

The legacy layout renderer is frozen against source revision
``fb846c77811ef8e53f4a1e98a505f9a423fe8677``. Its explicit entry point is
``praecentor.render.v1``; its loader lives in ``praecentor.io.layout_v1``.
The title, cue and background implementations remain in their original render
modules and must retain v1 semantics when a separate v2 renderer is introduced.

During this rollout stage, unversioned layouts still use v1, as do layouts
declaring ``version: 1``. Explicit v2 schemas, version dispatch and v2 drawing
are available. The eventual strict v2 default is a later coordinated cutover,
not part of this rollout stage.

V1 retains its original inferred regions and region-fitted text. Partial
typography/region mappings are not deep-merged with element presets, and the
editor-era ``components`` allowlist has no effect on the legacy renderer.

Seven 1920x1080 Inter PNG references and a provenance/measurement manifest live
under ``tests/fixtures/layout_v1``. They cover title/countdown, first-chord
preview, lyrics, short/long chord rows, warnings, riffs, layered backgrounds
and postlude. Run the reference check with the original Inter font files:

.. code-block:: bash

   PRAECENTOR_FONTS_DIR=/path/to/Inter/fonts \
     python -m pytest tests/test_controllers/test_layout_v1.py -q

The manifest records font hashes, Pillow/FreeType versions and the original
source revision. Without the supplied Inter fonts, ordinary legacy tests run
but the pixel-reference test is skipped. Different Pillow/FreeType versions
skip exact pixel comparison after the measurement checks. Do not regenerate
references to make a v1 behavioural change pass; these are the frozen baseline,
not approval of the future v2 default layout.

V2 layout contract
-------------------

V2 uses an absolute 1920x1080 pixel canvas. Its root fields are ``version``,
``canvas``, ``background``, ``title``, and ``song``. Unknown fields are errors;
legacy ``video``, ``regions``, ``typography``, ``render`` and ``components``
settings must be converted, not relabelled as v2. Song-file version and
layout-file version are separate values.

Minimal valid layout (empty canvases, no foreground elements):

.. literalinclude:: ../tests/fixtures/layout_v2/minimal.yaml
   :language: yaml

``canvas`` may be omitted, but if specified its dimensions must be 1920x1080.
Omitted title/song sections mean empty element maps. No element is implicitly
created. ``title.enabled`` defaults to true; optional ``title.countdown_beats``
must be a non-negative integer. A YAML mapping may not repeat any key in v2.

``title.elements`` supports ``title_text``, ``metadata_text``, ``countdown_text``
and ``first_chord_preview``. ``song.elements`` supports ``lyric_main``,
``chord_main``, ``next_chord``, ``beat_indicator`` and ``riff``. Elements are
keyed by name and may occur once on their respective canvas. Each included
element must supply all four of ``x``, ``y``, ``width`` and ``height`` directly
on the element mapping. There are no geometry defaults or cross-element
inferences.

Coordinates are finite numbers in pixels: ``0.5`` means half a pixel, not half
the canvas. Width and height must be positive. Percentage strings, booleans,
NaN and infinities are invalid. Negative coordinates and rectangles extending
outside the canvas are allowed and preserved for intentional cropping; the
validator does not silently clamp or reposition them. Text fitting concerns
the element rectangle, not its visible intersection with the output canvas.

Text elements accept a nested ``style`` mapping. Its supported fields are
``font_family`` (currently Inter only), ``weight`` (300, 400, 500, 600, 700 or
800), ``size_px`` (positive), ``color`` (#RGB or #RRGGBB), ``align``
(left/center/right), ``valign`` (top/center/bottom), ``line_height`` (positive
ratio), and ``letter_spacing_em`` (-1 to 1). The lyric style also supports
``split_a_color``, ``split_b_color`` and ``split_c_color``.

Style defaults use Inter, weight 700, white, centred alignment in both axes,
line height 1 and zero letter spacing. Title weight defaults to 800, metadata
colour to #D9D9D9 and next-chord colour to #FF9F1C. Default sizes are 78, 42,
72 and 48 for title/metadata/countdown/first-chord preview; 94, 76 and 64 for
lyrics/current chord/next chord. These are schema fallback values, not the
future default v2 composition. The complete example explicitly sets larger
chord sizes. We will jointly tune and approve the default composition later.

In v2 all text sizes are maxima: drawing must shrink text only when needed to
fit and must never enlarge it to fill a region. This is intentionally different
from v1's region-fitted text. Drawing measures actual glyph bounds, including
mixed-font musical accidentals and multiline spacing, then shrinks only when
needed. A text block that cannot fit even at one pixel is not drawn. Validation
alone does not claim to have measured text or approved a layout's appearance.

Per-element appearance and behaviour
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Additional nested settings are supported only on the following elements:

- ``metadata_text.behavior``: boolean ``show_artist``, ``show_key`` and
  ``show_hot_start`` flags, all defaulting to true.
- ``lyric_main.behavior``: ``scroll_preview_beats``, ``preview_opacity`` and
  ``preview_margin_px``.
- ``next_chord.appearance``: ``ring``, ``warning`` and ``loop`` mappings.
  ``next_chord.behavior.lookahead_beats`` controls the lookahead window.
- ``first_chord_preview.appearance``: ``padding_px`` and ``ring``.
  Its ``behavior`` supports ``slide_beats`` and ``slide_lead_beats``.
- ``beat_indicator.appearance``: ``fill_color``, ``pulse_first_color``,
  ``pulse_other_color``, ``pulse_expand_px`` and ``pulse_width_px``.
- ``riff.appearance``: ``viewport_beats``, ``center_fraction``,
  ``pre_roll_beats``, ``post_roll_beats``, ``lane_gap_px``,
  ``note_side_margin_px``, ``note_height_ratio``, ``lane_color``, ``note_color``,
  ``active_note_color``, ``playhead_color`` and ``playhead_width_px``.

Ring settings are ``enabled``, ``color``, ``width_px``, ``padding_px``,
``dotted``, ``dash_degrees`` and ``gap_degrees``. Warning settings are
``enabled``, ``color``, ``icon_scale``, ``gap_px``, ``flash_hz`` and
``flash_duty_cycle``. Loop settings are ``icon_scale`` and ``y_offset_px``.
These are decorations of their owning element, not references to another
element's rectangle. There is no inferred pre-roll or ring size ratio in v2.

Opacity/fraction/duty-cycle settings are bounded by 0 and 1; counts, gaps,
padding and durations are non-negative. Text sizes, line height, viewport
beats, note height, icon scale, dash angle, scroll/slide duration and lookahead
window must be greater than zero. Nested settings merge with their own style
defaults without changing geometry or mutating the caller's data.

Backgrounds and complete example
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``background`` has a colour (default black) and an ordered ``layers`` list.
Each image layer requires ``type: image`` and exactly one non-empty ``file``
or ``media`` reference. ``file`` is a layout-relative or absolute image path;
``media`` is a stored upload ID to be resolved by the application before
rendering. ``opacity`` defaults to 1 and must be between 0 and 1. Validation
does not fetch uploads or rewrite references to temporary paths.

This example exercises every supported element. Its coordinates and font
sizes are illustrative, not the approved default v2 layout. It uses a
placeholder background path and is not a request to render a real song:

.. literalinclude:: ../tests/fixtures/layout_v2/complete.yaml
   :language: yaml

Versioned Python entry points
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use ``praecentor.io.layout.load_layout_config(path)`` for YAML loading or
``resolve_layout_config(mapping)`` for in-memory validation. Both return
separate mappings; neither writes defaults into the input or a file.

Use ``praecentor.render.layout.render_cue_frames(layout_data=..., ...)`` and
``render_title_countdown_frames(layout_data=..., ...)`` for version-aware
direct frame calls. Pass the full root layout, not just its title subsection,
when selecting v2. Legacy ``title_layout=...`` calls remain supported as v1.
The explicitly legacy ``praecentor.render.v1`` entry point and the original
title/cue modules remain frozen v1 implementations, not version autodetectors.

The controller and version-aware frame calls select only by the explicit
integer version (no bools, strings or floats). Missing version still selects
v1 during this rollout, without guessing from field names. Explicit v2 calls
validate their schema and use ``praecentor.render.v2``, never the v1 frame
renderer. Future cutover will change the missing-version default to v2 and
reject legacy-shaped files.

V2 drawing behaviour
~~~~~~~~~~~~~~~~~~~~~

V2 outputs 1920x1080 frames. The controller uses 24 fps and shares the normal
audio/tap-map, beat analysis, section selection, encoding and muxing pipeline.
Direct frame calls may use another positive integer frame rate for inspection
but must retain the fixed canvas dimensions. Each emitted frame invokes the
progress callback once; calls can write PNGs or send frames to a frame sink.

Elements paint in their map order. Each element's drawing, including rings,
warnings, pulse effects and riffs, is clipped to its own rectangle and the
canvas. There is no implicit wash image, gutter or geometry derived from a
neighbouring element. ``align`` and ``valign`` place fitted text inside its
rectangle. Negative/off-canvas positions are cropped, not moved back on screen.

Song content still determines visibility. Chords carry forward until changed
or explicitly cleared. When an included riff is active, it replaces the
current-chord display; omitting the riff element leaves chords available.
Explicit riff pre/post-roll settings and the beat grid control its timing.
Next-chord lookahead honours explicit silence, repeated-chord loop symbols,
warning flashes and its configured beat window. Decorations never enlarge
their owning element's rectangle. Warning flashing does not change text fit.

The lyric element displays lyrics or instruction text. When a later distinct
line exists, its own rectangle is split into current/preview rows using
``preview_margin_px``. Preview opacity and scrolling use that element's
behaviour plus the song's lyric lead-in; they do not depend on other elements.
Part colours apply to the corresponding lyric lines. Postlude retains the last
visible lyric and current chord, and keeps the beat clock only when requested.

For backgrounds, use ``build_background_layer_renderer(layout_data=...,
layout_path=..., width=1920, height=1080)`` from the version-aware render module
and pass its ``next_frame`` as ``background_frame_provider`` to direct frame
calls. Close the background renderer afterwards if it is not None. The main
controller manages this lifetime automatically. Stored ``media`` IDs must be
resolved to file paths by the application before drawing; no upload is fetched
or modified by Praecentor. A solid background needs no provider.

The complete schema example is still illustrative, not the approved default
v2 layout. Sample-preview tooling and joint default-layout tuning are the next
roadmap step; no default composition is frozen by this drawing implementation.
