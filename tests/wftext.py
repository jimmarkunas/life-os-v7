"""LIFE-1013: the generated workflows write repeats as YAML anchors and aliases; a test that reads one as text reads it through here to get the plain text back (YAML readers need nothing)."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import wfsets  # noqa: E402


def text(path):
    return wfsets.expand_text(pathlib.Path(path).read_text())
