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

``convert lyrics``
   Convert raw lyric text into event YAML blocks for authoring.

``convert riff``
   Convert notes or notation into a supported Praecentor riff string.
