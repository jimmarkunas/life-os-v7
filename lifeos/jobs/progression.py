"""Does a protected human pursuit exist for this job? Retention asks here and never reads a Notion stage field.

v0 (until the Interview OS progression handoff, INT-7.1A, exists): the only human-owned facts Jobs can read are Applied and a Saturn Decision.
  Saturn Decision set        -> PROTECTED (a human decision exists)
  Applied, no other evidence -> UNKNOWN   (Jobs cannot prove there is no recruiter or interview progression; retention fails closed)
  neither                    -> NOT_PROTECTED (no pursuit has started)
Legacy Lifecycle / Liveness values never decide this. When INT-7.1A lands, its evidence replaces the Applied rule here and nothing else changes.
"""
PROTECTED, NOT_PROTECTED, UNKNOWN = "PROTECTED", "NOT_PROTECTED", "UNKNOWN"


def resolve(applied, saturn_decision=None, interview_evidence=None):
    """interview_evidence: reserved for INT-7.1A: True (protected progression), False (proven none) or None (no handoff yet)."""
    if saturn_decision or interview_evidence is True:
        return PROTECTED
    if applied:
        return NOT_PROTECTED if interview_evidence is False else UNKNOWN
    return NOT_PROTECTED
