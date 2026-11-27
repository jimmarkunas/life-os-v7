"""fake conformance."""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def load(family):
    with open(os.path.join(HERE, "cases", family + ".json")) as handle:
        return json.load(handle)


def recording(name):
    with open(os.path.join(HERE, "recordings", name + ".json")) as handle:
        return json.load(handle)


def differences(spec, subject_name, observe):
    """> {case name: {observable: (expected, got)}} for every case this subject runs (an `applies_to` list limits it)"""
    out = {}
    for case in spec["cases"]:
        if "applies_to" in case and subject_name not in case["applies_to"]:
            continue
        got = observe(case)
        want = case["expect"]
        out[case["name"]] = {key: (want[key], got.get(key)) for key in want if got.get(key) != want[key]}
    return out


def problems(spec, subject_name, observe):
    """> list of human-readable problems"""
    gaps = spec.get("gaps", {}).get(subject_name, {})
    found = differences(spec, subject_name, observe)
    out = []
    for name, diff in found.items():
        if diff and name not in gaps:
            out.append(f"{subject_name} differs on '{name}': {diff}")
        if not diff and name in gaps:
            out.append(f"{subject_name} no longer differs on '{name}': remove its documented gap ({gaps[name]})")
    for name in gaps:
        if name not in found:
            out.append(f"documented gap '{name}' names no case this subject runs")
    return out
