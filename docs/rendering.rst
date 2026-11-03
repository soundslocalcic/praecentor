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
declaring ``version: 1``. Version dispatch and the eventual strict v2 default
are separate later steps; v2 is not implemented by this change.

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
