"""The outcome-code registry: the one checked-in definition of every fixed code the pipeline can emit.

Each package declares its codes in lifeos/<package>/codes.json (plus reasons.json for stored reasons and
not_codes.json for configuration names); this module collects them. Each code has an owning stage, a kind (CLASSES), a
retry-budget flag, a meaning, a next action and a severity (SEVERITIES). Config names that look like codes belong in
NOT_CODES; tests/contracts/test_codes.py fails on a code-shaped string that is neither.
"""

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
    klass: str  # ok | state (a status word) | terminal (final for the job) | retry | blocked (a person or a limit must act) | degraded (part failed) | bug (off contract)
    attempt: bool  # counts against the job's retry budget
    meaning: str
    action: str
    severity: str = "none"
    public: bool = False  # reasons only: may print in a public log as itself (alertstate.REASONS is built from these); any other stored reason prints as "other"


def _r(stage, klass, attempt, meaning, action, severity="none", public=False):
    return Code(stage, klass, attempt, meaning, action, severity, public)


def declared(root=Path(__file__).resolve().parents[1]):
    """-> (codes, reasons, not_codes) collected from every lifeos/<package>/codes.json ({CODE: [stage, class, attempt,
    meaning, action[, severity]]}), reasons.json ({word: [stage, class, attempt, meaning, action, public]}) and
    not_codes.json (a list of names). A code is declared in exactly one file.
    """

    def read(name):
        return [json.loads(path.read_text()) for path in sorted(root.glob("*/" + name))]

    return (
        {code: _r(*row) for data in read("codes.json") for code, row in data.items()},
        {word: _r(*row[:5], public=row[5]) for data in read("reasons.json") for word, row in data.items()},
        {name for data in read("not_codes.json") for name in data},
    )


CODES, REASONS, _NAMES = declared()
for _reason in (
    "NOT_ALLOWLISTED",
    "NONCE_REPLAYED",
    "COUNTER_UNAVAILABLE",
    "DAILY_LIMIT",
    "LIMIT_UNCONFIGURED",
    "REAL_DISABLED",
    "BRIEF_MISSING",
    "BRIEF_UNSAFE",
    "BRIEF_HASH_MISMATCH",
    "BRIEF_INVALID",
    "PROFILE_MISMATCH",
    "PURGE_FAILED",
    "USAGE_UNAVAILABLE",
    "QUOTA_RESERVE",
    "QUOTA_UNCONFIGURED",
    "METER_UNAVAILABLE",
    "AUTH_UNAVAILABLE",
):
    if "FIT_EDGE_REFUSED_" + _reason not in CODES:  # the f-string forms Fit builds from Oracle's refusal reasons
        _base = CODES[_reason]
        CODES["FIT_EDGE_REFUSED_" + _reason] = _r(
            _base.stage,
            _base.klass,
            _base.attempt,
            "Oracle refused: " + _base.meaning[0].lower() + _base.meaning[1:],
            _base.action,
            _base.severity,
        )

CODES.update(registry.code_rows(_r))

# Every name in the environment-variable registry is a configuration name, never a result code.
from lifeos.platform import credentials  # noqa: E402 - placed beside the list it extends

NOT_CODES = (
    frozenset(_NAMES)
    | frozenset(row["name"] for row in credentials.config()["names"])
    | frozenset(credentials.config()["provided"]["names"])
)


def public_reasons():
    return frozenset(code for code, entry in REASONS.items() if entry.public)


_SQL_REASON = re.compile(r"unresolved_reason\s*=\s*'([^'%]+)'")
_SQL_REASON_IN = re.compile(r"unresolved_reason\s+(?:NOT\s+)?IN\s*\(([^)]*)\)")


def sql_reasons(source):
    """The literal reasons one module's SQL writes or tests on unresolved_reason: {reason}. A LIKE pattern is not a
    reason.
    """
    found = set(_SQL_REASON.findall(source))
    for group in _SQL_REASON_IN.findall(source):
        found.update(re.findall(r"'([^'%]+)'", group))
    return found


def candidates(source):
    tree = ast.parse(source)
    parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and CANDIDATE.match(node.value)
            and not isinstance(parent.get(node), ast.Expr)
        ):
            found.setdefault(node.value, node.lineno)
    return found


def lookup(code):
    return CODES.get(code)
