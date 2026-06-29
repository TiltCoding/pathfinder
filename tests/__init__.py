"""Test package bootstrap.

The suite runs offline stdlib `unittest` and imports the code under test from
`scripts/` (`import server`, `import _aipf`, `import queue`). `unittest discover
-s tests` and `python -m unittest tests.test_x` both import this package before
any test module, so putting `scripts/` on `sys.path` here once is the single
source of that setup — new tests don't need to repeat the per-file shim.

(Note: this is the unittest-correct mechanism — `conftest.py` is a pytest concept
and is NOT auto-loaded by unittest, which is what CI/`dev.py test` use. A bare
`python tests/test_x.py` does not import this package; run tests via
`python -m unittest tests.test_x` or `discover` so this bootstrap applies.)
"""
import os
import sys

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
