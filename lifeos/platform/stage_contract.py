"""A stage declared through a contract: inputs, guaranteed output counts, dry-run and live behaviour.

Called like a stage function, `contract(limit, live, **kw)`: it checks the returned counts are a dict carrying every
declared output key, else fails with STAGE_CONTRACT_OUTPUT_MISSING.
"""

from dataclasses import dataclass
from typing import Any, Callable


class ContractError(RuntimeError):
    """The message is always a fixed code."""


@dataclass(frozen=True)
class Contract:
    name: str
    run: Callable[..., Any]
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    dry_run: str
    write: str

    def __post_init__(self) -> None:
        if not (self.name and self.inputs and self.outputs and self.dry_run.strip() and self.write.strip()):
            raise ContractError("STAGE_CONTRACT_INCOMPLETE")

    def __call__(self, limit: int, live: bool, **keywords: Any) -> Any:
        counts = self.run(limit, live, **keywords)
        if not isinstance(counts, dict) or any(key not in counts for key in self.outputs):
            raise ContractError("STAGE_CONTRACT_OUTPUT_MISSING")
        return counts

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "dry_run": self.dry_run,
            "write": self.write,
        }
