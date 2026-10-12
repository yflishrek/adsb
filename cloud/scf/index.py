"""Tencent SCF entry point.

SCF resolves the handler as `<file>.<function>`, so this file exists purely to
re-export the collector that lives next to the rest of the pipeline code.  The
deployment zip therefore contains only three files, all shipped flat:

    index.py            <- this file
    scf_collect.py      <- copied from scripts/
    cloud_collect.py    <- copied from scripts/

No third-party packages are needed: cloud_collect.py falls back to urllib when
`requests` is unavailable, which keeps the zip ~20 KB and well inside the
console's upload limit.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scf_collect import main_handler  # noqa: F401  (re-exported for SCF)
