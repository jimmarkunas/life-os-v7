"""LIFE-42: the accepted LIFE-47 UI 4 interview page as Notion blocks, for a new machine-owned round.

Notion's API has no tabs, so UI 4's Prepare / Live Notes / After Interview tabs are top-level toggle headings. The machine marker
and identity stay the first two blocks, "Live Notes" and "Raw Notes" stay top-level protected regions and "Derived" stays the
last top-level heading (notion.protected_snapshot, notion.derived_blocks). Unknown facts are written as unknown, never guessed.
"""
from .identity import scheduling_pending
from .notion import MARKERS


def _run(content, bold=False, link=None):
    return {"type": "text", "text": {"content": content, "link": {"url": link} if link else None},
            "annotations": {"bold": bold}}


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


def _callout(icon, title, lines):
    return _block("callout", _rich([[_run(title, True)]] + lines), icon={"type": "emoji", "emoji": icon}, color="gray_background")


def _section(title, children=()):
    return _block("heading_2", _line(title), is_toggleable=bool(children), **({"children": list(children)} if children else {}))


def round_page(parent_query, query, parent_id, pipeline_id, identity):
    pending = scheduling_pending(query)
    who = query.interviewer.strip() if query.interviewer and query.interviewer.strip() else "Not known yet"
    ids = " / ".join(x for x in (f"requisition {query.requisition}" if query.requisition else "",
                                 f"Stable Job Key {query.stable_job_key}" if query.stable_job_key else "") if x)
    request = [("Request message ↗", False, query.source.strip())] if pending else ["Not recorded"]
    parent_url, pipeline_url = f"https://www.notion.so/{parent_id.replace('-', '')}", f"https://www.notion.so/{pipeline_id.replace('-', '')}"
    brief = _callout("📄", "10-second brief", [
        _line(("Company: ", True), parent_query.company.strip(), " · ", ("Role: ", True), parent_query.role.strip()),
        _line(("Round: ", True), str(query.ordinal or "Not numbered"), " · ", ("Interviewer: ", True), who),
        _line(("Stage: ", True), ("🟡 Scheduling Pending" if pending else "Scheduled", True), " · ", ("Interview outcome: ", True), "Not verified"),
        _line(("When/where: ", True), "Not scheduled · no Calendar event yet; no time is invented." if pending
              else f"{query.interview_date} · Calendar owns the confirmed time and joining details."),
        _line(("Next action: ", True), "Confirm an interview time with the recruiter." if pending else "Prepare for this round."),
        _line(("Evidence: ", True), *request, f" · {ids}" if ids else ""),
        _line(("Opportunity parent ↗", False, parent_url))])
    side = [
        _callout("📅", "Logistics", [_line("Scheduling Pending" if pending else f"Interview date: {query.interview_date}"),
                                     _line("Calendar event: ", "none yet" if pending else "not verified"), _line("Meeting link: ", "none yet" if pending else "not verified")]),
        _callout("📼", "Transcript & Recording", [_line(("Granola acquisition: ", True), "NOT RUN · interview not yet held"),
                                                 _line(("Post-analysis gate: ", True), "complete verbatim transcript + exact interview identity + Notion read-back.")]),
        _callout("🔗", "Navigation", [_line(("Opportunity parent ↗", False, parent_url)), _line(("Hiring Pipeline ↗", False, pipeline_url))]),
        _callout("⚠️", "Evidence guardrails", [_line("Unknown meeting details remain unknown. Derived coaching cannot overwrite facts. "
                                                     "Machine runs never edit Live Notes or Raw Notes.")])]
    columns = {"object": "block", "type": "column_list", "column_list": {"children": [
        {"object": "block", "type": "column", "column": {"children": [brief]}},
        {"object": "block", "type": "column", "column": {"children": side}}]}}
    notes = [_para((label, True)) for label in ("Questions Asked", "What Landed", "What Needs Work", "Signals / Concerns",
                                                "Commitments I Made", "Follow-Up Owed", "Next Step / Timing")]
    return [_para(MARKERS["round"]), _para(identity), columns,
            _section("Prepare", [_para(("Status: ", True), "NOT RUN · waiting for a scheduled round." if pending else "NOT RUN."),
                                 _para("Game Theory and Jordan Belfort / Straight Line pre-interview analysis appears here once run; "
                                       "missing inputs stay unavailable and Live Notes / Raw Notes are never overwritten.")]),
            _section("Live Notes", notes),
            _section("After Interview", [_para(("Status: ", True), "NOT APPLICABLE YET · the interview has not been held." if pending
                                               else "BLOCKED until a complete verbatim transcript is verified.")]),
            _section("Raw Notes", [_para("Write unprocessed notes here. Machine runs never overwrite this section.")]),
            _section("Derived")]
