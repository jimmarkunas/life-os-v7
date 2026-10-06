"""Three explicit source shapes, decided from the normalized mail record alone (D54: no language model).

DIRECT_HUMAN       a human who says, in first person or in a signature, that they are a recruiter / sourcer / talent partner (in-house included).
HUMAN_NAMED_RELAY  a job-board message that exposes an actual human name through a fixed pattern.
OTHER              automation, bulk or list mail, auto-replies, role addresses, hiring managers and anything without qualifying evidence.
Strong automation evidence is checked first and subject words never override it. Returns (shape, reason code, evidence)."""
import re

from .identity import clean_name

DIRECT_HUMAN, HUMAN_NAMED_RELAY, OTHER = "DIRECT_HUMAN", "HUMAN_NAMED_RELAY", "OTHER"
RELAY_DOMAINS = ("linkedin.com", "indeed.com", "ziprecruiter.com", "dice.com", "glassdoor.com", "wellfound.com", "jobright.ai", "lensa.com", "hiring.cafe")
ROLE_ALIASES = {"info", "jobs", "careers", "career", "talent", "hr", "recruiting", "recruitment", "recruiter", "hiring", "apply", "team", "hello", "contact", "support", "admin", "sales", "staffing"}
TITLE = (r"(?:(?:senior|sr\.?|lead|principal|technical|tech|it|executive|corporate|contract|associate)\s+)*"
         r"(?:recruiter|sourcer|talent\s+acquisition(?:\s+(?:partner|specialist|manager|lead|coordinator))?|talent\s+partner|"
         r"staffing\s+(?:consultant|specialist|manager)|recruiting\s+(?:manager|coordinator|partner|specialist|lead)|headhunter|search\s+consultant|recruitment\s+consultant)")
FIRST_PERSON = re.compile(rf"\bI(?:'m|’m| am)\s+(?:a|an|the|your)?\s*(?:\w+\s+){{0,3}}?{TITLE}\b", re.I)
SIGNATURE = re.compile(rf"^[\s\-–—|•*]*(?:[\w.'’ \-]{{0,40}}[|,\-–—]\s*)?{TITLE}\b", re.I)
HIRING_MANAGER = re.compile(r"\b(?:hiring manager|engineering manager|director|vice president|vp\b|head of|team lead|manager of)\b", re.I)
AUTOREPLY = re.compile(r"\b(?:automatic reply|auto[- ]?reply|out of (?:the )?office|undeliverable|delivery (?:status|failure)|vacation reply)\b", re.I)
RELAY_PHRASE = re.compile(r"\b(?:sent you a message|has messaged you|new message from|replied to your|message from|message replied|you have a new message|inmail)\b", re.I)
RELAY_BODY_NAME = re.compile(r"(?:^|\n)[ \t]*(?:InMail:\s*)?You have a new message\s*\n+\s*(?P<name>[^\n]{2,60}?)\s*\n", re.I)      # the board's plain-text layout: header line, then the sender's name
SOLICITATION = tuple(re.compile(p, re.I) for p in (
    r"\bupdated (?:resume|cv)\b", r"\bexpected (?:pay )?rate\b", r"\b(?:c2c|corp[- ]to[- ]corp|w2|1099)\b", r"\bvisa (?:status|type)\b|\bwork authori[sz]ation\b",
    r"\bjob description\b", r"\b(?:urgent|immediate) (?:requirement|opening|position|need)\b", r"\blet me know (?:your|if you(?:'re| are)) interest",
    r"\bshare (?:your|an) (?:updated )?(?:resume|cv)\b", r"\b(?:direct client|end client|implementation partner|prime vendor)\b", r"\bjob (?:opportunity|opening)\b",
    r"\bI have an? (?:urgent |immediate )?(?:position|requirement|opening|role)\b"))
RELAY_SUBJECT_NAME = re.compile(r"^(?:re:\s*)?(?:new )?message from\s+(?P<name>[^:\-|]+)", re.I)


def _domain(address):
    return address.rsplit("@", 1)[-1] if "@" in address else ""


def _is_relay(address):
    domain = _domain(address)
    return any(domain == d or domain.endswith("." + d) for d in RELAY_DOMAINS)


def signature_line(body):
    """The first short line in the closing part of the message whose start is a recruiter title, or ''."""
    lines = [line.strip() for line in str(body or "").splitlines() if line.strip()]
    for line in lines[-14:]:
        if len(line) <= 120 and SIGNATURE.search(line):
            return line
    return ""


def first_person(body):
    match = FIRST_PERSON.search(str(body or "")[:4000])
    return match.group(0) if match else ""


def classify(message):
    """-> (shape, reason, evidence). `evidence` carries only what the source states: name, relay, signature."""
    marks, address = message["markers"], message["sender_address"]
    if AUTOREPLY.search(message.get("subject", "")) or marks["auto"]:
        return OTHER, "AUTOREPLY", {}
    if _is_relay(address):
        return _relay(message)
    if marks["bulk"]:
        return OTHER, "BULK", {}
    if marks["noreply"] or address.split("@")[0].replace("-", "").replace("_", "").replace(".", "") in ROLE_ALIASES or not address:
        return OTHER, "AUTOMATION_ADDRESS", {}
    body = message.get("body", "")
    line, first = signature_line(body), first_person(body)
    if not (line or first) and clean_name(message.get("sender_name", "")) and sum(1 for rx in SOLICITATION if rx.search(subject_and(message))) >= 2:
        return DIRECT_HUMAN, "STAFFING_OUTREACH", {"name": message.get("sender_name", ""), "signature": "", "first_person": ""}
    if not (line or first):
        return OTHER, ("HIRING_MANAGER" if HIRING_MANAGER.search(signature_tail(body)) else "NO_RECRUITER_EVIDENCE"), {}
    return DIRECT_HUMAN, "RECRUITER_SELF_IDENTIFIED", {"name": message.get("sender_name", ""), "signature": line, "first_person": first}


def subject_and(message):
    return message.get("subject", "") + "\n" + str(message.get("body", ""))[:6000]


def signature_tail(body):
    return "\n".join([line.strip() for line in str(body or "").splitlines() if line.strip()][-14:])


def _relay(message):
    subject, body = message.get("subject", ""), message.get("body", "")
    if not (RELAY_PHRASE.search(subject) or RELAY_PHRASE.search(body[:1500]) or " via " in message.get("sender_name", "")):
        return OTHER, "RELAY_NOT_A_MESSAGE", {}                         # alerts, digests and acknowledgements from a board
    name = message.get("sender_name", "") if " via " in message.get("sender_name", "") else ""
    if not name:
        found = RELAY_BODY_NAME.search(body[:600])
        name = found.group("name").strip() if found else ""
    if not name:
        found = RELAY_SUBJECT_NAME.match(subject.strip())
        name = found.group("name").strip() if found else ""
    return HUMAN_NAMED_RELAY, "RELAY_NAMED" if name else "RELAY_NAME_UNREADABLE", {"name": name, "relay": True}
