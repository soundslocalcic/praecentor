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

Minimal valid layout (inherits the shared template and all foreground elements):

.. literalinclude:: ../tests/fixtures/layout_v2/minimal.yaml
   :language: yaml

V2 YAML is an override of the shared ``assets/layouts/default-v2.yaml`` template.
Missing sections, elements, rectangle fields and styles inherit its values.
Nested mappings merge recursively; lists (including background layers) replace
their default list. Empty mappings mean no overrides, not removal. Resolution
returns a separate mapping and does not expand the original YAML or database spec.
``canvas`` may be omitted, but supplied dimensions must remain 1920x1080.
Optional ``title.countdown_beats`` must be a non-negative integer. A YAML mapping
may not repeat any key in v2.

``title.elements`` supports ``title_text``, ``metadata_text``, ``countdown_text``
``first_chord_preview`` and ``beat_indicator``. ``song.elements`` supports ``lyric_main``,
``chord_main``, ``next_chord``, ``beat_indicator`` and ``riff``. Elements are
keyed by name and may occur once on their respective canvas. Resolved elements
always have explicit pixel ``x``, ``y``, ``width`` and ``height`` values, inherited
from the shared template unless overridden. They are never inferred from
another element's position. For example, changing a chord's X retains its
default Y, width and height.

Disable an element explicitly with a boolean ``enabled: false``:

.. code-block:: yaml

   version: 2
   song:
     elements:
       riff:
         enabled: false
       chord_main:
         x: 100
         style:
           size_px: 150

``title.enabled: false`` skips the title sequence. ``song.enabled: false``
disables song foreground elements while retaining frame timing/backgrounds.
Use both flags for an intentionally background-only layout. Element and canvas
enable flags default to true and survive repeated normalization.

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
line height 1 and zero letter spacing; the lyric/instruction template uses line
height 1.2. Title weight defaults to 800, metadata
colour to #D9D9D9 and title/event lookahead colours to #FF9F1C. The shared template controls
resolved sizes: currently 96, 48, 132 and 132 for title/metadata/countdown/
first-chord preview; 94, 144 and 132 for lyrics/current chord/next chord.
The renderer, previews and future editor all resolve the same template, rather
than maintaining separate geometry or composition defaults.

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
- ``lyric_main.behavior``: ``scroll_preview_beats``, ``preview_opacity``,
  ``outgoing_opacity``, ``event_margin_rem``, ``event_margin_em``, ``preview_margin_px``,
  ``entrance_beats``, ``exit_beats`` and ``edge_fade_px``.
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

``background`` has a colour inherited from the template and an ordered ``layers`` list.
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
current-chord display; explicitly disabling the riff element leaves chords available.
Explicit riff pre/post-roll settings and the beat grid control its timing.
Next-chord lookahead honours explicit silence, repeated-chord loop symbols,
warning flashes and its configured beat window. Outside the change window,
active chords show a repeat symbol instead of hiding the indicator. Its dotted
ring counts down the current phrase continuously, including across long-held
chords and lyric-only events. It uses the beat grid, phrase overrides/resets
and one-shot extra beats, not a slow countdown to a distant chord change.
Inside the window it shows the incoming chord (or explicit silence) while
the phrase ring continues. Decorations never enlarge their owning element's
rectangle. Warning flashing does not change text fit.

The beat pie removes elapsed fill clockwise from twelve o'clock. The unfilled
area is transparent, revealing the layout background rather than a black disc.

The lyric element displays a continuous measured scroll of distinct lyric or
short-instruction blocks. Each block fits against the full lyric rectangle,
not a half-height preview box. Its actual glyph height determines its space in
the scroll. Lyrics and short instructions default to line height 1.2. The
default inter-event gap is ``event_margin_rem: 1.5`` using a fixed 94px canvas
reference, giving 141px. This is a layout unit, not the browser's conventional
16px CSS root: local font-size overrides do not change this gap. An explicit
numeric ``preview_margin_px`` takes precedence; otherwise a numeric
``event_margin_em`` scales with that element's configured font size. Leave
those two settings absent/null to use the default rem gap.
Duplicate consecutive display text and chord-only events do not restart or
clear the scroll.

The active block is vertically centred between transitions. Outgoing text may
remain partially visible above, and future blocks may be clipped below; the
renderer does not shrink everything merely to show a complete preview. Only
blocks intersecting the viewport are drawn, using the bounded glyph caches,
rather than allocating one giant bitmap for the entire song.

The scroll's alpha is multiplied by a cached vertical gradient at both viewport
edges. ``edge_fade_px`` defaults to 282px and may be set to zero for a hard clip.
The fade acts on text/progress opacity, not background colour: image layers,
solid colours and other elements remain untouched. Only the visible mask area
is allocated, including for off-canvas viewports, and normal text/event fades
multiply with the edge fade rather than being replaced by it.
The top fade runs down from the viewport's upper edge; the bottom fade runs up
from its lower edge. This works unchanged with chords above and lyrics below,
or the reverse, without inferring geometry from the chord box. Fade depth is
limited to half the viewport for short boxes so the centre remains fully opaque.
V2 does not load or apply ``wash.png``. That asset remains only for frozen v1
rendering and must not be deleted while v1 compatibility is retained.

During the final ``scroll_preview_beats`` (default 3) before a new display event,
the scroll eases to the next block's centre. Song lyric lead-in can extend the
transition window; arrival remains aligned with the actual cue time. Default
text colour is white, active opacity is 100%, upcoming opacity is 50% via
``preview_opacity``, and outgoing opacity is 0% via ``outgoing_opacity``.
During a transition the outgoing block fades completely out and the incoming
block towards 100%, without a jump at the event boundary. Explicit text/part
colours remain supported.

