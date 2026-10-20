"""LIFE-42: the accepted LIFE-47 UI 4 interview page (Jim, LIFE-42 comment 10829) as Notion blocks, for a new machine-owned round.

Same sections and wording as UI 4, with the round's own state. Two API-forced differences, nothing else: Notion's public API has
no tab block, so UI 4's Prepare / Live Notes / After Interview tabs are top-level toggle headings in the same order; and the
existing protected read-back needs "Live Notes" and "Raw Notes" as top-level regions (notion.protected_snapshot), so they sit
below the two-column brief instead of inside its left column, with "Raw Notes" keeping that exact label. The machine marker and
identity stay the first two blocks and "Derived" stays the last top-level heading (notion.derived_blocks). UI 4's synthetic
coaching cards and sandbox review links are demo content, not template. Unknown facts are written as unknown, never guessed.
"""
from .identity import scheduling_pending
from .notion import MARKERS


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
    return [_para(MARKERS["round"]), _para(identity), _block("heading_2", _line(f"{round_name} · " + ("Scheduling Pending" if pending else who))),
            columns, _prepare(pending), _live_notes(), _after(pending), raw, _section("Derived")]
