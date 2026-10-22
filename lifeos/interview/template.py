"""LIFE-88 / LIFE-46: the two Jim-approved native Notion layouts as block trees, nothing else.

UI 3c opportunity page: https://app.notion.com/p/3ff3c5a0592681aeba23e00675f097f1 (LIFE-88 D01-D18, 10867, 10869).
UI 4 interview page: https://app.notion.com/p/3fa3c5a059268118ac0bd448ee12c199 (LIFE-46 10874, 10875).

Same block types, nesting, order, labels, tables, native tabs and column ratios as those pages, filled from a view.Opportunity
(and its round). Their ExampleCo / Smartsheet values are QA fixture data: here every value comes from the view, and a source the
view does not have is written as not verified. Deliberate differences, each required by an approved rule: QA-only prose (the
"QA preview details" / "QA design reference" toggles, "(QA preview)" / "(sandbox)" suffixes) is left out (D16.2); Mark Complete
is a toggle that says it is unavailable, never a checkbox (D05, LIFE-88 10869); a Full Transcript page is linked from its callout
because the API creates child pages only at the end of a page. Nested children are written in stages by notion.write_tree."""
from .notion import FOLD, JIM_NOTES, LIVE_PANE, MARKERS, RAW_HEADING, RAW_TOGGLE, ROUND_FOLD
from .view import UNCONNECTED, primary_action, stage

GRAY, NV = "gray", "Not verified"
ARROW, INFO = "https://www.notion.so/icons/arrow-right_gray.svg", "https://www.notion.so/icons/info-alternate_gray.svg"
PAGE_ICONS = {kind: {"type": "external", "external": {"url": f"https://www.notion.so/icons/{name}_gray.svg"}}
              for kind, name in (("opportunity", "briefcase"), ("round", "target"))}       # the approved pages' own icons
PLAIN = "default_background"


def run(content, bold=False, link=None, italic=False, color=None):
    out = {"type": "text", "text": {"content": content, "link": {"url": link} if link else None},
           "annotations": {"bold": bold, "italic": italic}}
    if color:
        out["annotations"]["color"] = color
    return out


def mention(page_id):
    return {"type": "mention", "mention": {"type": "page", "page": {"id": page_id}}}


def _rich(parts):
    return [p if isinstance(p, dict) else run(p) if isinstance(p, str) else run(*p) for p in parts if p]


def block(kind, *parts, children=None, **extra):
    body = {"rich_text": _rich(parts), **extra}
    if children:
        body["children"] = list(children)
    return {"object": "block", "type": kind, kind: body}


def para(*parts, children=None):
    return block("paragraph", *parts, children=children)


def field(label, *value):
    return para((f"{label}: ", True), *value)


def note(value):
    return para((value, False, None, False, GRAY))


def h(level, value):
    return block(f"heading_{level}", value)


def bullet(*parts):
    return block("bulleted_list_item", *parts)


def toggle(title, children, color=None):
    return block("toggle", title, children=children, **({"color": color} if color else {}))


def callout(children, icon=None, color=None, *title):
    extra = {"color": color} if color else {}
    if icon:
        extra["icon"] = {"type": "external", "external": {"url": icon}} if icon.startswith("https://") else {"type": "emoji", "emoji": icon}
    return block("callout", *title, children=children, **extra)


def divider():
    return {"object": "block", "type": "divider", "divider": {}}


def columns(*cols):
    return {"object": "block", "type": "column_list", "column_list": {"children": [
        {"object": "block", "type": "column", "column": {"width_ratio": ratio, "children": list(kids)}} for ratio, kids in cols]}}


def tabs(*panes):
    return {"object": "block", "type": "tab", "tab": {"children": [para(title, children=kids) for title, kids in panes]}}


