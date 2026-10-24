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
