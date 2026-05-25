# Configuration file for the Sphinx documentation builder.

from __future__ import annotations

import os
import sys

# Make the package importable for autodoc.
sys.path.insert(0, os.path.abspath(".."))

# -- Project information --------------------------------------------

project = "ThermoFooty"
copyright = "2026, Bart R. H. Geurten"
author = "Bart R. H. Geurten"
release = "0.1.0-dev0"

# -- General configuration ------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",        # Google-style docstrings
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "myst_parser",                # Markdown support
    "sphinx_copybutton",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# Napoleon — Google-style docstrings
napoleon_google_docstrings = True
napoleon_numpy_docstrings = True

# Autodoc — mock heavy / network deps so docs build on cheap CI
autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_mock_imports = [
    "meteostat",
    "cdsapi",
    "xarray",
    "netCDF4",
    "geopy",
    "pypdf",
    "pdfplumber",
    "bs4",
    "requests",
]

# Intersphinx — link to scientific Python ecosystem
intersphinx_mapping = {
    "python":       ("https://docs.python.org/3", None),
    "numpy":        ("https://numpy.org/doc/stable/", None),
    "scipy":        ("https://docs.scipy.org/doc/scipy/", None),
    "pandas":       ("https://pandas.pydata.org/docs/", None),
    "rerandomstats": ("https://zerotonin.github.io/reRandomStats/", None),
}

# -- Options for HTML output ----------------------------------------

html_theme = "furo"
html_title = "ThermoFooty Documentation"
html_static_path = ["_static"]