def table(rows, header_row=True, header_column=False):
    width = len(rows[0])
    return {"object": "block", "type": "table", "table": {"table_width": width, "has_column_header": header_row,
            "has_row_header": header_column, "children": [{"object": "block", "type": "table_row", "table_row": {
                "cells": [_rich(cell if isinstance(cell, list) else [cell]) for cell in row]}} for row in rows]}}


def _url(page_id):
    return f"https://www.notion.so/{page_id.replace('-', '')}" if page_id else None


def fold(*lines, title=FOLD, extra=()):
    """D236: the machine marker and identity lines inside one collapsed gray toggle, the page's first block."""
    return toggle(title, [para(x) for x in lines] + list(extra), color=GRAY)


def render_line(job, source, page="pending"):
    return f"v7-render:1;job={job or 'none'};source={source};page={page}"


# ---------- UI 3c opportunity page ----------

def _health(view, rounds_label):
    rows = [("Interviews", rounds_label),
            ("Job Ledger", {"linked": "exact row linked by Stable Job Key", "mismatch": "row does not match this job's Stable Job Key",
                            "unreadable": "row unreadable this run"}.get(view.ledger, "no exact row linked")),
            ("Request message", "linked" if view.request_url else "not recorded")]
    rows += [(name, "not connected to LIFE OS yet") for name in UNCONNECTED]
    return callout([field(k, v) for k, v in rows] + [field("Overall", "PARTIAL — never read a missing source as all clear")],
                   "⚠️", "yellow_background", (f"Source Health — {view.company}", True))


def _interviews(view):
    rows = [["Person", "Interview Date", "Interview"]]
    for r in view.rounds:
        name = f"Interview {r.ordinal}" if r.ordinal else f"Interview — {r.date}"
        rows.append([r.interviewer or "Interviewer not verified",
                     f"{r.date} · Calendar event not verified" if r.date else "Scheduling pending · no Calendar event",
                     [mention(r.page_id)] if r.page_id else name])
    if len(rows) == 1:
        rows.append(["No interview pages yet", "—", "—"])
    return rows


