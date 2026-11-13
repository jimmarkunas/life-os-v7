"""Closes a Hiring Pipeline row on a known rejection mail that names the row's exact role and company (Jim, Oct 24 2026).

A row closes only when the outcome mail (D190) is a REJECTION naming its exact role and company, the row was computed this run from complete, fresh reads, and no other role Jim pursued there shares its words.
A company-only match ("Possible rejection") stays a line Jim confirms. The close is the Close screen's own checked write through `decision.route`: the one Ledger row gets `Hiring Decision = Rejected` (re-read, written,
read back; LIFE-353: Ledger rows only). Anything the Close screen would refuse is refused here, counted by fixed code, and the row stays as it was. A Reopen undoes it.
Runs only when the hourly run is live AND the HIRING_AUTO_CLOSE switch is on (lifeos/platform/switches.json; a repository variable "off" is the kill switch); off, the rows that would close are only counted.
A closed row stays on the pipeline for SHOW_HOURS, then drops off. Counts and fixed codes only."""
# Detail moved out of the docstring (LIFE-595):
# Jim's request, Oct 24 2026: "Should these auto-close on known rejection and persist for 24 hours with a closed/rejected message?"
# Nothing is deleted, sent or marked in the mailbox. A closed row shows as "Closed — rejection email received <day> · auto-closed <day>" for SHOW_HOURS.
import hashlib
from datetime import datetime, timedelta

from lifeos.platform import names
from lifeos.platform.notion_client import NotionError
from lifeos.platform import switches
from . import decision, models as M
from .outcomes import REJECTED as REJECTION

SWITCH, SHOW_HOURS = "HIRING_AUTO_CLOSE", 24


def due(rows):
    """The rows a known rejection closes: a REJECTION outcome whose mail names this exact role (outcomes.exact_role), on a row computed this run.
    Never a company-only match, an offer, a role that only shares words with the mail, or a row carried from an earlier run."""
    return [r for r in rows or [] if (r.get("outcome") or {}).get("kind") == REJECTION and r["outcome"].get("match") == "role" and r["outcome"].get("exact") is True
            and not r.get("carried")]


def unique_role(row, pursuits):
    """No other role Jim pursued at this company (Ledger pursuit rows, any decision) contains this role's words: the mail may be about that one."""
    words = set(names.tokens(row["role"]))
    return not any(names.same_company(o["company"], row["company"]) and not M.same(o["company"], o["role"], row["company"], row["role"]) and words <= set(names.tokens(o["role"]))
                   for o in pursuits)


def request(row, target_id, day):
    """The Close screen's own confirmed request shape. The operation id is fixed by the row and the message's received time, so a rerun is a no-op, never a second write."""
    op = "auto-" + hashlib.sha256(f"{row['key']}|{row['outcome']['received']}".encode()).hexdigest()[:24]
    return {"action": decision.ACTION, "confirmed": True, "id": target_id, "company": row["company"], "role": row["role"], "outcome": decision.REJECTED, "op": op,
            "reason": f"Auto-closed: rejection email received {day} (Jim's standing rule, Oct 24)"}


def run(rows, environ, live, now, ledger_client, day_of):
    """-> {"due", "closed": [shown entries], "refused": {code: n}, "switch": bool}. `day_of(iso)` formats a received time as "Sep 17". The Ledger's pursuit rows are read once, only when a row is due."""
    out = {"due": 0, "closed": [], "refused": {}, "switch": bool(live) and switches.state(SWITCH, environ)}
    pursuits = None
    for row in due(rows):
        out["due"] += 1
        if not out["switch"]:
            continue
        day = day_of(row["outcome"]["received"])
        try:
            if pursuits is None:
                pursuits = decision.rows(decision.ledger_pages(ledger_client))
            if not unique_role(row, pursuits):
                raise decision.fail("ROLE_NOT_UNIQUE")
            target = decision.route(pursuits, {"company": row["company"], "role": row["role"], "action": decision.ACTION})
            done = decision.confirm_rejected(ledger_client, request(row, decision._page_id(target.get("id")), day), True, now)
            if done.get("verified") is not True or done.get("status") not in ("recorded", "already_recorded"):
                raise decision.fail("NOT_VERIFIED")
            out["closed"].append({"key": row["key"], "label": M.label(row["company"], row["role"]), "received": row["outcome"]["received"], "at": now.isoformat()})
        except Exception as error:                                       # noqa: BLE001 - one row's failure never loses the closes already made; codes only, never text
            code = str(error)[:40] if isinstance(error, NotionError) else "UNEXPECTED"
            out["refused"][code] = out["refused"].get(code, 0) + 1
    return out


def shown(prior, closed, now, live_keys=()):
    """The closed rows still inside SHOW_HOURS: this run's, plus the prior run's that have not aged out (one entry per row key, the newest close wins).
    A row that is back in the live table (reopened) is never shown twice."""
    keep = {}
    for entry in list((prior or {}).get("auto_closed") or []) + list(closed):
        if (isinstance(entry, dict) and isinstance(entry.get("at"), str) and entry.get("key") not in set(live_keys)
                and datetime.fromisoformat(entry["at"]) > now - timedelta(hours=SHOW_HOURS)):
            keep[entry["key"]] = entry
    return sorted(keep.values(), key=lambda e: e["at"])


def cells(entries, day_of):
    """One table row per shown close, in the table's four cells (Company — Role · Stage · Next step · Due · Notes)."""
    return [[(e["label"], None), ("Closed", None), (f"Closed — rejection email received {day_of(e['received'])} · auto-closed {day_of(e['at'])}", None), ("Retired", None)] for e in entries]