``entrance_beats`` defaults to zero for an immediately centred first block;
set it above zero to scroll the beginning into view. ``exit_beats`` defaults
to one beat, scrolling the final block off the viewport at the end of the cue
timeline plus postlude; set it to zero to retain the final lyric. Long Intro,
Break and Outro windows use the v1 beat-duration threshold and insert a timed
progress-bar block into the same scroll. It approaches, centres, fills, fades
and leaves with the same margins/motion as text; surrounding lyrics may remain
partially visible. Consecutive break events share one bar and chord-only cues
do not shorten its time window. The final outro's window includes the postlude.
These calculations are internal to the lyric element and do not depend on
other elements' rectangles. Chords persist during postlude and the beat clock
stays only when requested.

Fitted text layouts and small glyph sprites use bounded caches. Moving or
fading unchanged text reuses its fit/rasterisation; cache keys retain geometry,
style, line colours and selected font files. Opacity is applied to a copy, so
one frame cannot alter later cached text. Per-frame text painting composites
only the glyph bounds rather than allocating another full-canvas text image.

For backgrounds, use ``build_background_layer_renderer(layout_data=...,
layout_path=..., width=1920, height=1080)`` from the version-aware render module
and pass its ``next_frame`` as ``background_frame_provider`` to direct frame
calls. Close the background renderer afterwards if it is not None. The main
controller manages this lifetime automatically. Stored ``media`` IDs must be
resolved to file paths by the application before drawing; no upload is fetched
or modified by Praecentor. A solid background needs no provider.

The complete schema example is still illustrative, not the approved default
v2 layout. No default composition is frozen by this drawing implementation.

Preview and tune the draft layout
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The packaged ``praecentor/assets/layouts/default-v2.yaml`` is the shared draft
for visual tuning. It contains every supported Title and Song element with
explicit geometry. Inclusion is not simultaneous visibility: riffs, warnings,
countdowns and other state-dependent content appear only in suitable samples.
By default the lyric viewport is horizontally centred across the canvas, starts
at Y=0 without a top inset and extends to Y=908, bisecting the chord/riff row.
Its lower edge fades out over that row; it paints before the foreground chord
and timing graphics. Current chords/riffs occupy the left 1236x240px box at
(54,788), with current chords left-aligned. Lookahead stays at (1338,788) in a
240px square. The beat pie is 25% larger: a 300px square at (1596,758), retaining
the same centre Y=908 without moving lookahead. Pulse dimensions scale with it.
The title canvas includes the same beat-pie geometry/appearance by default and
the controller supplies the song's beats-per-bar for its countdown clock.
The title's first-chord preview has the same default rectangle, orange text,
font size, ring colour/width and text padding as the event lookahead. Overrides
remain independent: changing an event element does not move a title element.
Custom v2 layouts inherit this template too; omissions retain defaults and
explicit ``enabled: false`` settings hide elements. The template's final visual
approval and reference capture remain separate from this override contract.

Generate a contact sheet from the actual v2 renderer, without audio or FFmpeg:

.. code-block:: bash

   praecentor preview-layout --font-dir /path/to/Inter/fonts \
     --output layout-preview.png

This writes ``layout-preview.png``, a ``layout-preview-frames`` directory of
1920x1080 PNGs, and ``layout-preview.json`` recording the source template,
sample names and selected text/accidental font files. Inter is required for
included text; fallback to a different primary font is an error, not a silent
substitution. Fonts may also be selected through ``PRAECENTOR_FONTS_DIR``.

Preview your own explicit v2 YAML, inspect bounds, or open a local viewer:

.. code-block:: bash

   praecentor preview-layout my-layout.yaml --sample lyrics --guides \
     --output lyrics-guides.png
   praecentor preview-layout my-layout.yaml --sample chords-long --show
   praecentor preview-layout --background /path/to/cola.jpg --show

The positional argument is the layout file itself, not the song's
``Praecentor.yaml``. For a song, use the path named by its
``video.layout.file`` setting. The song may legitimately declare version 1
while that referenced layout declares version 2. A custom file containing
only ``version: 2`` inherits all foreground elements and renders the shared
default composition. Omitting the file argument previews that same composition.

``--background`` replaces the preview's image layers with one fully opaque
image, without modifying the source layout. To judge a particular opacity or
layer stack, set it in your YAML and pass that layout file instead. ``--show``
is opt-in and uses Pillow Image.show(); a desktop viewer may be unavailable
in Docker or CI, so saved PNGs are the portable output.

Samples are ``title``, ``countdown``, ``lyrics``, ``lyrics-long``, ``instruction``,
``chords-short``, ``chords-long``, ``accidentals``, ``riff``, ``warning`` and
``beat``. ``all`` creates a two-column labelled contact sheet plus all original
frames. Guide overlays group coincident rectangles, such as chords/riffs, and
are never applied to normal renders. The samples use deterministic synthetic
song events, not sample text stored inside the layout itself. An input v1
layout is rejected instead of being silently reinterpreted.

The Python entry point is ``praecentor.controllers.preview.preview_layout``;
it returns the saved file paths and font report. The CLI handles optional
viewer launching. Normal still generation never opens a viewer. The workflow
is to edit the draft YAML, regenerate samples, review them with the user, and
only then freeze the approved template and reference frames. Tooling readiness
does not imply default-layout approval or permission to migrate stored layouts.
