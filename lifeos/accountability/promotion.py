"""LIFE-918 AC6, AC11, AC12: the MEJ/JFM v2 quarterly promotion check. NOT WIRED and OFF BY DEFAULT: nothing calls `run_if_due` yet, and the kill switch V2_PROMOTION is off until Jim's activation.

On the first reconciliation run at or after Fri Jan 1 2027, 8:00 AM America/Chicago (a tz-aware compare, so daylight time cannot shift it), `run_if_due` evaluates the promotion checks. A pass sets the v2-official row (V2_OFFICIAL = on) through
an injected setter; a fail keeps legacy official and sends ONE Agent Inbox alert through an injected alerter, under the same dedupe key every day so the existing inbox send updates one row. V2_PROMOTION off (the default) stops everything:
no read, no write, no alert. Names, counts and fixed codes only; never an issue summary.

AC11 (any missing, ambiguous, duplicate or wrong-date datum is a hard fail, and no tolerance ever excuses it):
- membership: the frozen quarter list exists (the shipped manifest is empty, so missing membership fails) and 100% of it is counted in v2;
- no key is attributed to two goals;
- every Done completion date is the first verified transition into Done from the Jira changelog, and present;
- historical parity: the v1 historical figures are unchanged;
- manual and automatic recurring closures are kept distinct (no overlap, and manual + automatic = the reported total).
AC12 (the reconciliation method, `reconcile`): each Task discrepancy between the legacy and v2 figures needs an explanation record (who explained it, what, a source reference). A discrepancy without a complete record is unexplained.
At most ONE unexplained discrepancy per board per quarter is tolerated; two or more block promotion. The tolerance applies only to AC12; it never turns an AC11 failure into a pass."""
from datetime import datetime
from zoneinfo import ZoneInfo

CENTRAL = ZoneInfo("America/Chicago")
FIRST_RUN = (2027, 1, 1)                      # Fri Jan 1 2027, 8:00 AM America/Chicago
FIRST_RUN_HOUR = 8
BOARDS = ("JFM", "MEJ")
ALERT_SUBJECT = "V2 promotion check failed: legacy stays official"
EXPLANATION_FIELDS = ("by", "what", "source")
MAX_UNEXPLAINED = 1                           # per board per quarter (AC12)

MEMBERSHIP_MISSING = "ACCOUNTABILITY_PROMOTION_MEMBERSHIP_MISSING"
MEMBERSHIP_INCOMPLETE = "ACCOUNTABILITY_PROMOTION_MEMBERSHIP_INCOMPLETE"
DUPLICATE_ATTRIBUTION = "ACCOUNTABILITY_PROMOTION_DUPLICATE_ATTRIBUTION"
COMPLETION_DATE_UNVERIFIED = "ACCOUNTABILITY_PROMOTION_COMPLETION_DATE_UNVERIFIED"
HISTORY_DRIFT = "ACCOUNTABILITY_PROMOTION_HISTORY_DRIFT"
RECURRING_NOT_DISTINCT = "ACCOUNTABILITY_PROMOTION_RECURRING_NOT_DISTINCT"
TOLERANCE_EXCEEDED = "ACCOUNTABILITY_PROMOTION_TOLERANCE_EXCEEDED"
INPUT_INVALID = "ACCOUNTABILITY_PROMOTION_INPUT_INVALID"
SETTER_UNAVAILABLE = "ACCOUNTABILITY_PROMOTION_SETTER_UNAVAILABLE"
ALERT_FAILED = "ACCOUNTABILITY_PROMOTION_ALERT_FAILED"


