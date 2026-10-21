"""Facts about Jim's physical mail that no approved source supplies yet, entered by hand as repository variables (LIFE-95). Never in the repository text: they name a real mailbox and shipment.

`PHYSICAL_MAIL_TRACKING` is JSON: {"<vendor mail number>": {"bundle": "...", "carrier": "...", "service": "...", "number": "...", "url": "https://...", "delivered": "YYYY-MM-DD"}}. The card shows
an entry exactly as written and never infers a carrier or a delivery state; `delivered` is added only when Jim has seen the delivery. `MAIL_PORTAL_URL` is the mailbox page the card links to.
A malformed value is ignored (the piece then shows as awaiting tracking), never guessed."""
import json


def portal(environ):
    value = (environ.get("MAIL_PORTAL_URL") or "").strip()
    return value if value.startswith("https://") else None


def tracking(environ):
    try:
        data = json.loads((environ.get("PHYSICAL_MAIL_TRACKING") or "").strip() or "{}")
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(number): entry for number, entry in data.items() if isinstance(entry, dict) and isinstance(entry.get("number"), str) and entry["number"].strip()}
