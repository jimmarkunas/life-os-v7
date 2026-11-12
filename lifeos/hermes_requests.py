"""Hermes requests (LIFE-307 / LIFE-293): V7 asks Hermes for work through Buzz, and only when something is new. Counts and fixed codes only.

What is new: a LIFE issue in status Agent Go that carries the label `hermes`. Each one gets ONE signed, correlated request (recipient alias `hermes`, type
handoff_request, case <KEY>-AGENTGO). The relay is the ledger: the case tag shows that a request already exists, and the idempotency key makes a repeated post a
no-op (`already_delivered`). Nothing new means nothing is posted. A dry run (the default) reads and counts and never writes.
Flow: read the eligible issues completely -> look each case up on the relay -> plan -> post (live only) -> read each posted case back once.
Fail closed: an unreadable Jira page or relay lookup plans nothing; a refused or ambiguous post stops the run with its fixed code. The request text is a fixed
template with the issue key only: no ticket text, no names, no message content.
Config (names only; values never appear in code, output or logs): JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN, BUZZ_URL_LIFE_OS (the relay's https address),
BUZZ_STAGE_KEY (this stage's own scoped member key), BUZZ_STAGE_CHANNEL (the channel UUID), BUZZ_STAGE_HERMES_PUBKEY (the approved recipient's public key)."""
import os
import re

from lifeos.buzz import nip98
from lifeos.buzz.relay import HEX, URL, UUID, BuzzError, Relay
from lifeos.jira.dash_data import search
from lifeos.platform.jira import Jira, JiraError

COMMUNITY, ALIAS, ETYPE = "life-os", "hermes", "handoff_request"
LABEL, STATUS = "hermes", "Agent Go"
JQL = f'project = LIFE AND status = "{STATUS}" AND labels = "{LABEL}" ORDER BY key ASC'
KEY = re.compile(r"LIFE-\d+")
NAMES = ("BUZZ_URL_LIFE_OS", "BUZZ_STAGE_KEY", "BUZZ_STAGE_CHANNEL", "BUZZ_STAGE_HERMES_PUBKEY")
TEXT = "REQUEST case={case} jira={key}: this LIFE issue is in {status}. Read it, then reply in this thread with the case id and ACK, or BLOCKED and one reason. No other action."


class HermesError(JiraError):
    """Fixed codes only; never a key, a URL, an issue field or message content."""


def case_of(key):
    return f"{key}-AGENTGO"


def idempotency_of(key):
    return f"hermes-{key}-agentgo"


def relay_from_env(environ, live):
    """(relay, channel) from the environment. A dry run gets a relay with no write permission at all."""
    missing = [name for name in NAMES if not (environ.get(name) or "").strip()]
    if missing:
        raise HermesError("HERMES_CONFIG_MISSING:" + ",".join(missing))            # setting names only, never values
    url, channel, pubkey = (environ[name].strip() for name in ("BUZZ_URL_LIFE_OS", "BUZZ_STAGE_CHANNEL", "BUZZ_STAGE_HERMES_PUBKEY"))
    if not (URL.match(url) and UUID.match(channel) and HEX.match(pubkey)):
        raise HermesError("HERMES_CONFIG_INVALID")
    try:
        secret = nip98.secret_from(environ["BUZZ_STAGE_KEY"].strip())
    except Exception:                                                              # noqa: BLE001 - whatever the key parser says, the caller sees one fixed code
        raise HermesError("HERMES_CONFIG_INVALID") from None
    writes = [(COMMUNITY, channel)] if live else []
    return Relay({COMMUNITY: url}, {COMMUNITY: secret}, writes, recipients={(COMMUNITY, ALIAS): pubkey}, may_address=True), channel


def _buzz(error):
    return HermesError("HERMES_BUZZ_" + str(error.code).upper())


def _exists(relay, channel, key):
    try:
        page = relay.evidence_page(COMMUNITY, case=case_of(key), channel=channel, limit=1)
    except BuzzError as error:
        raise _buzz(error) from None
    return bool(page["events"])


def run(limit, live, environ=os.environ, jira=None, relay=None, channel=None):
    jira = jira or Jira.from_env(environ)
    issues = search(jira, JQL, "status,labels")
    keys = sorted({i["key"] for i in issues if isinstance(i, dict) and isinstance(i.get("key"), str) and KEY.fullmatch(i["key"])}, key=lambda k: int(k.split("-")[1]))
    counts = {"eligible": len(keys), "invalid": len(issues) - len(keys), "already": 0, "planned": 0, "posted": 0, "already_delivered": 0, "verified": 0, "deferred": 0, "failed": 0}
    if not keys:
        return counts                                                              # nothing new: nothing is read from Buzz and nothing is posted
    if relay is None:
        relay, channel = relay_from_env(environ, live)
    pending = [key for key in keys if not _exists(relay, channel, key)]
    counts["already"], counts["planned"] = len(keys) - len(pending), len(pending)
    if not live:
        return counts
    batch = pending[:max(limit, 0)]
    counts["deferred"] = len(pending) - len(batch)
    posted = []
    for key in batch:
        try:
            out = relay.post_message(COMMUNITY, channel, TEXT.format(case=case_of(key), key=key, status=STATUS), idempotency_of(key),
                                     recipient=ALIAS, etype=ETYPE, case=case_of(key))
        except BuzzError as error:
            raise _buzz(error) from None
        status = out.get("status") if isinstance(out, dict) else None
        if status == "already_delivered":
            counts["already_delivered"] += 1
        elif status == "sent_not_read_back":
            counts["posted"] += 1
            posted.append(key)
        else:
            raise HermesError("HERMES_BUZZ_UNEXPECTED")
    for key in posted:                                                             # publication is not receipt: each posted case is read back once
        counts["verified" if _exists(relay, channel, key) else "failed"] += 1
    if counts["failed"]:
        raise HermesError("HERMES_READBACK_MISSING")
    return counts
