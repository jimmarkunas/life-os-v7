"""The one list of job sources (source_registry.json): a new source is its module, its test and one object there.

At import time `lifeos/run.py` adds the stage (exit 1 with the row's `partial_failure` code when counts carry `failed
> 0`), `lifeos/jobs/tick.py` its tick step after the step `after` names, and `lifeos/platform/codes.py` its codes.
`tests/contracts/test_source_registry.py` fails for a stage under lifeos/sources/ that is neither registered nor on
its "not yet migrated" list.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

FILE = Path(__file__).with_name("source_registry.json")


@dataclass(frozen=True)
class Source:
    name: str
    module: str
    function: str = "run"
    args: dict = field(default_factory=dict)
    floor: int = 0
    tick: dict | None = None  # keyword arguments of tick.Step, when the source runs in the Jobs tick
    after: str = ""
    # its own outcome codes: {CODE: (class, counts as attempt, meaning, action[, severity])}
    codes: dict = field(default_factory=dict)
    partial_failure: str = ""


def load(path=FILE):
    return tuple(Source(**{**entry, "codes": {code: tuple(row) for code, row in (entry.get("codes") or {}).items()},
                           "tick": {**entry["tick"], **({"providers": tuple(entry["tick"]["providers"])} if "providers" in entry["tick"] else {})} if entry.get("tick") else None})
                 for entry in json.loads(Path(path).read_text())["sources"])


SOURCES = load()


def stages(lazy, sources=SOURCES):
    """-> {name: stage function}; `lazy` is lifeos.run.lazy."""
    return {s.name: lazy(s.module, s.function, floor=s.floor, **s.args) for s in sources}


def with_tick_steps(base, make_step, sources=SOURCES):
    """-> the tick plan with each source's step inserted after the step it names; an unknown or already-followed `after`
    raises.
    """
    plan, followed = list(base), {}
    for s in sources:
        if s.tick is None:
            continue
        names = [step.name for step in plan]
        if s.after not in names:
            raise ValueError(f"source {s.name}: tick step {s.after!r} is not in the plan")
        if s.after in followed:
            raise ValueError(f"sources {followed[s.after]} and {s.name} both follow tick step {s.after!r}; name the other source's step instead")
        followed[s.after] = s.name
        plan.insert(names.index(s.after) + 1, make_step(s.name, **s.tick))
    return tuple(plan)


def code_rows(make_code, sources=SOURCES):
    """-> {CODE: Code}; `make_code` is called as make_code(source name, *row)."""
    return {code: make_code(s.name, *row) for s in sources for code, row in s.codes.items()}


def partial_failures(sources=SOURCES):
    """-> {stage name: code} for sources that exit 1 on failed > 0."""
    return {s.name: s.partial_failure for s in sources if s.partial_failure}
