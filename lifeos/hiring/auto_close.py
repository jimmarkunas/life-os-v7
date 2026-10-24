"""Auto-close on a known rejection (Jim, Oct 24 2026): "Should these auto-close on known rejection and persist for 24 hours with a closed/rejected message?"

A row closes itself only when the outcome mail (D190) is a REJECTION that names the row's exact role as well as its company, the row was computed this run from complete,
fresh reads, and no other role Jim pursued at that company shares its words. A company-only match ("Possible rejection") stays a line Jim confirms. The close is the same checked write the private Close screen makes, through `decision.route`:
the one Ledger application gets `Hiring Decision = Rejected` (re-read, written, read back), else the one Active Notes page moves to Retired Opportunities. Anything the Close screen would
refuse is refused here, counted by fixed code, and the row stays as it was. Nothing is deleted, sent or marked in the mailbox, and a Reopen undoes it.

It runs only when the hourly run is live AND the repository variable HIRING_AUTO_CLOSE is "on" (Jim's switch). Off, the rows that would close are only counted.
A closed row stays on the Hiring Pipeline for SHOW_HOURS as "Closed — rejection email received <day> · auto-closed <day>", then drops off. Counts and fixed codes only are logged."""
import hashlib
from datetime import datetime, timedelta

from lifeos.platform import names
from lifeos.platform.notion_client import NotionError
from . import decision, models as M
from .outcomes import REJECTED as REJECTION

SWITCH, SHOW_HOURS = "HIRING_AUTO_CLOSE", 24


def due(rows):
    """The rows a known rejection closes: a REJECTION outcome whose mail names this exact role (outcomes.exact_role), on a row computed this run.
    Never a company-only match, an offer, a role that only shares words with the mail, or a row carried from an earlier run."""
    return [r for r in rows or [] if (r.get("outcome") or {}).get("kind") == REJECTION and r["outcome"].get("match") == "role" and r["outcome"].get("exact") is True
            and not r.get("carried")]


def unique_role(row, applied, parents):
    """No other role Jim pursued at this company (Ledger rows, any decision, and Notes pages, retired included) contains this role's words: the mail may be about that one."""
    words = set(names.tokens(row["role"]))
    return not any(names.same_company(o["company"], row["company"]) and not M.same(o["company"], o["role"], row["company"], row["role"]) and words <= set(names.tokens(o["role"]))
                   for o in list(applied) + list(parents))


def request(row, target_id, day):
    """The Close screen's own confirmed request shape. The operation id is fixed by the row and the message's received time, so a rerun is a no-op, never a second write."""
    op = "auto-" + hashlib.sha256(f"{row['key']}|{row['outcome']['received']}".encode()).hexdigest()[:24]
    return {"action": decision.ACTION, "confirmed": True, "id": target_id, "company": row["company"], "role": row["role"], "outcome": decision.REJECTED, "op": op,
            "reason": f"Auto-closed: rejection email received {day} (Jim's standing rule, Oct 24)"}


def run(rows, applied, parents, environ, live, now, ledger_client, pipe, root, read_parents, day_of):
    """-> {"due", "closed": [shown entries], "refused": {code: n}, "switch": bool}. `day_of(iso)` formats a received time as "Sep 17"; `read_parents()` re-reads the pages for move_page."""
    out = {"due": 0, "closed": [], "refused": {}, "switch": bool(live) and (environ.get(SWITCH) or "").strip() == "on"}
    for row in due(rows):
        out["due"] += 1
        if not out["switch"]:
            continue
        day = day_of(row["outcome"]["received"])
        try:
            if not unique_role(row, applied, parents):
                raise decision.fail("ROLE_NOT_UNIQUE")
            kind, target = decision.route(applied, parents, {"company": row["company"], "role": row["role"], "action": decision.ACTION})
            want = request(row, decision._page_id(target.get("id")), day)
            if kind == "notes" and decision.unapplied_matches(ledger_client, decision.parse(want)):
                raise decision.fail("LEDGER_UNAPPLIED")                  # a Ledger row for this role not marked Applied may be the application: Jim decides, as on the Close screen
            done = decision.confirm_rejected(ledger_client, want, True, now) if kind == "ledger" else decision.move_page(pipe, root, want, True, now, read_parents)
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
