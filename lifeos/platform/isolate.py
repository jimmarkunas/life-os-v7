"""LIFE-689: one bad item never fails a stage: run a list of items, keep the successes, record each failure under a fixed code, and decide when the stage itself has failed.

The rule (docs/platform/stage-isolation.md): a stage fails only when EVERY item failed or the stage itself could not do its job (list its items, write its result). Items that succeeded
are always kept. A failed item is recorded under a fixed code, never under an exception's text. A stage that adopts the helper adds `items`, `item_failed` and `item_codes` to its counts
ONLY when at least one item failed, so a run where every item succeeds prints exactly the counts it printed before; `stage_failed` marks a failure of the stage itself.
`lifeos.run.main` applies the rule to the stages that report these keys; every other stage keeps its exit behavior."""
import re
from dataclasses import dataclass, field

CODE = re.compile(r"[A-Z][A-Z0-9_]{2,60}")
DEFAULT_CODE = "ITEM_FAILED"


def fixed_code(error, default=DEFAULT_CODE):
    """The fixed code an error carries, never its text: a `code` attribute or a message that is exactly an upper-case code (the platform's own errors); anything else is `default`."""
    for candidate in (getattr(error, "code", None), str(error)):
        if isinstance(candidate, str) and CODE.fullmatch(candidate):
            return candidate
    return default


@dataclass
class Isolated:
    """What a list of items came to: `results` are (item, value) pairs in order, `failures` are (item, fixed code) pairs in order."""
    total: int = 0
    results: list = field(default_factory=list)
    failures: list = field(default_factory=list)

    @property
    def codes(self):
        out = {}
        for _, code in self.failures:
            out[code] = out.get(code, 0) + 1
        return out

    @property
    def all_failed(self):
        return self.total > 0 and len(self.failures) == self.total


def run_items(items, work, code_of=fixed_code, reraise=()):
    """Run `work(item)` for every item. An error in one item is recorded under `code_of(error)` and the next item still runs; it never raises for a single item.
    Only `Exception` is caught (an interrupt or exit still stops the run) and `reraise` names errors that must stop the stage (a deadline, for example)."""
    out = Isolated()
    for item in items:
        out.total += 1
        try:
            out.results.append((item, work(item)))
        except reraise:
            raise
        except Exception as error:               # noqa: BLE001 - one item's error of any kind is that item's failure, recorded by fixed code only
            out.failures.append((item, code_of(error)))
    return out


def record(counts, isolated):
    """Add the item outcome to a stage's counts: nothing when every item succeeded (the counts stay exactly as before), else `items`, `item_failed` and `item_codes`. -> counts"""
    if isolated.failures:
        counts["items"], counts["item_failed"], counts["item_codes"] = isolated.total, len(isolated.failures), isolated.codes
    return counts


def stage_fails(counts):
    """True when the stage itself failed (`stage_failed`, or every item failed), False when some items failed and the stage still did its job, None when the counts carry no item outcome:
    the caller then keeps the stage's own rule."""
    if counts.get("stage_failed"):
        return True
    if "item_failed" in counts and isinstance(counts.get("items"), int):
        return counts["item_failed"] >= counts["items"]
    return None