class PromotionError(Exception):
    """A fixed code, never a message with content."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def boundary(year=FIRST_RUN[0], month=FIRST_RUN[1], day=FIRST_RUN[2]):
    """The 8:00 AM America/Chicago instant of that date (daylight time is applied by the zone, never a fixed offset)."""
    return datetime(year, month, day, FIRST_RUN_HOUR, 0, tzinfo=CENTRAL)


def due(now):
    """True at or after the first run instant. A naive `now` is refused: a time without a zone cannot be compared safely."""
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise PromotionError(INPUT_INVALID)
    return now >= boundary()


def reconcile(discrepancies):
    """AC12 method. `discrepancies` is a list of {"key": issue key, "explanation": {"by", "what", "source"} or None}.
    -> {"explained": [keys], "unexplained": [keys]}. A record counts only when all three fields are present and non-blank; a repeated key is one discrepancy."""
    explained, unexplained, seen = [], [], set()
    for item in discrepancies or []:
        key = str((item or {}).get("key") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        record = (item or {}).get("explanation")
        complete = isinstance(record, dict) and all(isinstance(record.get(f), str) and record.get(f).strip() for f in EXPLANATION_FIELDS)
        (explained if complete else unexplained).append(key)
    return {"explained": sorted(explained), "unexplained": sorted(unexplained)}


def _board(name, data):
    """-> sorted list of AC11 hard-fail codes for one board, then the AC12 result. A malformed board is INPUT_INVALID (hard)."""
    if not isinstance(data, dict):
        return [INPUT_INVALID], None
    hard = set()
    frozen, counted = data.get("frozen"), data.get("counted")
    if not isinstance(frozen, list) or not frozen:
        hard.add(MEMBERSHIP_MISSING)
    elif not isinstance(counted, list) or not set(frozen) <= set(counted):
        hard.add(MEMBERSHIP_INCOMPLETE)
    if isinstance(counted, list):
        keys = [k for k in counted]
        if len(keys) != len(set(keys)):
            hard.add(DUPLICATE_ATTRIBUTION)
        goals = data.get("goals")
        if isinstance(goals, dict):                              # {issue key: [goal keys]}: one goal each
            if any(not isinstance(v, list) or len(set(v)) != 1 for v in goals.values()):
                hard.add(DUPLICATE_ATTRIBUTION)
    completions = data.get("completions")
    if not isinstance(completions, dict):
        hard.add(COMPLETION_DATE_UNVERIFIED)
    else:
        for item in completions.values():
            if not isinstance(item, dict) or item.get("source") != "changelog" or not item.get("date") or item.get("ambiguous"):
                hard.add(COMPLETION_DATE_UNVERIFIED)
    parity = data.get("history")
    if not isinstance(parity, dict) or "before" not in parity or "after" not in parity:
        hard.add(INPUT_INVALID)
    elif parity["before"] != parity["after"]:
        hard.add(HISTORY_DRIFT)
    recurring = data.get("recurring")
    if not isinstance(recurring, dict) or not all(isinstance(recurring.get(k), int) and recurring.get(k) >= 0 for k in ("manual", "auto", "total")):
        hard.add(RECURRING_NOT_DISTINCT)
    elif recurring["manual"] + recurring["auto"] != recurring["total"] or set(recurring.get("manual_keys") or []) & set(recurring.get("auto_keys") or []):
        hard.add(RECURRING_NOT_DISTINCT)
    result = reconcile(data.get("discrepancies"))
    return sorted(hard), result


def evaluate(inputs):
    """-> {"verdict": "PASS"|"FAIL", "reasons": [sorted fixed codes], "quarter", "boards": {name: {"hard": [...], "unexplained": n, "explained": n}}}.
    PASS needs both boards (JFM and MEJ) clean: no AC11 failure and at most one unexplained discrepancy per board (AC12). The AC12 tolerance never excuses an AC11 failure."""
    reasons, boards = set(), {}
    if not isinstance(inputs, dict) or not isinstance(inputs.get("boards"), dict) or not inputs.get("quarter"):
        return {"verdict": "FAIL", "reasons": [INPUT_INVALID], "quarter": str((inputs or {}).get("quarter") or "") if isinstance(inputs, dict) else "", "boards": {}}
    for name in BOARDS:
        hard, result = _board(name, inputs["boards"].get(name))
        unexplained = len(result["unexplained"]) if result else 0
        boards[name] = {"hard": hard, "unexplained": unexplained, "explained": len(result["explained"]) if result else 0}
        reasons.update(hard)
        if unexplained > MAX_UNEXPLAINED:
            reasons.add(TOLERANCE_EXCEEDED)
    return {"verdict": "FAIL" if reasons else "PASS", "reasons": sorted(reasons), "quarter": str(inputs["quarter"]), "boards": boards}


def alert_item(verdict):
    """The one Agent Inbox item for a failed check. The key is the same every day (it carries the quarter only), so the existing inbox send updates one row instead of adding more. Codes and counts only."""
    return {"subject": ALERT_SUBJECT, "urgency": "Today", "workspace": "Personal", "key": f"v2-promotion-{verdict.get('quarter') or 'unknown'}",
            "details": "reasons: " + ", ".join(verdict.get("reasons") or []) + ". Legacy stays official; v2 stays preview."}


def refusing_setter():
    """The setter used until a safe Runtime Configuration row writer exists: V7 has no writer of settings rows today (settings.py and runtime_config.py only read). It refuses with a fixed code, so a PASS never silently
    pretends to have promoted; the caller keeps legacy official and alerts."""
    raise PromotionError(SETTER_UNAVAILABLE)


def run_if_due(now, switches, readers, setters, alerter):
    """-> {"status": ..., ...} with fixed words and codes only.
    switches: {"V2_PROMOTION": bool, "V2_OFFICIAL": bool}, read by the caller from lifeos.accountability.switches. readers: a function returning the evaluation inputs. setters: a function that sets V2_OFFICIAL on.
    alerter: a function taking the one inbox item. Order: kill switch off -> not_enabled (nothing read, written or sent); already official -> already_official; before the first run instant -> not_due; else read, evaluate, then set or alert."""
    if not (switches or {}).get("V2_PROMOTION"):
        return {"status": "not_enabled"}
    if (switches or {}).get("V2_OFFICIAL"):
        return {"status": "already_official"}
    if not due(now):
        return {"status": "not_due"}
    try:
        verdict = evaluate(readers())
    except PromotionError as error:
        verdict = {"verdict": "FAIL", "reasons": [error.code], "quarter": "", "boards": {}}
    except Exception:                                                  # noqa: BLE001 - an unreadable input is a hard fail with a fixed code, never text
        verdict = {"verdict": "FAIL", "reasons": [INPUT_INVALID], "quarter": "", "boards": {}}
    if verdict["verdict"] == "PASS":
        try:
            setters()
        except PromotionError as error:
            verdict = {**verdict, "verdict": "FAIL", "reasons": [error.code]}
        except Exception:                                              # noqa: BLE001
            verdict = {**verdict, "verdict": "FAIL", "reasons": [SETTER_UNAVAILABLE]}
        else:
            return {"status": "promoted", "verdict": verdict}
    sent = True
    try:
        alerter(alert_item(verdict))
    except Exception:                                                  # noqa: BLE001 - a failed alert never turns a fail into a pass
        sent = False
    return {"status": "kept_legacy", "verdict": verdict, "alerted": sent, **({} if sent else {"alert_code": ALERT_FAILED})}


def inbox_alerter(deliver=None):
    """The alerter for `run_if_due`: the existing Agent Inbox delivery (lifeos.inbox.send_to_jim.deliver, which updates the row of the same key instead of adding one). A delivery that is not ok raises, so run_if_due records it as not sent."""
    if deliver is None:
        from lifeos.inbox import send_to_jim
        deliver = send_to_jim.deliver

    def send(item):
        out = deliver({**item, "from": "v7-promotion"})
        if not (out or {}).get("ok"):
            raise PromotionError(ALERT_FAILED)
    return send
