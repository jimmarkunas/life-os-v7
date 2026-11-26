"""Does a protected human pursuit exist for this job? Retention asks here and never reads a Notion stage field.

The only human-owned facts Jobs can read are Applied and a Saturn Decision.
  Saturn Decision set        -> PROTECTED
  Applied, no other evidence -> UNKNOWN (retention fails closed)
  neither                    -> NOT_PROTECTED
Legacy Lifecycle / Liveness values never decide this; the Interview OS handoff protects a job in
hiring_pipeline.handoff_for.
"""
PROTECTED, NOT_PROTECTED, UNKNOWN = "PROTECTED", "NOT_PROTECTED", "UNKNOWN"


def resolve(applied, saturn_decision=None):
    if saturn_decision:
        return PROTECTED
    return UNKNOWN if applied else NOT_PROTECTED