def opportunity_page(view, today, source="pending", adopted=None):
    """The full UI 3c page: machine region first, then the protected Jim's Notes region (blocks after JIM_NOTES)."""
    marker = MARKERS["opportunity"] + (f";adopted={adopted}" if adopted else "")
    current, next_date = stage(view, today)
    label, action, due, link, basis = primary_action(view, today)
    ledger = [("Job Ledger row ↗", False, _url(view.ledger_page_id))] if view.ledger_page_id else []
    posting = [("Original posting ↗", False, view.apply_url)] if view.apply_url else []
    primary = callout([
        para((action, True)),
        para(("Due: ", True), due, " · ", ("Owner: ", True), "Jim · ", ("Source completeness: ", True), "Partial",
             *([" · ", ("Request message ↗", False, link)] if link else [])),
        toggle("Mark Complete · unavailable", [para(("Action: ", True), action), para(
            "Not available yet: completion needs an authenticated two-step confirmation with a canonical write and read-back, "
            "which is not proven. Nothing here records completion.")])], None, PLAIN,
        (label, True), " · ", basis)
    secondary = [h(4, "Secondary Actions"), para("No additional verified actions."),
                 note("A live opportunity may show up to two. Never pad with invented obligations."),
                 toggle("Empty / unknown states", [field("All caught up", "Only after complete source checks."),
                                                   field("Suggested next move", "Labeled suggestion, never a task."),
                                                   field("Degraded", "Explain missing or conflicting source evidence.")]), para()]
    left = [
        callout([h(3, "LinkedIn Contacts"),
                 table([["Person", "Role / Relevance", "Outreach Status"], ["No separately verified contacts", "—", "Not tracked"]]),
                 note("Networking contacts for this exact opportunity only. Interviewers and recruiting coordinators remain in "
                      "their own tables.")], INFO, PLAIN),
        tabs(("Role Snapshot", [
            h(3, "Role requirements"), field("Key systems", NV), field("Employer need", f"{NV} — the original posting is not connected yet."),
            h(3, "Your fit"), field("Relevant strengths", view.fit_summary + " (LIFE OS fit summary)" if view.fit_summary else NV),
            field("Potential gap", NV), para(mention(view.ledger_page_id)) if view.ledger_page_id else para("Job Ledger row not linked.")]),
             ("Win Strategy", [
            h(3, "How to win this opportunity"), field("Positioning", "Not run."), field("Lead with", "Not run."),
            field("Address", "Not run."), field("Interview execution", "Not run."),
            field("Advance", "Confirm the employer’s next decision, owner and timing before follow-up.")])),
        toggle("Job Description · requirement highlights", [
            bullet(f"{NV} — no verified requirement highlights yet."), *([bullet(*posting)] if posting else []),
            bullet("Full original posting is in the separate section below once verified; do not reconstruct it from highlights")])]
    right = [
        callout([h(3, "Quick Facts"), table([["Employment Type", NV],
                                             ["Compensation", f"{view.compensation} (Job Ledger)" if view.compensation else NV],
                                             ["Work Arrangement", f"{view.work_mode} (Job Ledger)" if view.work_mode else NV]],
                                            header_row=False, header_column=True)], INFO, PLAIN),
        callout([h(3, "Interviews"), table(_interviews(view)),
                 note("Names link to LinkedIn and dates to the Calendar event only once verified; Interview links open the "
                      "interview page.")], "📅", "gray_background"),
        callout([h(3, "Recruiters & Coordinators"), table([["Person", "Role", "Email"], ["No verified recruiters or coordinators",
                                                                                         "—", "Not verified"]]),
                 note("Email opens the latest verified thread for THIS job only, never composes. Interview participants are "
                      "listed only under Interviews.")], "👥", "gray_background"),
        callout([h(3, "Materials Sent"), bullet(("Submitted résumé", True), " — File link not yet verified"),
                 note("Other sent forms, correspondence, project plans and articles appear here when linked to verified files "
                      "or outbound messages.")], "📎", "gray_background")]
    machine = [
        fold(marker, render_line(view.stable_job_key, source), extra=[_health(view, f"{len(view.rounds)} verified interview page(s)")]),
        para(("Stage: ", True), current, " · ", ("Next confirmed interview: ", True), next_date, " · ",
             ("Current opportunity status: ", True), "Not verified (outcome sources not connected)", *([" · "] + ledger if ledger else [])),
        callout([h(3, "Action Center"), columns((0.625, [primary]), (0.375, secondary))], ARROW, "blue_background"),
        divider(), columns((0.63, left), (0.37, right)), divider(),
        h(2, "Job Description — Original Posting"),
        callout([para("When the authoritative source is verified, the COMPLETE verbatim posting appears here with its original "
                      "URL and capture information. Excerpts are never substituted for the full JD.", *([" "] + posting if posting else []))],
                "📄", "gray_background", (f"Full original job description not yet connected for {view.company}.", True)),
        divider()]
    return machine + notes_region()


def notes_region():
    return [h(2, JIM_NOTES),
            callout([], "✍️", "gray_background", ("Protected personal space.", True), " LIFE OS never writes, moves or deletes "
                    "anything below this heading. Existing notes and interview pages stay exactly as they are."),
            toggle("Live Notes · protected space", [para()]), toggle("Raw Notes · protected space", [para()]),
            toggle("Interview Rounds · existing children remain canonical",
                   [para("Open interview pages from the Interviews table; their child pages stay at the end of this page.")])]


def machine_part(blocks):
    """The machine region of a rendered UI 3c page: everything before the Jim's Notes heading."""
    return blocks[:next((i for i, b in enumerate(blocks) if b["type"] == "heading_2"
                         and b["heading_2"]["rich_text"][0]["text"]["content"] == JIM_NOTES), len(blocks))]


# ---------- UI 4 interview page ----------

