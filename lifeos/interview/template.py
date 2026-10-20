"""LIFE-42: the accepted LIFE-47 UI 4 interview page (Jim, LIFE-42 comment 10829) as Notion blocks, for a new machine-owned round.

Same sections and wording as UI 4, with the round's own state. Two API-forced differences, nothing else: Notion's public API has
no tab block, so UI 4's Prepare / Live Notes / After Interview tabs are top-level toggle headings in the same order; and the
existing protected read-back needs "Live Notes" and "Raw Notes" as top-level regions (notion.protected_snapshot), so they sit
below the two-column brief instead of inside its left column, with "Raw Notes" keeping that exact label. The machine marker and
identity stay the first two blocks and "Derived" stays the last top-level heading (notion.derived_blocks). UI 4's synthetic
coaching cards and sandbox review links are demo content, not template. Unknown facts are written as unknown, never guessed.
"""
from .identity import scheduling_pending
from .notion import FOLD, JIM_NOTES, MARKERS


def _run(content, bold=False, link=None, italic=False):
    return {"type": "text", "text": {"content": content, "link": {"url": link} if link else None},
            "annotations": {"bold": bold, "italic": italic}}


def _line(*parts):
    return [_run(p) if isinstance(p, str) else _run(*p) for p in parts if p]


def _rich(lines):
    out = []
    for n, line in enumerate(lines):
        out += ([_run("\n")] if n else []) + line
    return out


def _block(kind, rich, **extra):
    return {"object": "block", "type": kind, kind: {"rich_text": rich, **extra}}


def _para(*parts):
    return _block("paragraph", _line(*parts))


def _field(label, value):
    return _para((f"{label}: ", True), value)


def _callout(icon, title, lines):
    return _block("callout", _rich([[_run(title, True)]] + lines), icon={"type": "emoji", "emoji": icon}, color="gray_background")


def _states(summary, rows):
    return _block("toggle", _line(summary), children=[_field(label, value) for label, value in rows])


def fold(*lines):
    """D236: the machine marker and identity lines inside one collapsed gray toggle, the page's first block."""
    return {"object": "block", "type": "toggle", "toggle": {"rich_text": _line(FOLD), "color": "gray", "children": [_para(x) for x in lines]}}


def _section(title, children=()):
    return _block("heading_2", _line(title), is_toggleable=bool(children), **({"children": list(children)} if children else {}))


def _prepare(pending):
    return _section("Prepare", [
        _block("heading_3", _line("Pre-interview analysis · Game Theory + Jordan Belfort / Straight Line")),
        _field("Status", "NOT RUN · waiting for a scheduled round." if pending else "NOT RUN."),
        _field("Input contract", "Parent JD and role, company/interviewer/round evidence, approved personal-brand proof, submitted materials, "
                                 "prior-round facts and transcript/notes when available; missing facts remain unavailable."),
        _field("Game Theory lens (derived)", "Jim's objectives and mutual fit; source-supported decision criteria, incentives, information "
                                             "asymmetry, leverage, risks, and questions that reduce uncertainty."),
        _field("Jordan Belfort / Straight Line lens (derived)", "truthful rapport/intelligence gathering, Three Tens (candidate fit, opportunity, "
                                                                "trust), conversational line, evidence-backed objections/looping, tonality plan and closing questions."),
        _field("Output", "2–4 verifiable stories or metrics, round-specific interview plan, high-value questions, objection/credibility-risk "
                         "responses, and a clear close. No hidden-motive or hiring-probability claims."),
        _field("Refresh", "Reuse a previously accepted, source-fingerprinted analysis when inputs and engine/doctrine versions match. "
                          "Never overwrite Live Notes or Protected Raw Notes."),
        _states("Pre-analysis states · normal / empty / degraded", (
            ("Ready", "Both derived lens cards, source references, evidence version, and last accepted analysis time appear."),
            ("Empty", "Analysis has not been run — show \"Not run\" and missing input references; do not show recycled coaching."),
            ("Degraded", "Missing JD, stale role evidence, model quota/route failure or invalid output — identify cause; keep any earlier "
                         "accepted version visibly dated, never label it current.")))])


LIVE_NOTES = (("Questions Asked", "Add exact question here when asked."), ("What Landed", "Add observed, evidenced response here."),
              ("What Needs Work", "Add actual answer gaps here."), ("Signals / Concerns", "Record explicit evidence, not mind-reading."),
              ("Commitments I Made", "Add verbatim commitments."), ("Follow-Up Owed", "Add action and supported due date."),
              ("Next Step / Timing", "Enter only a verified date or confirmed follow-up plan."))


