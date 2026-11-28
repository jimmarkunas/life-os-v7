"""the generated workflows write repeats as YAML anchors and aliases; domains.yml and hourly.yml are callers, so the pipeline's own structure is read from its logical text"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import wfsets  # noqa: E402
import wfsplit  # noqa: E402

LOGICAL = {"domains.yml": "domains", "hourly.yml": "hourly"}


def logical(name):
    """the whole pipeline `name` ("domains" or "hourly") as the one document the parts describe (every job and step; the text the single generated file held before it was cut into a caller and reusable workflows)."""
    return wfsplit.logical_compressed(name)


def text(path):
    path = pathlib.Path(path)
    if path.parent.name == "workflows" and path.name in LOGICAL:
        return wfsets.expand_text(logical(LOGICAL[path.name]))
    return wfsets.expand_text(path.read_text())


def called(body):
    """the text of the reusable workflow a caller job `uses`, and of everything that one calls ('' when the job calls none): a caller holds no steps, so what its job does is read from here."""
    import yaml

    uses = body.get("uses") if isinstance(body, dict) else None
    if not uses or not str(uses).startswith("./.github/workflows/"):
        return ""
    doc = yaml.safe_load(text(ROOT / str(uses).removeprefix("./")))
    return "".join(yaml.safe_dump(job) + called(job) for job in (doc.get("jobs") or {}).values())


def plain(name):
    """the logical pipeline `name` with its YAML aliases and `set:` names written out"""
    return wfsets.expand_text(logical(name))


WORKFLOWS = ROOT / ".github" / "workflows"
CALLED = ("domain-", "tick-", "diag-")  # the reusable workflows the three callers (domains, hourly, diag) hand their jobs to


def _flat_diag():
    import yaml

    files = {p.name: p.read_text() for p in WORKFLOWS.glob("diag*.yml")}
    return yaml.safe_dump(wfsplit.flatten(files, "diag.yml"), sort_keys=False, width=10**6)


def read(path):
    """the text of the workflow at `path`; for domains, hourly and diag (callers now) the one logical document with every job's steps, else the file as it is"""
    path = pathlib.Path(path)
    if path.parent.name == "workflows" and path.name in LOGICAL:
        return logical(LOGICAL[path.name])
    if path.parent.name == "workflows" and path.name == "diag.yml":
        return _flat_diag()
    return path.read_text()


def workflow_texts(expand=True):
    """{stem: text} of every top-level workflow (the reusable ones a caller hands jobs to are read through their caller), aliases and `set:` names written out when `expand`"""
    out = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        if path.name.startswith(CALLED):
            continue
        out[path.stem] = wfsets.expand_text(read(path)) if expand else read(path)
    return out


WORKFLOWS = ROOT / ".github" / "workflows"
CALLED = ("domain-", "tick-", "diag-")  # the reusable workflows the three callers (domains, hourly, diag) hand their jobs to


def _flat_diag():
    import yaml

    files = {p.name: p.read_text() for p in WORKFLOWS.glob("diag*.yml")}
    return yaml.safe_dump(wfsplit.flatten(files, "diag.yml"), sort_keys=False, width=10**6)


def read(path):
    """the text of the workflow at `path`; for domains, hourly and diag (callers now) the one logical document with every job's steps, else the file as it is"""
    path = pathlib.Path(path)
    if path.parent.name == "workflows" and path.name in LOGICAL:
        return logical(LOGICAL[path.name])
    if path.parent.name == "workflows" and path.name == "diag.yml":
        return _flat_diag()
    return path.read_text()


def workflow_texts(expand=True):
    """{stem: text} of every top-level workflow (the reusable ones a caller hands jobs to are read through their caller), aliases and `set:` names written out when `expand`"""
    out = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        if path.name.startswith(CALLED):
            continue
        out[path.stem] = wfsets.expand_text(read(path)) if expand else read(path)
    return out
