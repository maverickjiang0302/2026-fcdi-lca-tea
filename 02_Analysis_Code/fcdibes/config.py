"""Filesystem aliases and the impact-category vocabulary.

Folders, file names and versions are defined once, in `datalayer.py`; the
names below are thin aliases of its folders, kept so that code written
against the old layout still imports. Nothing here computes a path.

    REPO_ROOT    project root (the folder that holds 01_Data, 02_Analysis_Code, ...)
    DATA_DIR     the reference folder: it holds every input under its registered
                 file name (this is what `data_dir` arguments mean)
    RESULTS_DIR  the committed processed-output folder
    FIGURES_DIR  the committed figure folder

The aliases are fixed at import time and always name the COMMITTED folders. Code
that writes must use the run-aware functions of the data layer (`out()`,
`fig_file()`), which resolve run folders such as verify_run and quick_run.
"""

from __future__ import annotations

from . import datalayer as _dl

REPO_ROOT = _dl.ROOT
DATA_DIR = _dl.REF_DIR
RESULTS_DIR = _dl.PROC_DIR
FIGURES_DIR = _dl.FIG_DIR

DEFAULT_SCENARIO = "base"

# TRACI 2.1 categories, in the order they appear in every table and figure.
# The parenthesised unit is part of the key because that is how the source
# workbooks label the columns.
IMPACT_CATEGORIES = [
    "Acidification (kg SO2 eq)",
    "Carcinogenics (CTUh)",
    "Ecotoxicity (CTUe)",
    "Eutrophication (kg N eq)",
    "Fossil fuel depletion (MJ surplus)",
    "Global warming (kg CO2 eq)",
    "Non carcinogenics (CTUh)",
    "Ozone depletion (kg CFC-11 eq)",
    "Respiratory effects (kg PM2.5 eq)",
    "Smog (kg O3 eq)",
]

# Short labels for axes and tables.
IMPACT_SHORT = {c: c.split(" (")[0] for c in IMPACT_CATEGORIES}


def normalization_key(category: str) -> str:
    """Map a full category label to the key used by the normalisation table.

    'Global warming (kg CO2 eq)' -> 'Global warming'
    """
    return category.split(" (")[0]
