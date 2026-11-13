"""A shim that forwards the installed Close unit and release tool to lifeos.sources.apps.close_app."""
# LIFE-624 step 1. Removed in step 3, only after Oracle's unit and lifeos/edge/oracle/close_release.py point at lifeos.sources.apps.close_app (step 2, a runner pack).
# Do NOT remove before then: the live unit imports `handle` from here, and the release tool reads the literal CLOSURE below from this file (ast, never executed).
from lifeos.sources.apps.close_app import handle

__all__ = ["CLOSURE", "handle"]
CLOSURE = ("lifeos/hiring/close_screen.py", "lifeos/hiring/decision.py", "lifeos/hiring/models.py", "lifeos/jobs/__init__.py", "lifeos/jobs/hiring_pipeline.py", "lifeos/platform/names.py",
           "lifeos/sources/close_app.py", "lifeos/sources/apps/close_app.py", "lifeos/sources/apps/__init__.py", "lifeos/sources/apps/signed.py", "lifeos/sources/apps/outreach_app.py", "lifeos/recruiters/__init__.py", "lifeos/recruiters/outreach.py", "lifeos/recruiters/notion.py", "lifeos/recruiters/identity.py",
           "lifeos/network/__init__.py", "lifeos/network/surface.py", "lifeos/network/match.py", "lifeos/platform/hidden.py", "lifeos/platform/ledger_body.py",
           "lifeos/sources/apps/resume_app.py", "lifeos/platform/notion_files.py", "lifeos/platform/gdrive.py", "lifeos/platform/google_consent.py",
           "lifeos/sources/apps/manage_app.py", "lifeos/sources/apps/alerts_app.py", "lifeos/platform/alerts.py", "lifeos/platform/alert_registry.py", "lifeos/platform/alert_settings.py", "lifeos/platform/redact.py",
           "lifeos/platform/credentials.py", "lifeos/platform/credential_list.py", "lifeos/platform/fixed_code.py", "lifeos/platform/notion_text.py", "lifeos/platform/notion_pages.py")                                    # the same literal as lifeos.sources.apps.close_app.CLOSURE (a test holds them equal)