PREP_SECTIONS = (("focus", "What they may evaluate"), ("strongest_evidence", "Strongest stories & evidence"),
                 ("pressure_points", "Risks & objections"), ("questions", "Questions to ask"))
LIVE_HEADINGS = ("Questions Asked", "What Landed", "What Needs Work", "Signals / Concerns", "Commitments I Made",
                 "Follow-Up Owed", "Next Step / Timing")


def prepare_pane(prep, ordinal):
    """The Prepare tab's blocks: accepted preparation (prep.normalize output) or the not-run state."""
    out = [h(3, "Interview objective"), para("Not run." if not prep else "Use the evaluation themes, stories and questions below.")]
    for key, label in PREP_SECTIONS:
        values = (prep or {}).get(key) or ()
        out += [h(3, label)] + ([bullet(v) for v in values] if values else [para("Not run.")])
    out += [h(3, "Prior-round lessons"),
            para("No prior round — this is Interview 1." if ordinal == 1 else "Inherited only from the previous round's accepted "
                 "preparation; it appears under Risks & objections and Questions to ask."),
            toggle("Coaching methods & source requirements", [
                field("Game Theory", "Evaluate mutual fit, source-supported incentives, decision criteria, information gaps and "
                      "risks; do not infer hidden motives."),
                field("Jordan Belfort / Straight Line", "Build truthful rapport, test the Three Tens (candidate fit, opportunity, "
                      "trust), handle supported objections, and plan a concise close."),
                field("Evidence required", "Canonical parent JD, confirmed interviewer/round facts, approved career proof, "
                      "submitted materials and any verified prior-round transcript/notes."),
                field("Source health", "ACCEPTED PREPARATION" if prep else "NOT RUN. Failed or stale inputs display DEGRADED, "
                      "not recycled coaching.")])]
    return out


def transcript_lines(state="absent", page_id=None):
    """The UI 4 Full Transcript facts: (Source Health line, Transcript & Recording line). A page that exists is linked; it reads as an accepted
    transcript only in state "accepted" (create.transcript_state), never because the page exists."""
    if not page_id:
        return (field("Transcript", "no Full Transcript page yet; Granola import not connected"),
                field("Full Transcript page", "Not created yet — added when a verified transcript is imported"))
    if state == "accepted":
        return field("Transcript", "accepted Full Transcript page"), field("Full Transcript page", "Accepted transcript · ", mention(page_id))
    return (field("Transcript", "Full Transcript page exists but is not an accepted transcript"),
            field("Full Transcript page", "Exists — not an accepted transcript · ", mention(page_id)))