def _live_notes():
    blocks = []
    for label, hint in LIVE_NOTES:
        blocks += [_para((label, True)), _block("bulleted_list_item", _line(hint))]
    return _section("Live Notes", [_block("heading_3", _line("Editable live notes"))] + blocks)


def _after(pending):
    return _section("After Interview", [
        _block("heading_3", _line("Post-interview analysis · Game Theory + Jordan Belfort / Straight Line")),
        _field("Status", "NOT APPLICABLE YET · the interview has not been scheduled or held." if pending
               else "BLOCKED — COMPLETE VERBATIM GRANOLA TRANSCRIPT NOT YET VERIFIED. No completed analysis has run."),
        _field("Required evidence gate", "A source-attributed, word-for-word Granola transcript must be acquired, matched to this exact "
                                         "interview, normalized, written to the existing Full Transcript child, and read back as complete before final post-analysis."),
        _field("First extract facts", "Verbatim questions and Jim answers; expressed interviewer feedback/concerns; commitments; follow-up owed; "
                                      "next steps and supported timing. Keep precise transcript evidence links distinct from inference."),
        _field("Game Theory review (derived)", "Evidence-supported decision dynamics, information gained/missed, leverage, remaining "
                                               "uncertainties and next-round strategy."),
        _field("Jordan Belfort / Straight Line review (derived)", "Conversation control and intelligence gathering, Three Tens credibility "
                                                                 "signals, objection isolation/looping, proof quality and closing/follow-up opportunities. Assess vocal tonality only when "
                                                                 "suitable audio evidence exists; text alone is insufficient."),
        _field("Outputs", "What worked, what needs work, verified commitments, next-round adjustments and source-linked follow-up guidance — "
                          "no opaque score, inferred hidden motives or offer-probability prediction."),
        _field("Failure state", "Missing/partial/misidentified/failed transcript = PENDING or DEGRADED; never publish a transcript-grounded "
                                "analysis as complete."),
        _callout("📑", "Transcript-backed facts", [
            _line(("Status: ", True), "Waiting for accepted Full Transcript; no factual extraction is displayed."),
            _line(("Questions asked: ", True), "Verbatim excerpts + source segment links (unavailable)."),
            _line(("Answer evidence / interviewer reactions: ", True), "Exact observed wording + provenance (unavailable)."),
            _line(("Commitments / follow-up / next step: ", True), "Only explicit statements with supported owners and dates (unavailable).")]),
        _callout("♟️", "Game Theory · post-interview", [_line(("Analysis: ", True), "Pending source-confirmed transcript.")]),
        _callout("🗣️", "Jordan Belfort / Straight Line · post-interview", [_line(("Analysis: ", True), "Pending source-confirmed transcript. "
                                                                                                    "Vocal tonality is unscorable from text alone.")]),
        _states("Post-analysis states · ready / empty / degraded", (
            ("Ready", "Accepted verbatim transcript + exact interview match + extraction with source spans + both derived coaching lenses + "
                      "future-round suggestions."),
            ("Empty", "No verified transcript; explain why post-analysis is unavailable without implying the interview did not occur."),
            ("Degraded", "Transcript incomplete/truncated, mismatched identity, permission failure, analysis failure or failed Notion read-back. "
                         "Display PENDING/DEGRADED and no final coaching claim."))),
        _field("Human content safety", "Your Live Notes and Protected Raw Notes remain separate and editable. These machine-owned sections "
                                       "never overwrite them; no automatic emails, calendar changes, or hiring status changes.")])


