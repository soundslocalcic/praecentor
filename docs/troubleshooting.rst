Troubleshooting
===============

Missing fonts
-------------

If the render fails and the text looks like it cannot load Inter or related font
files, make sure the font directory exists and is exported correctly:

.. code-block:: bash

   export PRAECENTOR_FONTS_DIR="$HOME/Library/Fonts/Inter"

You can also set the value directly in the config:

.. code-block:: yaml

   render:
     font_dir: "/Users/you/Library/Fonts/Inter"

Stem mismatch warnings
----------------------

Praecentor validates the duration of audio stems before rendering. If a track is
shorter or longer than expected, check:

- the source file length
- the correct track mapping
- whether any stem should be omitted from the render output

Timing issues
-------------

When the lyric or chord cues are off, validate the song and inspect the timing:

.. code-block:: bash

   praecentor validate Praecentor.yaml
   praecentor timeline Praecentor.yaml
   praecentor diagnose beats Praecentor.yaml

These commands help isolate whether the issue comes from YAML structure, timing
maps, or audio detection.

Render failures
---------------

If a render command fails, check:

- ffmpeg is installed and available
- all referenced media files exist
- layout files resolve correctly
- output directories are writable
- the song YAML uses valid event and timing structures
