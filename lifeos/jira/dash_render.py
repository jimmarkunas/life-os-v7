"""JFM-221 (Jim, Oct 29 2026): the Notion blocks of the Jim & Matt dashboard pages. Pure: Jira facts in, Notion blocks out. Every Jira text passes dash_scrub first.
Times are Central Time, 12-hour, labelled CT."""
import hashlib
import json

from lifeos.jira import dash_data as D
from lifeos.jira.dash_scrub import scrub

DONE_MARK = "✔ Generated in full from Jira"
MIN_WEEK, CATEGORIES = 5, ("Outreach", "Scheduled", "Networking Calls", "Recruiter Calls", "Company Calls")


def clock(moment):
    local = moment.astimezone(D.CENTRAL)
    return f"{local:%b} {local.day}, {local.strftime('%I:%M %p').lstrip('0')} CT"


def day(value):
    return f"{value:%b} {value.day}"


def seg(text, link=None, bold=False, italic=False, color=None):
    out = {"type": "text", "text": {"content": str(text)[:1900], **({"link": {"url": link}} if link else {})}}
    if bold or italic or color:
        out["annotations"] = {**({"bold": True} if bold else {}), **({"italic": True} if italic else {}), **({"color": color} if color else {})}
    return out


def _parts(parts):
    return [p if isinstance(p, dict) else seg(p) for p in parts if p != ""]


def para(*parts):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _parts(parts)}}


def heading(level, *parts):
    kind = f"heading_{level}"
    return {"object": "block", "type": kind, kind: {"rich_text": _parts(parts)}}


def bullet(*parts):
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": _parts(parts)}}


def quote(*parts):
    return {"object": "block", "type": "quote", "quote": {"rich_text": _parts(parts)}}


def callout(emoji, color, *parts):
    return {"object": "block", "type": "callout", "callout": {"rich_text": _parts(parts), "icon": {"type": "emoji", "emoji": emoji}, "color": color}}


def divider():
    return {"object": "block", "type": "divider", "divider": {}}


def title_of(issue, terms):
    return scrub(issue["summary"], terms)[0]


def item(issue, terms, *tail):
    return bullet(seg(issue["key"], issue["url"], bold=True), " — ", title_of(issue, terms), *[t for t in tail if t])


def _section(title, issues, terms, tail, empty="None"):
    return [heading(3, f"{title} ({len(issues)})"), *([item(i, terms, *tail(i)) for i in issues] or [bullet(seg(empty, italic=True))])]


def megibow_line(counts, week_start):
    """One person's MegIBOW week: total against the minimum of 5 and whether every category has at least 1. `counts` None = not entered / unreadable."""
    label = f"MegIBOW · week of {day(week_start)}"
    if counts is None:
        return [heading(3, label), para(seg("Not entered for this week yet.", italic=True))]
    total = sum(counts.get(c, 0) for c in CATEGORIES)
    missing = [c for c in CATEGORIES if counts.get(c, 0) < 1]
    verdict = "✅ minimum met" if total >= MIN_WEEK and not missing else "⚠️ below target"
    return [heading(3, label),
            para(seg(f"Total {total} / min {MIN_WEEK}", bold=True), f" · {verdict}"),
            para(" · ".join(f"{c} {counts.get(c, 0)}" for c in CATEGORIES)),
            *([para(seg("No activity yet in: ", bold=True), ", ".join(missing))] if missing else [])]


def checkin(people, now, week_start, megibow, terms):
    """people: {name: {"window": dash_data.window(...), "must": [...], "blocked": [...]}}. -> blocks of one Wednesday check-in page."""
    blocks = [callout("🗓️", "blue_background", seg("Generated from Jira ", bold=True), f"on {clock(now)}. ",
                      "Read-only: change the work in Jira and next week's page follows. Done = status-category change date.")]
    for name in ("Jim", "Matt"):
        p = people.get(name) or {"window": {"done": [], "due": [], "overdue": []}, "must": [], "blocked": []}
        w = p["window"]
        blocks += [heading(2, name),
                   *_section("Done in the last 7 days", w["done"], terms, lambda i: [f" · done {day(i['changed'])}"]),
                   *_section("Due in the next 7 days", w["due"], terms, lambda i: [f" · due {day(i['due'])}", f" · {i['status']}"]),
                   *_section("Overdue", w["overdue"], terms, lambda i: [seg(f" · was due {day(i['due'])}", color="red"), f" · {i['status']}"]),
                   *_section("Must-wins", p["must"], terms, lambda i: [f" · {i['status']}" + (f" ({day(i['changed'])})" if i["category"] == "done" and i["changed"] else ""),
                                                                      f" · due {day(i['due'])}" if i["due"] and i["category"] != "done" else ""]),
                   *_section("Blocked with no reason in a comment", p["blocked"], terms, lambda i: [" · add a comment saying why it is blocked"]),
                   *megibow_line(megibow.get(name), week_start)]
    return blocks + [divider(), para(seg(DONE_MARK, italic=True, color="gray"))]


def fingerprint(blocks):
    return hashlib.sha256(json.dumps(blocks, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def synopsis(quarter, label, people, now, terms):
    """people: {name: {"wins": [(issue, vision text or None)], "must": [...], "decisions": [...], "completed": {epic name: n}}}. -> (blocks, fingerprint)."""
    body = []
    for name in ("Jim", "Matt"):
        p = people.get(name) or {"wins": [], "must": [], "decisions": [], "completed": {}}
        done_qbr = sum(1 for i in p["decisions"] + [w for w, _ in p["wins"]] if i["category"] == "done")
        total_qbr = len(p["decisions"]) + len(p["wins"])
        done_must = sum(1 for i in p["must"] if i["category"] == "done")
        completed = sum(p["completed"].values())
        body += [heading(2, name),
                 para(seg("Progress: ", bold=True), f"{done_qbr} of {total_qbr} QBR items done · {done_must} of {len(p['must'])} must-wins done · "
                      f"{completed} issue(s) completed this quarter"
                      + (" (" + " · ".join(f"{k} {v}" for k, v in sorted(p["completed"].items())) + ")" if completed else "")),
                 heading(3, "Vision (Mary Morrissey)")]
        if not p["wins"]:
            body.append(para(seg("No Win ticket yet for this quarter.", italic=True)))
        for issue, text in p["wins"]:
            body += [bullet(seg(issue["key"], issue["url"], bold=True), " — ", title_of(issue, terms), f" · {issue['status']}"),
                     quote(scrub(text, terms)[0]) if text else para(seg("No vision statement in the ticket yet.", italic=True))]
        body += _section("Must-wins", p["must"], terms, lambda i: [f" · {i['status']}", f" · due {day(i['due'])}" if i["due"] and i["category"] != "done" else ""])
        body += _section(f"Decisions & commitments ({label})", p["decisions"], terms,
                         lambda i: [f" · {i['status']}", f" · due {day(i['due'])}" if i["due"] and i["category"] != "done" else ""])
    mark = fingerprint(body)
    head = [callout("🧭", "purple_background", seg(f"{quarter} synopsis", bold=True), f" · from Jira (label {label} and must-win) · updated {clock(now)}. ",
                    "Read-only: plan in the QBR, track in Jira; this page follows.")]
    return head + body + [divider(), para(seg(f"{DONE_MARK} · {mark}", italic=True, color="gray"))], mark
