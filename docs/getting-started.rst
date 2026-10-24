Getting started
===============

Requirements
------------

Before running Praecentor, make sure you have:

- Python 3.8 or newer
- ffmpeg available on your PATH
- a local copy of the Inter font files installed somewhere on your machine

.. note::

   The Inter font files are intentionally not shipped with the repository. They
   should be downloaded locally and kept outside the project tree.

Install the project
-------------------

.. code-block:: bash

   cd /path/to/praecentor
   python -m pip install -r requirements.txt
   python -m pip install -e .

Check that the CLI is available:

.. code-block:: bash

   praecentor --help

Set up local fonts
-------------------

Download Inter and place the font files in a local directory such as:

.. code-block:: bash

   ~/Library/Fonts/Inter/

Then tell Praecentor where to find them:

.. code-block:: bash

   export PRAECENTOR_FONTS_DIR="$HOME/Library/Fonts/Inter"

You can also set the directory in a render/layout configuration:

.. code-block:: yaml

   render:
     font_dir: "/Users/you/Library/Fonts/Inter"

This keeps the repository clean and avoids committing licensed font assets.
