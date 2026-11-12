"""LIFE-589: a stage declared through a contract: its name, what it reads (inputs), the counts it always returns (outputs), what a dry run does and what a live run changes.

    CONTRACT = stage_contract.Contract(name="sponsors", run=run, inputs=(...), outputs=("status", "sponsors", "saved"), dry_run="...", write="...")

A contract is callable exactly like a stage function, `contract(limit, live, **kw)`, so lifeos/run.py's `lazy(module, "CONTRACT")` and the source registry (`"function": "CONTRACT"`) use it with no other change.
Calling it runs the stage and then checks the one thing that can be checked for any stage: the returned counts are a dict that carries every declared output key. A stage that breaks its own
declaration fails with the fixed code STAGE_CONTRACT_OUTPUT_MISSING instead of handing a downstream reader a count that is not there. The prose fields are for the reader (and `describe()`); they must not be empty.
Imports nothing from lifeos (platform rule)."""
from dataclasses import dataclass
from typing import Any, Callable


class ContractError(RuntimeError):
    """Message is always a fixed code."""


@dataclass(frozen=True)
class Contract:
    name: str
    run: Callable[..., Any]                 # the stage function: (limit, live, **keywords) -> counts dict
    inputs: tuple[str, ...]                 # what it reads, in plain words (a page, a table, an optional switch)
    outputs: tuple[str, ...]                # keys that every returned counts dict carries
    dry_run: str                            # what a dry run does
    write: str                              # what a live run changes

    def __post_init__(self) -> None:
        if not (self.name and self.inputs and self.outputs and self.dry_run.strip() and self.write.strip()):
            raise ContractError("STAGE_CONTRACT_INCOMPLETE")

    def __call__(self, limit: int, live: bool, **keywords: Any) -> Any:
        counts = self.run(limit, live, **keywords)
        if not isinstance(counts, dict) or any(key not in counts for key in self.outputs):
            raise ContractError("STAGE_CONTRACT_OUTPUT_MISSING")
        return counts

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "inputs": list(self.inputs), "outputs": list(self.outputs), "dry_run": self.dry_run, "write": self.write}