def round_page(parent_query, query, parent_id, pipeline_id, identity):
    pending = scheduling_pending(query)
    who = query.interviewer.strip() if query.interviewer and query.interviewer.strip() else "Not known yet"
    ids = " / ".join(x for x in (f"requisition {query.requisition}" if query.requisition else "",
                                 f"Stable Job Key {query.stable_job_key}" if query.stable_job_key else "") if x)
    request = [("Request message ↗", False, query.source.strip())] if pending else ["request message not recorded"]
    parent_url, pipeline_url = f"https://www.notion.so/{parent_id.replace('-', '')}", f"https://www.notion.so/{pipeline_id.replace('-', '')}"
    round_name = f"Interview {query.ordinal}" if query.ordinal else f"Interview — {query.interview_date}"
    brief = _callout("📄", "10-second brief", [
        _line(("Company: ", True), parent_query.company.strip(), " · ", ("Role: ", True), parent_query.role.strip(), " (from opportunity parent)"),
        _line(("Round: ", True), str(query.ordinal or "Not numbered"), " · ", ("Interviewer: ", True), who),
        _line(("Stage: ", True), ("🟡 Scheduling Pending" if pending else "Scheduled", True), " · ", ("Interview outcome: ", True), "Not verified"),
        _line(("When/where: ", True), "Not scheduled · no Calendar event yet. Calendar owns confirmed timing and joining details." if pending
              else f"{query.interview_date} · Calendar owns confirmed timing and joining details."),
        _line(("Next action: ", True), "Confirm an interview time with the recruiter." if pending
              else "Verify interview scheduling / outcome before marking this round completed."),
        _line(("Objective (derived): ", True), "Not run."),
        _line(("Evidence freshness: ", True), *request, f" · {ids}" if ids else "", " · Calendar confirmation pending · no accepted analysis yet."),
        _line(("Opportunity parent ↗", False, parent_url))])
    side = [
        _callout("📅", "Logistics", [_line("Scheduling Pending" if pending else f"Interview date: {query.interview_date}"),
                                     _line("Calendar event: ", "none yet" if pending else "not verified"),
                                     _line("Meeting link: ", "none yet" if pending else "not verified")]),
        _callout("📼", "Transcript & Recording", [
            _line(("Granola acquisition: ", True), "NOT RUN · transcript not yet available / validated"),
            _line(("Full verbatim transcript: ", True), "not yet created; a Full Transcript child is added when a transcript is imported."),
            _line(("Post-analysis gate: ", True), "Await complete transcript + exact interview identity + Notion read-back."),
            _line(("Audio file: ", True), "Unavailable (optional when supported/authorized)."),
            _line(("Video/recording: ", True), "Unavailable (optional)."),
            _line(("Processing: ", True), "Pending · no summary or coaching represented as transcript-backed.")]),
        _callout("🔗", "Navigation", [_line(("Opportunity parent ↗", False, parent_url)), _line(("Hiring Pipeline ↗", False, pipeline_url)),
                                     _line("Prior round: " + ("none (this is Interview 1)" if query.ordinal == 1 else "see the opportunity parent"))]),
        _callout("⚠️", "Evidence guardrails", [_line("Unknown meeting details remain unknown. Derived coaching cannot overwrite facts. "
                                                     "No credentials in page content.")])]
    columns = {"object": "block", "type": "column_list", "column_list": {"children": [
        {"object": "block", "type": "column", "column": {"children": [brief]}},
        {"object": "block", "type": "column", "column": {"children": side}}]}}
    raw = _section("Raw Notes", [_block("toggle", _line("Raw notes · open to write"), children=[
        _para(("Write unprocessed notes here. Production must never overwrite human-authored content.", False, None, True))])])
    return [fold(MARKERS["round"], identity), _block("heading_2", _line(f"{round_name} · " + ("Scheduling Pending" if pending else who))),
            columns, _prepare(pending), _live_notes(), _after(pending), raw, _section("Derived")]


def _link(label, url):
    return (label, False, url) if url else None


def _url(page_id):
    return f"https://www.notion.so/{page_id.replace('-', '')}" if page_id else None


def _bullets(title, items):
    return _block("toggle", _line(title), children=[_block("bulleted_list_item", _line(item)) for item in items])


OWNERSHIP = (("Opportunity parent", "JD, durable role context, contacts, submitted materials, round index"),
             ("Interview child", "round-specific notes + assets"), ("Prior interview child", "inherited verified context"),
             ("Calendar", "logistics"), ("Gmail/Outlook", "recruiting/process facts"), ("Interview OS", "Derived prep only"),
             ("Jim's notes", "yours only; LIFE OS never writes there"))
GUARDRAILS = ("One JD per opportunity", "No competing round copies", "No invented interview children", "No synthetic links shown as real",
              "Preserve Jim-authored content", "Missing context degrades visibly")
JD_RECORD = ("Original vacancy / posting URL", "Captured full job-description text or archived source", "Core responsibilities",
             "Required qualifications", "Preferred qualifications", "Tech / domain requirements",
             "Compensation, location, work arrangement when sourced", "Recruiter clarifications that materially change the role",
             "Source/provenance and capture date")


