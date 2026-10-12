"""'What they want': one short, fixed phrase built from a fixed request pattern in the sender's own words (D54: no language model, D185).

Only a second-person request in the message counts: a call or conversation, a resume, a rate, availability, or application information. The phrase is evergreen
(no 'today' or 'tomorrow') and carries only the role and requisition the source states. No pattern -> '' (blank). Conflicting durations -> '' and ambiguous, so the row is
marked for review instead of guessing. Returns (phrase, ambiguous)."""
import re

NOUN = r"(?P<noun>phone call|intro(?:ductory)? call|conversation|discussion|meeting|call|chat)"
NEGATION = re.compile(r"\b(?:not|no need|don't|do not|never|unless|rather than|instead of)\b", re.I)
DURATION = re.compile(r"(?:(?P<a>\d{1,3})\s*(?:[-–—]|to)\s*(?P<b>\d{1,3})|(?P<n>\d{1,3}))[-\s]*(?:minutes?|mins?)\b", re.I)
CALL = (re.compile(rf"\b(?:open|available|free|willing|interested)\b[^.?!\n]{{0,40}}?\b(?:to|for|in)\b[^.?!\n]{{0,60}}?\b{NOUN}\b", re.I),
        re.compile(rf"\b(?:schedule|set up|arrange|book|find (?:a )?time for)\b[^.?!\n]{{0,50}}?\b{NOUN}\b", re.I),
        re.compile(r"\b(?:can|could|may) (?:we|i)\b[^.?!\n]{0,20}?\b(?P<noun>chat|talk|speak)\b", re.I))
RESUME = re.compile(r"\b(?:send|share|forward|provide|attach|submit|reply with)\b[^.?!\n]{0,40}?\b(?:resume|résumé|cv)\b|\b(?:need|require|request)\w*\s+your\s+(?:updated\s+)?(?:resume|résumé|cv)\b", re.I)
RATE = re.compile(r"\b(?:send|share|provide|confirm|let me know|what(?:'s| is| are)|please)\b[^.?!\n]{0,40}?\b(?:rate|salary|compensation)\b", re.I)
AVAILABILITY = re.compile(r"\b(?:send|share|provide|confirm|let me know|what|when|please)\b[^.?!\n]{0,40}?\b(?:availability|available times?)\b|\bwhen (?:are|would) you (?:be )?(?:available|free)\b", re.I)
APPLICATION = re.compile(r"\b(?:please|kindly)\s+(?:apply|submit\b[^.?!\n]{0,30}?\bapplication|complete\b[^.?!\n]{0,30}?\bapplication)", re.I)
NOUNS = {"phone call": "call", "intro call": "call", "introductory call": "call", "chat": "chat", "talk": "conversation", "speak": "conversation"}


def _sentences(body):
    for line in str(body or "")[:6000].splitlines():
        if line.lstrip().startswith(">"):
            continue                                                  # quoted text is not this sender's request
        yield from (s for s in re.split(r"(?<=[.?!])\s+", line) if s.strip())


def _clean(sentence, found):
    """A match counts unless a negation comes before it in the same sentence ('no need to send a resume')."""
    return not NEGATION.search(sentence[:found.start()][-40:] + " " + found.group(0)[:30])


def _duration(sentence):
    found = DURATION.search(sentence)
    if not found:
        return ""
    if found.group("a"):
        return f"{int(found.group('a'))}–{int(found.group('b'))}-minute"
    return f"{int(found.group('n'))}-minute"


def request(body, role="", requisition=""):
    calls, found_kinds = [], set()
    for sentence in _sentences(body):
        for rx in CALL:
            hit = rx.search(sentence)
            if hit and _clean(sentence, hit):
                noun = hit.group("noun").lower()
                calls.append((_duration(sentence), NOUNS.get(noun, noun)))
                break
        for kind, rx in (("resume", RESUME), ("rate", RATE), ("availability", AVAILABILITY), ("application", APPLICATION)):
            hit = rx.search(sentence)
            if hit and _clean(sentence, hit):
                found_kinds.add(kind)
    durations = {d for d, _ in calls if d}
    if len(durations) > 1:
        return "", True                                               # two different durations: not one clear request
    parts = []
    if calls:
        duration, noun = next(((d, n) for d, n in calls if d), calls[0])
        parts.append(f"schedule a {duration + ' ' if duration else ''}{noun}")
    for kind, text in (("resume", "send an updated resume"), ("rate", "provide expected rate"), ("availability", "share availability"),
                       ("application", "submit application information")):
        if kind in found_kinds:
            parts.append(text)
    if not parts:
        return "", False
    phrase = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    about = " about " if len(parts) == 1 and calls else " for "
    if role and requisition:
        phrase += f"{about}{role} opportunity #{requisition}"
    elif role:
        phrase += f"{about}{role} opportunity"
    elif requisition:
        phrase += f"{about}opportunity #{requisition}"
    return (phrase[0].upper() + phrase[1:] + ".")[:300], False
