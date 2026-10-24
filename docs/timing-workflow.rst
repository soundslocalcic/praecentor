Timing workflow
===============

Praecentor supports two main timing strategies:

- automatic beat detection from the audio file
- interactive tapping to capture lyrics and beat timing

Automatic timing
----------------

When the song config has a beat source like ``auto`` or a click track, the
render pipeline can infer timing from the audio signal and use that to drive the
beat grid and cue placement.

Interactive tapping
-------------------

The tap commands are designed for live alignment, especially when a pianist is
playing along and marking musical boundaries in real time:

.. code-block:: bash

   praecentor tap lyrics Praecentor.yaml
   praecentor tap beats Praecentor.yaml

The CLI can play the relevant audio and wait for Enter presses to mark chord or
lyric transitions. This is useful when it is faster to record timing while
listening than to hand-edit YAML offsets.

Timing maps
-----------

The ``timing.lyrics.map_file`` and related timing paths tell Praecentor where the
captured data lives. In other words, the song YAML defines the overall structure,
while the timing maps describe the precise moment that each event should appear.

This split keeps the song definition readable while preserving the exact timing
information required for effective video rendering.

Validation
----------

Before making a final render, validate the song definition:

.. code-block:: bash

   praecentor validate Praecentor.yaml

And inspect the timeline if needed:

.. code-block:: bash

   praecentor timeline Praecentor.yaml
   praecentor diagnose beats Praecentor.yaml
