"""LIFE-618: the one list of job sources, read from source_registry.json. Adding a source = THREE files: its module, its test, and one object in that file (LIFE-588). Nothing else is edited by hand.

From that list, at import time:
  * `lifeos/run.py` adds the stage (`python -m lifeos.run <name>`), so the hand-kept STAGES line is not needed;
  * `lifeos/jobs/tick.py` adds the source's Jobs-tick step (budget, limit, bulk, group, providers) after the step `after` names;
  * `lifeos/platform/codes.py` registers the source's own outcome codes;
  * `lifeos/run.py` makes a run whose counts carry `failed > 0` exit 1 with the row's `partial_failure` code (a code the row's `codes`, or a package's codes.json, registers).
The code map (`python -m lifeos.sources.devtools.codemap build`) is generated from STAGES and is not committed, so it follows without a hand edit; a new file under lifeos/ needs no row in docs/test-index.json (the selector falls back to the mirror test folder).
`tests/contracts/test_source_registry.py` imports every entry, and fails by name for a stage under lifeos/sources/ that is neither registered nor on its short "not yet migrated" list.
Stays by hand, on purpose: a manual-dispatch input or step in a workflow file (protected-file approval; whether a source can be run by hand is a per-source choice), and one row in its package's config.txt for each new setting (environment name) it reads.
It is data plus four small functions and imports nothing from lifeos (D17: platform knows nothing about jobs or sources; the modules are named by string, never imported here).
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
    args: dict = field(default_factory=dict)    # fixed keyword arguments for the stage function (e.g. the Scale-Up lane)
    floor: int = 0                              # raise a smaller --limit to this (the same meaning as run.lazy)
    tick: dict | None = None                    # run inside the Jobs tick: the keyword arguments of tick.Step (budget, limit, bulk, group, providers)
    after: str = ""                             # the tick step it follows
    codes: dict = field(default_factory=dict)   # its own outcome codes: {CODE: (class, counts as an attempt, meaning, action[, severity])}
    partial_failure: str = ""                   # a run whose counts carry failed > 0 exits 1 with this code (run.py prints "<STAGE> FAILED: <code>")


def load(path=FILE):
    return tuple(Source(**{**entry, "codes": {code: tuple(row) for code, row in (entry.get("codes") or {}).items()},
                           "tick": {**entry["tick"], **({"providers": tuple(entry["tick"]["providers"])} if "providers" in entry["tick"] else {})} if entry.get("tick") else None})
                 for entry in json.loads(Path(path).read_text())["sources"])


SOURCES = load()


def stages(lazy, sources=SOURCES):
    """-> {name: stage function} for the registered sources; `lazy` is lifeos.run.lazy (passed in so this file imports nothing)."""
    return {s.name: lazy(s.module, s.function, floor=s.floor, **s.args) for s in sources}


def with_tick_steps(base, make_step, sources=SOURCES):
    """-> the tick plan `base` with each registered source's step inserted after the step it names. `make_step` is tick.Step. An `after` that is not in the plan, or that another source already follows, raises, so a typo or a clash is never silent."""
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
    """-> {CODE: Code} for the registered sources' codes; `make_code` is codes._r, called as make_code(source name, *row)."""
    return {code: make_code(s.name, *row) for s in sources for code, row in s.codes.items()}


def partial_failures(sources=SOURCES):
    """-> {stage name: code} for the registered sources that exit 1 when their counts carry failed > 0 (merged into run.PARTIAL_FAILURE)."""
    return {s.name: s.partial_failure for s in sources if s.partial_failure}
