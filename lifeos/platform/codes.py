"""The outcome-code registry (LIFE-72): one checked-in definition of every fixed code the pipeline can emit. LIFE-644: each package declares its own in lifeos/<package>/codes.json (plus reasons.json for stored reasons and not_codes.json for configuration names); this file only collects them.
A code is a fixed UPPER_SNAKE word that names a result: a refusal, a failure, a skip, a normal stop or a state. For each one the registry says which stage owns it,
what kind of result it is (CLASSES), whether it counts as an attempt against the job's retry budget, what it means in plain words, what to do next, and whether it
may ever alert (SEVERITIES: none, info, page). tests/contracts/test_codes.py fails when the code emits a code-shaped string that is neither here nor in NOT_CODES, so a new code cannot appear without a definition. No code pages yet: turning any severity up is Jim's call (LIFE-71, alerts are suspended).
Config and environment names that look like codes are listed in NOT_CODES."""
import ast
import json
import re
from pathlib import Path
from typing import NamedTuple

from lifeos.platform import source_registry as registry

CLASSES = ("ok", "state", "terminal", "retry", "blocked", "degraded", "bug")
SEVERITIES = ("none", "info", "page")
CANDIDATE = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)+$")


class Code(NamedTuple):
    stage: str
    klass: str          # ok: a normal success | state: a status word, not a result | terminal: final for this job | retry: tried again later | blocked: a person or a limit must act | degraded: ran, but part failed | bug: off contract, a code defect
    attempt: bool       # counts as an attempt against the job's retry budget
    meaning: str
    action: str
    severity: str = "none"
    public: bool = False   # reasons only: may be printed in a public log as itself (alertstate.REASONS is built from these); every other stored reason prints as "other"


def _r(stage, klass, attempt, meaning, action, severity="none", public=False):
    return Code(stage, klass, attempt, meaning, action, severity, public)


def declared(root=Path(__file__).resolve().parents[1]):
    """-> (codes, reasons, not_codes) collected from every lifeos/<package>/codes.json (a flat {CODE: [stage, class, attempt, meaning, action[, severity]]}), reasons.json
    ({word: [stage, class, attempt, meaning, action, public]}) and not_codes.json (a list of configuration names). A code is declared in exactly one file (tests/contracts/test_codes.py)."""
    def read(name):
        return [json.loads(path.read_text()) for path in sorted(root.glob("*/" + name))]
    return ({code: _r(*row) for data in read("codes.json") for code, row in data.items()}, {word: _r(*row[:5], public=row[5]) for data in read("reasons.json") for word, row in data.items()},
            {name for data in read("not_codes.json") for name in data})


CODES, REASONS, _NAMES = declared()
for _reason in ("NOT_ALLOWLISTED", "NONCE_REPLAYED", "COUNTER_UNAVAILABLE", "DAILY_LIMIT", "LIMIT_UNCONFIGURED", "REAL_DISABLED", "BRIEF_MISSING", "BRIEF_UNSAFE",
                "BRIEF_HASH_MISMATCH", "BRIEF_INVALID", "PROFILE_MISMATCH", "PURGE_FAILED", "USAGE_UNAVAILABLE", "QUOTA_RESERVE", "QUOTA_UNCONFIGURED",
                "METER_UNAVAILABLE", "AUTH_UNAVAILABLE"):
    if "FIT_EDGE_REFUSED_" + _reason not in CODES:                  # the f-string forms Fit builds from Oracle's refusal reasons; same meaning as the bare reason
        _base = CODES[_reason]
        CODES["FIT_EDGE_REFUSED_" + _reason] = _r(_base.stage, _base.klass, _base.attempt, "Oracle refused: " + _base.meaning[0].lower() + _base.meaning[1:], _base.action, _base.severity)

CODES.update(registry.code_rows(_r))      # LIFE-618: the registered job sources' own codes (lifeos/platform/source_registry.json)

# LIFE-560: every name in the environment-variable registry (credentials.config()) is a configuration name, never a result code.
from lifeos.platform import credentials    # noqa: E402 - placed beside the list it extends
NOT_CODES = frozenset(_NAMES) | frozenset(row["name"] for row in credentials.config()["names"]) | frozenset(credentials.config()["provided"]["names"])


def public_reasons():
    """The reasons a counts-only public log may print as themselves, as the allowlist alertstate.REASONS uses."""
    return frozenset(code for code, entry in REASONS.items() if entry.public)


_SQL_REASON = re.compile(r"unresolved_reason\s*=\s*'([^'%]+)'")
_SQL_REASON_IN = re.compile(r"unresolved_reason\s+(?:NOT\s+)?IN\s*\(([^)]*)\)")


def sql_reasons(source):
    """The literal reasons one module's SQL writes or tests on unresolved_reason: {reason}. A LIKE pattern is not a reason."""
    found = set(_SQL_REASON.findall(source))
    for group in _SQL_REASON_IN.findall(source):
        found.update(re.findall(r"'([^'%]+)'", group))
    return found


def candidates(source):
    """The fixed code-shaped strings in one module's source (docstrings excluded): {code: first line number}."""
    tree = ast.parse(source)
    parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and CANDIDATE.match(node.value) and not isinstance(parent.get(node), ast.Expr):
            found.setdefault(node.value, node.lineno)
    return found


def lookup(code):
    """-> the Code for a registered code, else None."""
    return CODES.get(code)
