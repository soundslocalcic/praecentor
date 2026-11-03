CLI reference
=============

Praecentor exposes a small command set for validation, timing capture, timeline
inspection, and rendering.

Core commands
-------------

.. code-block:: bash

   praecentor validate SONG.yaml
   praecentor tap lyrics SONG.yaml
   praecentor tap beats SONG.yaml
   praecentor timeline SONG.yaml
   praecentor diagnose beats SONG.yaml
   praecentor render SONG.yaml
   praecentor preview-layout [LAYOUT.yaml] --sample all --output layout-preview.png
   praecentor convert lyrics
   praecentor convert riff

Command overview
----------------

``validate``
   Check that a song file is structurally valid before rendering.

``tap lyrics``
   Capture lyric timing interactively by pressing Enter at lyric boundaries.

``tap beats``
   Capture beat timing interactively for the musical timeline.

``timeline``
   Inspect the final cue ordering and rendered timeline assumptions.

``diagnose beats``
   Inspect detected beat timing and dynamic-tempo behaviour.

``render``
   Produce the final MP4 and Maestro outputs defined in the song YAML.

``preview-layout``
   Produce deterministic v2 stills using the actual Pillow renderer, without
   a song, audio or video encode. Omit LAYOUT.yaml to use the packaged draft
   containing all supported elements. ``--sample all`` writes a labelled
   contact sheet and separate full-size frames; other samples write one PNG.
   Use ``--guides`` for bounds and coordinates, ``--background`` to replace
   image layers for inspection, and ``--font-dir`` to select local Inter files.
   ``--show`` explicitly opens the saved PNG via Pillow Image.show(); no viewer
   opens by default. PNG and JSON report output work in headless environments.

``convert lyrics``
   Convert raw lyric text into event YAML blocks for authoring.

``convert riff``
   Convert notes or notation into a supported Praecentor riff string.
