"""LIFE-47 P1 slice A: the server-side contract behind the Close Opportunity page. Pure: no I/O, clock or credential; the future host supplies the authenticated user, `now` and the live stage.
A link carries signed claims only (the exact pursuit, the stage it was seen in, an expiry), never a credential; `opened` (GET) writes nothing; `closing` (POST) yields the D194/D195 request `decision` validates and writes."""
import hashlib
import hmac
import json

from . import decision as D

REASONS = ("Rejected", "Withdrew", "Position filled", "Declined offer", "Hired", "Other")      # the six the approved page offers
SUPPORTED = ("Rejected",)       # the only closure the canonical record carries (Hiring Decision: Rejected | Passed): the rest fail closed until Jim approves a mapping and schema


def _mac(secret, *parts):
    return hmac.new(secret, "\x1f".join(parts).encode(), hashlib.sha256).hexdigest()


def link(secret, job, now):
    """job: id, company, role, req, stage -> (body, mac) for the Close link."""
    body = json.dumps({**job, "exp": int(now) + 3 * 86400}, sort_keys=True, separators=(",", ":"))
    return body, _mac(secret, "link", body)


def opened(secret, body, mac, now):
    if not hmac.compare_digest(str(mac).encode(), _mac(secret, "link", body).encode()):
        raise D.fail("LINK_INVALID")
    claims = json.loads(body)
    if claims["exp"] < now:
        raise D.fail("LINK_EXPIRED")
    return claims


def csrf(secret, user, body):
    return _mac(secret, "csrf", user, body)


def closing(secret, user, allowed, body, mac, form, stage, now):
    """stage: the pursuit's live stage, read server-side. -> the request for `decision`, or a fixed code and nothing written."""
    if user not in allowed:
        raise D.fail("FORBIDDEN")
    claims = opened(secret, body, mac, now)
    if not hmac.compare_digest(str(form.get("csrf")).encode(), csrf(secret, user, body).encode()):
        raise D.fail("CSRF")
    reason, notes = form.get("reason"), str(form.get("notes") or "").strip()
    if reason not in REASONS or form.get("confirm") != "yes" or len(notes) > D.REASON_MAX or (reason == "Other" and not notes):
        raise D.fail("REQUEST_INVALID")
    if reason not in SUPPORTED:
        raise D.fail("REASON_UNSUPPORTED")
    if stage != claims["stage"]:
        raise D.fail("STALE_STATE")
    return {"action": D.ACTION, "confirmed": True, **{k: claims[k] for k in ("id", "company", "role")}, "reason": notes or reason, "op": _mac(secret, "op", body, reason, notes)[:32]}
