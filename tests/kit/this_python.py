"""a script under test that runs a bare `python3` must get the SAME interpreter the tests run with (CI has one Python)
"""
import os
import sys


def env(base=None):
    """The environment with the folder of the running interpreter first on PATH, so `python3` in a child process is this
    Python.
    """
    out = dict(os.environ if base is None else base)
    out["PATH"] = os.path.dirname(os.path.abspath(sys.executable)) + os.pathsep + out.get("PATH", "")
    return out
