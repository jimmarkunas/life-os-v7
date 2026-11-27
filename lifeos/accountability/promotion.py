"""The v2 quarterly promotion check (off by default, not wired; V2_PROMOTION is the kill switch).

On the first run at or after Fri Jan 1 2027, 8:00 AM America/Chicago, `run_if_due` evaluates the checks: a pass
sets V2_OFFICIAL through an injected setter; a fail keeps legacy official and sends ONE Agent Inbox alert through
an injected alerter. Any missing, ambiguous, duplicate or wrong-date datum is a hard fail, never excused (frozen
quarter list 100% counted, no key with two goals, completion dates from the first verified Done transition, v1
figures unchanged, manual and automatic closures distinct). `reconcile` allows at most ONE unexplained discrepancy
per board per quarter."""
from datetime import datetime
from zoneinfo import ZoneInfo

CENTRAL = ZoneInfo("America/Chicago")
FIRST_RUN = (2027, 1, 1)
FIRST_RUN_HOUR = 8
BOARDS = ("JFM", "MEJ")
ALERT_SUBJECT = "V2 promotion check failed: legacy stays official"
EXPLANATION_FIELDS = ("by", "what", "source")
MAX_UNEXPLAINED = 1

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

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def boundary(year=FIRST_RUN[0], month=FIRST_RUN[1], day=FIRST_RUN[2]):
    return datetime(year, month, day, FIRST_RUN_HOUR, 0, tzinfo=CENTRAL)


def due(now):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise PromotionError(INPUT_INVALID)
    return now >= boundary()


def reconcile(discrepancies):
    """`discrepancies`: [{"key", "explanation": {"by", "what", "source"} or None}] -> {"explained": [keys],
    "unexplained": [keys]}. An explanation needs all three fields non-blank."""
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
        if isinstance(goals, dict):
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
    """-> {"verdict": "PASS"|"FAIL", "reasons": [codes], "quarter", "boards": {name: {"hard": [...], "unexplained": n,
    "explained": n}}}. PASS needs both boards with no hard failure and at most one unexplained discrepancy each."""
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
    failing = failing_boards(verdict)
    return {"subject": ALERT_SUBJECT, "urgency": "Today", "workspace": "Personal", "key": f"v2-promotion-{verdict.get('quarter') or 'unknown'}",
            "details": ("failing board: " + ", ".join(failing) + ". " if failing else "") + "reasons: " + ", ".join(verdict.get("reasons") or []) + ". Both boards stay on legacy; v2 stays preview."}


def failing_boards(verdict):
    """Boards that made a check fail. Promotion is one unit: either failing keeps both on legacy."""
    boards = (verdict or {}).get("boards") or {}
    return [name for name in BOARDS if name in boards and (boards[name]["hard"] or boards[name]["unexplained"] > MAX_UNEXPLAINED)]


def refusing_setter():
    raise PromotionError(SETTER_UNAVAILABLE)


def run_if_due(now, switches, readers, setters, alerter):
    """-> {"status": ..., ...}, fixed words and codes only. Order: kill switch off -> not_enabled (nothing read,
    written or sent); already official -> already_official; before the first run instant -> not_due; else read,
    evaluate, set or alert."""
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
    except Exception:  # noqa: BLE001 - an unreadable input is a hard fail
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
    """Alerter over the Agent Inbox delivery, which the CALLER passes in (this package never imports the inbox). A
    delivery that is not ok raises, so run_if_due records it as not sent."""
    if deliver is None:
        raise PromotionError(ALERT_FAILED)

    def send(item):
        out = deliver({**item, "from": "v7-promotion"})
        if not (out or {}).get("ok"):
            raise PromotionError(ALERT_FAILED)
    return send
