"""LIFE-549: fake conformance. Behavior cases are DATA (tests/kit/conformance/cases/<family>.json), written once and run against every subject:
the fake, and ground truth (the real library over a loopback server, the real library's API, or a recorded and scrubbed real response shape).
A case names what it expects; `gaps` lists, per subject, the cases where that subject is KNOWN to differ, with the reason. A gap that no longer differs fails
(so a documented gap cannot go stale), and an undocumented difference fails (so a fake cannot drift unnoticed). Domain-free: imports no lifeos code."""
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
    """-> {case name: {observable: (expected, got)}} for every case this subject runs (an `applies_to` list limits it); `observe(case) -> dict`."""
    out = {}
    for case in spec["cases"]:
        if "applies_to" in case and subject_name not in case["applies_to"]:
            continue
        got = observe(case)
        want = case["expect"]
        out[case["name"]] = {key: (want[key], got.get(key)) for key in want if got.get(key) != want[key]}
    return out


def problems(spec, subject_name, observe):
    """-> list of human-readable problems; empty means the subject conforms (apart from its documented gaps)."""
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
