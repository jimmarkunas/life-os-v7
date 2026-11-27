"""the generated workflows write repeats as YAML anchors and aliases"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import wfsets  # noqa: E402


def text(path):
    return wfsets.expand_text(pathlib.Path(path).read_text())
