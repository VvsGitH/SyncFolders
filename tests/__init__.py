"""Puts src/ on sys.path so the test modules can import the tool's modules.

Discovery imports this package before any test module, so the insertion always
happens in time. It is what keeps `python -m unittest discover -s tests -t .`
working with no install step and no build.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