def round_page(view, query, parent_id, identity, prep=None, transcript=("absent", None)):
    pending = not query.interview_date
    who = (query.interviewer or "").strip() or None
    name = f"Interview {query.ordinal}" if query.ordinal else f"Interview — {query.interview_date}"
    ref, cancelled = query.calendar_ref, bool(query.calendar_ref and query.cancelled and not pending)
    status = ("Scheduling Pending" if pending else f"Cancelled {query.interview_date} · Calendar" if cancelled
              else f"Scheduled {query.interview_date} · " + ("Calendar invite accepted" if ref else "Calendar not verified"))
    health = callout([field("Round identity", f"{name} · {status}"),
                      field("Calendar / meeting", "The Calendar invite was cancelled." if cancelled else "Accepted Calendar invite linked by reference; join URL not verified."
                            if ref and not pending else "No confirmed event, join URL or original invite verified."),
                      field("Request message", "linked" if view.request_url else "not recorded"),
                      field("Preparation", "accepted source-grounded preparation" if prep else "not run"),
                      transcript_lines(*transcript)[0],
                      field("Source quality", "PARTIAL — unknown facts stay unknown")],
                     "⚠️", "yellow_background", (f"Source Health — {view.company} · {name}", True))
    rails = para(("Guardrails: ", True), "Never invent interview dates, contacts, outcome or URLs. The opportunity page owns the "
                 "full JD. Calendar owns logistics; Live Notes and Raw Notes survive every automated refresh unchanged.")
    snapshot = callout([para(("Interviewer: ", True), who or NV, " · ", ("Status: ", True), status),
                        para(("When: ", True), "No verified Calendar event or meeting time" if pending else f"{query.interview_date} · cancelled in Calendar" if cancelled
                             else f"{query.interview_date} · accepted Calendar invite" if ref else f"{query.interview_date} · Calendar event not verified"),
                        para(("Next action: ", True), "Confirm an interview time with the recruiter." if pending
                             else "Ask the recruiter whether the interview moves to a new time." if cancelled
                             else "Prepare; joining details come from the Calendar invite." if ref
                             else "Verify scheduling and interview details before treating this round as confirmed."),
                        para(("Objective (derived): ", True), "Not run." if not prep else "See Prepare.")],
                       "🎯", "blue_background", ("Interview snapshot", True))
    live = [note("Your notes. Type directly beneath each heading; automated updates never touch these blocks.")]
    for heading in LIVE_HEADINGS:
        live += [h(3, heading), para()]
    after = [h(3, "Outcome & decisions"), para(("Outcome: ", True), f"{NV} · ", ("Employer decision: ", True), "Unknown · ",
                                                ("Next round: ", True), NV),
             h(3, "Commitments & follow-up"), field("Your commitments", "No verified interview evidence yet."),
             field("Follow-up owed / due", "Unverified; no task or deadline is created from this page."),
             h(3, "Transcript-backed facts"), field("Status", "PENDING — no accepted word-for-word transcript."),
             field("Questions and answers", "No verified verbatim evidence."), field("Interviewer feedback", "No verified statements."),
             h(3, "Coaching & next-round improvements"), field("Game Theory review", "Not run."),
             field("Straight Line review", "Not run. Audio is required for vocal-tonality claims."),
             field("Next-round preparation", "Wait for source-backed interview notes and transcript."),
             toggle("Processing requirements & degraded states", [
                 para("A complete word-for-word Granola transcript must match this exact round and be saved/read back in the "
                      "Full Transcript page before final post-analysis."),
                 para("Source excerpts and commitments are FACT; coaching assessments are DERIVED and must be labeled."),
                 para("Missing, partial, misidentified or unreadable evidence displays PENDING or DEGRADED, never a completed "
                      "analysis or hiring prediction."),
                 para("Live Notes and Raw Notes are protected from machine edits.")])]
    left = [tabs(("Prepare", prepare_pane(prep, query.ordinal)), (LIVE_PANE, live), ("After Interview", after)),
            h(3, RAW_HEADING), toggle(RAW_TOGGLE, [para(("Write unprocessed notes here. LIFE OS never overwrites them.", False, None, True))])]
    right = [callout([h(3, "Logistics"), field("Date / time", query.interview_date or NV), field("Calendar event", f"{'Cancelled' if cancelled else 'Accepted invite'} · ref {ref}" if ref and not pending else "Unavailable"),
                      field("Meeting link / location", "Unavailable"),
                      note("Only the exact confirmed invitation and joining information for this round is linked.")], "📅", "gray_background"),
             callout([h(3, "Interviewer"), field("Name", who or NV), field("Role", NV), field("LinkedIn", NV)], "👤", "gray_background"),
             callout([h(3, "Transcript & Recording"), field("Granola", "Not imported / not verified"),
                      transcript_lines(*transcript)[1],
                      field("Audio / video recording", "No verified media linked")], "📼", "gray_background"),
             callout([h(3, "Navigation"), para(mention(parent_id)), para(mention(view.pipeline_id)),
                      field("Previous round", "None — this is Interview 1" if query.ordinal == 1 else NV)], "🔗", "gray_background")]
    return [fold(MARKERS["round"], identity, title=ROUND_FOLD, extra=[health, rails]), snapshot, columns((0.7, left), (0.3, right))]
