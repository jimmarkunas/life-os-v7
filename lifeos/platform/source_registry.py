"""LIFE-618: the one list of job sources, read from source_registry.json. Adding a source = its module + one object in that file (plus its tests); nothing else is edited by hand.

From that list, at import time:
  * `lifeos/run.py` adds the stage (`python -m lifeos.run <name>`), so the hand-kept STAGES line is not needed;
  * `lifeos/jobs/tick.py` adds the source's Jobs-tick step (budget, limit, bulk, group, providers) after the step `after` names;
  * `lifeos/platform/codes.py` registers the source's own outcome codes.
The code map and docs/codemap.json are generated from STAGES (`python -m lifeos.sources.devtools.codemap build`), so they follow without a hand edit.
`tests/contracts/test_source_registry.py` imports every entry, and fails by name for a stage under lifeos/sources/ that is neither registered nor on its short "not yet migrated" list.
Stays by hand, on purpose: a manual-dispatch input or step in a workflow file (protected-file approval; whether a source can be run by hand is a per-source choice).
It is data plus three small functions and imports nothing from lifeos (D17: platform knows nothing about jobs or sources; the modules are named by string, never imported here).
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

FILE = Path(__file__).with_name("source_registry.json")


@dataclass(frozen=True)
class Source:
    name: str                                   # the stage name
    module: str                                 # the module that holds the stage function
    function: str = "run"
    floor: int = 0                              # raise a smaller --limit to this (the same meaning as run.lazy)
    tick: dict | None = None                    # run inside the Jobs tick: the keyword arguments of tick.Step (budget, limit, bulk, group, providers)
    after: str = ""                             # the tick step it follows
    codes: dict = field(default_factory=dict)   # its own outcome codes: {CODE: (class, counts as an attempt, meaning, action[, severity])}


def load(path=FILE):
    return tuple(Source(**{**entry, "codes": {code: tuple(row) for code, row in (entry.get("codes") or {}).items()},
                           "tick": {**entry["tick"], **({"providers": tuple(entry["tick"]["providers"])} if "providers" in entry["tick"] else {})} if entry.get("tick") else None})
                 for entry in json.loads(Path(path).read_text())["sources"])


SOURCES = load()


def stages(lazy, sources=SOURCES):
    """-> {name: stage function} for the registered sources; `lazy` is lifeos.run.lazy (passed in so this file imports nothing)."""
    return {s.name: lazy(s.module, s.function, floor=s.floor) for s in sources}


def with_tick_steps(base, make_step, sources=SOURCES):
    """-> the tick plan `base` with each registered source's step inserted after the step it names. `make_step` is tick.Step. An `after` that is not in the plan raises, so a typo is never silent."""
    plan = list(base)
    for s in sources:
        if s.tick is None:
            continue
        names = [step.name for step in plan]
        if s.after not in names:
            raise ValueError(f"source {s.name}: tick step {s.after!r} is not in the plan")
        plan.insert(names.index(s.after) + 1, make_step(s.name, **s.tick))
    return tuple(plan)


def code_rows(make_code, sources=SOURCES):
    """-> {CODE: Code} for the registered sources' codes; `make_code` is codes._r, called as make_code(source name, *row)."""
    return {code: make_code(s.name, *row) for s in sources for code, row in s.codes.items()}
