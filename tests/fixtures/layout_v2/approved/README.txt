APPROVED V2 DEFAULT REFERENCES
==============================

Approved by the user after full-song renders on 2026-11-03.
manifest.json records the resolved default spec, source revision, font hashes
and Pillow/FreeType versions. The eleven PNGs use the real preview renderer's
deterministic sample states. They freeze the accepted default, not just a schema.

Run with the recorded Inter fonts:
  PRAECENTOR_FONTS_DIR=/path/to/Inter/fonts python -m pytest tests/test_controllers/test_preview.py -q -k approved

The resolved-spec test always runs. Exact pixel comparison requires the recorded
Inter/accidental font files and Pillow/FreeType versions; it skips otherwise.
Do not regenerate these merely to make a renderer change pass. Future default
changes need user review and intentional reference approval.