def opportunity_page(parent_query, query, pipeline_id, marker, adopted=False):
    """LIFE-46: the accepted UI 3b opportunity parent (UI 3 + QA Opportunity Parent, Jim, LIFE-46 comment 10857) as Notion blocks.

    Same sections, order and wording as UI 3b, filled only with this job's verified facts; everything else is written as not
    verified. API-forced differences, as for UI 4: UI 3's tabs (Interviews, Strategy, LinkedIn & Outreach, Materials & Sources)
    are top-level toggle headings below the two columns in the same order, and the nested Action due callout is two lines of
    Role Snapshot (Notion's API takes two levels of nesting per write). The sandbox banner, demo contact cards, test timeline
    and review links are demo content, not template. "🔒 Jim's notes" is the last section: for a new page it holds one
    "Write here." line; on adoption Jim's existing blocks follow it unchanged. The marker is folded away (D236)."""
    pending = scheduling_pending(query)
    company, role = parent_query.company.strip(), parent_query.role.strip()
    request = _link("Request message ↗", query.source.strip() if query.source else None)
    ledger = _link("Job Ledger row ↗", _url(query.ledger_page_id))
    pipeline = _link("Daily Report · Hiring Pipeline ↗", _url(pipeline_id))
    ids = " / ".join(x for x in (f"requisition {query.requisition}" if query.requisition else "",
                                 f"Stable Job Key {query.stable_job_key}" if query.stable_job_key else "") if x)
    stage = "🟡 Scheduling Pending · Interview 1" if pending else "Interviewing"
    snapshot = _callout("📄", "Role Snapshot", [
        _line((company, True)), _line((role, True)),
        _line(("Employment: ", True), "Not verified"), _line(("Compensation: ", True), "Not verified"),
        _line(("Work arrangement: ", True), "Not verified"), _line(("Core systems: ", True), "Not verified"),
        _line(("Role shape: ", True), "Not verified"), _line(("Stage: ", True), (stage, True)),
        _line(("Verified scheduling evidence: ", True), *(["No interview time yet · ", request] if pending and request else ["Not verified"])),
        _line(("Job Ledger: ", True), *([ledger] if ledger else ["exact row not linked"]), f" · {ids}" if ids else ""),
        _line("Close opportunity: approved confirmation design only; no live action link."),
        _line(("Action due: ", True), "No verified deadline."),
        _line(("Next action: ", True), "Confirm an interview time with the recruiter." if pending
              else "Verify interview scheduling / outcome before marking any round completed.")])
    timeline = _callout("📅", "Interview Timeline · evidence versus demo", [
        _line(*(["Interview request: ", request, " · no interview time yet"] if pending and request else ["No verified timeline events yet."])),
        _line(("Interview result: ", True), "Unverified.")])
    follow = _callout("📨", "Follow-up · owner and evidence", [
        _line(("Owner: ", True), "Jim · ", ("Contacts: ", True), "not verified."),
        _line(("Next action: ", True), "Verify the outcome and whether a response/commitment remains before sending anything; "
              "do not infer an unanswered message from an incomplete source check."),
        _line(("Next due: ", True), "No verified current deadline.")])
    strategy = _callout("➡️", "Working strategy / next action", [_line("Derived: Not run.")])
    nav = _callout("🔗", "Quick navigation", [x for x in (
        _line("Job Description — separate full-width section below (verbatim source still pending)"), _line(pipeline),
        _line(ledger) if ledger else None, _line(request) if request else None,
        _line("Interview rounds — child pages at the end of this page"),
        _line("Jim's notes — protected section at the bottom of this page")) if x])
    context = _callout("📄", "Role context · highlights (not full JD)", [
        _line("Not verified."), _line("Full, verbatim JD belongs only in the separate Job Description element below.")])
    contacts = _callout("👥", "Contacts", [
        _line("No verified contacts yet."),
        _line("Gmail opens an evidenced original message/thread. LinkedIn opens only a verified profile. Never create a contact, "
              "log a sent message, or change hiring state by opening a link. Missing source destinations stay unlinked.")])
    health = _block("callout", _rich([[_run("Source health · freshness", True)],
        _line(("Request message: ", True), "linked" if request else "not recorded"),
        _line(("Interview Calendar / meeting outcome: ", True), "Not verified."),
        _line(("JD Health: ", True), ("🟠 INCOMPLETE", True), " — no clean full archived JD yet. Until fixed, Interview OS may use "
              "verified role notes but must not pretend they are the full posting."),
        _line(("Round Health: ", True), "🟡 Interview 1: Scheduling Pending" if pending else "🟡 Rounds: not verified"),
        _line(("Submitted resume: ", True), "Not verified."), _line(("LinkedIn: ", True), "Not verified."),
        _line("Missing or stale sources must remain visibly ", ("UNVERIFIED / DEGRADED", True), ", never silently current or empty.")]),
        icon={"type": "emoji", "emoji": "⚠️"}, color="yellow_background")
    owners = _callout("📄", "Canonical Ownership", [_line((f"{who} ", True), f"→ {what}") for who, what in OWNERSHIP])
    rails = _callout("⚠️", "Guardrails", [_line(rule) for rule in GUARDRAILS])
    columns = {"object": "block", "type": "column_list", "column_list": {"children": [
        {"object": "block", "type": "column", "column": {"children": [snapshot, timeline, follow, strategy]}},
        {"object": "block", "type": "column", "column": {"children": [nav, context, contacts, health, owners, rails]}}]}}
    italic = lambda value: _para((value, False, None, True))
    sections = [
        _callout("🧭", "Canonical opportunity parent", [_line("Owns durable role context, the one canonical Job Description, recruiting "
                                                               "contacts, submitted materials, and the interview-round chain.")]),
        columns,
        _section("Interviews", [
            _block("heading_3", _line("Interview Chain · source links where verified")),
            italic("Each round is a durable child of this opportunity and also participates in an ordered round chain. "
                   "Later rounds inherit verified context from earlier rounds."),
            _block("bulleted_list_item", _line("Round pages are child pages at the end of this opportunity page.")),
            _block("bulleted_list_item", _line("Additional rounds: none verified. Do not manufacture future child pages."))]),
        _section("Strategy", [
            _block("heading_3", _line("Opportunity strategy · derived, not the job description")),
            _para("Derived: Not run."),
            italic("The complete, verbatim source JD belongs exclusively in the separate Job Description section below, never in this strategy tab.")]),
        _section("LinkedIn & Outreach", [
            _para("No verified LinkedIn connections linked to this job yet."),
            _para(("Interaction boundary: ", True), "Opening a verified LinkedIn profile is read-only; only the separately authorized "
                  "Mark as Sent workflow can create a canonical tracked Waiting conversation. Referral offered is not referral confirmed. "
                  "Contacts/progress are associated with the exact job and must not create another CRM.")]),
        _section("Materials & Sources", [
            _block("heading_3", _line("Submitted materials")),
            _block("bulleted_list_item", _line(("Canonical submitted resume: ", True), "source/version should be linked here once verified")),
            _block("bulleted_list_item", _line(("Other candidate materials: ", True), "none verified. Any case study, presentation, "
                                               "portfolio item, or take-home belongs here once actually sent.")),
            _block("heading_3", _line("Source navigation")),
            _block("bulleted_list_item", _line("Canonical Job Ledger: ", *([ledger] if ledger else ["not linked"]))),
            _block("bulleted_list_item", _line("Email source: ", *([request] if request else ["not recorded"]))),
            _block("bulleted_list_item", _line("Calendar source: unavailable until scheduling is confirmed"))]),
        _block("heading_3", _line("Job Description — original source (separate from Strategy)")),
        _callout("📄", "SOURCE JD NOT YET ATTACHED · VERBATIM CONTENT ONLY", [_line(
            "Insert the complete, authoritative job description for this opportunity here when obtained, preserving the original wording "
            "and a link to its source. Do not substitute AI strategy, inferred requirements or the short Role Context summary above for "
            "the JD. Interview pages link here; they do not maintain separate copies.")]),
        _bullets("Required JD record", JD_RECORD),
        {"object": "block", "type": "divider", "divider": {}},
        _block("heading_2", _line(JIM_NOTES)),
        _callout("✍️", "Your space · LIFE OS never writes, moves or deletes anything below this heading.", [_line(
            "When you adopt an existing hand-written page, everything you already wrote is kept here unchanged, in its original order. "
            "LIFE OS checks after every write that this section is unchanged; if it changed, it stops and reports DEGRADED.")])]
    return [fold(marker)] + sections + ([] if adopted else [italic("Write here.")])
