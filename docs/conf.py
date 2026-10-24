from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

project = "Praecentor"
author = "Praecentor contributors"
release = "0.1.0"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosectionlabel",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx_click",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

language = "en"

html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]
html_show_sphinx = False

autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
    "member-order": "bysource",
}

autodoc_mock_imports = [
    "librosa",
]

# Keep the project importable without forcing every dependency to be installed
# at doc-build time. Dependencies are still installed via the docs requirements.
os.environ.setdefault("PRAECENTOR_DOCS_BUILD", "1")
