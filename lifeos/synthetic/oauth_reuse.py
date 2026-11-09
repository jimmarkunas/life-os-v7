"""The reuse review for the four Google routes (LIFE-379 / GWT-T08-03): every actual current Google OAuth or service-account consumer found in this repository, who holds its
credential, and the duplicate or unsafe paths that stay NAMED GAPS. Read-only facts from the code and docs on the pinned revision; nothing here reads a credential, requests a new
Google project or refresh token, or changes any custody. Credential NAMES appear (never values), in lowercase.

`status` is verified_in_code, verified_in_docs, or custody_unverified (the code names a credential but this review did not see where it is stored)."""

REVISION = "4707aafcad310fdd0eb9a769e7c4bb58a4e29909"          # origin/main when this review was written; `blob` is each file's git blob id at that revision
NEW_GOOGLE_PROJECT_REQUESTED = False
NEW_REFRESH_TOKEN_REQUESTED = False
STATUSES = ("verified_in_code", "verified_in_docs", "custody_unverified")

CONSUMERS = [
    {"id": "v7_gmail_reader", "path": "lifeos/platform/gmail.py", "blob": "ceb25a6ca6f5b0c4b4a897adb6ef3f9a3ccab5c6", "kind": "oauth_refresh_token",
     "credentials": ["gmail_oauth_client_id", "gmail_oauth_client_secret", "gmail_oauth_refresh_token"],
     "custody_owner": "GitHub Actions secrets (the names are passed from secrets.* in .github/workflows/hourly.yml)", "custody_evidence": ".github/workflows/hourly.yml line 112; gmail.py lines 55-56",
     "supplied_by": ".github/workflows/hourly.yml", "supplied_by_blob": "984715130b827a8fd8b10dbe1b05d69ca15c97d9", "status": "verified_in_code"},
    {"id": "v7_drive_close_service", "path": "lifeos/platform/gdrive.py", "blob": "e535a837a30e000565c68b8ad290ea0ca8340de4", "kind": "oauth_refresh_token",
     "credentials": ["gdrive"], "custody_owner": "the Edge Close service's encrypted credential store on lifeos-edge-01 (one JSON with client id, client secret and refresh token; full drive scope)",
     "custody_evidence": "docs/DRIVE_RESUMES.md; gdrive.py module docstring", "supplied_by": "docs/DRIVE_RESUMES.md", "supplied_by_blob": "527e0001f60b0ccb49ed859d4a40a49ca0553fea", "status": "verified_in_docs"},
    {"id": "drive_consent_tool", "path": "lifeos/platform/google_consent.py", "blob": "0ac42088cab6f47e2d0ea8309007d5bda4980e01", "kind": "one_time_consent",
     "credentials": ["gdrive"], "custody_owner": "runs on Jim's Mac, pipes straight into the Edge credential store; reuses the existing LIFE OS Gmail Web OAuth client whose id and secret sit in Bitwarden Secrets Manager (project LIFE OS EDGE / OPENCLAW)",
     "custody_evidence": "docs/DRIVE_RESUMES.md; google_consent.py module docstring", "supplied_by": "docs/DRIVE_RESUMES.md", "supplied_by_blob": "527e0001f60b0ccb49ed859d4a40a49ca0553fea", "status": "verified_in_docs"},
    {"id": "openclaw_gmail", "path": "lifeos/edge/oracle/openclaw-gmail.mjs", "blob": "48dac829d4e0a5c0bc75e3b0f9bd8d8e25bbfad0", "kind": "oauth_refresh_token",
     "credentials": ["openclaw/gmail/clientId", "openclaw/gmail/clientSecret", "openclaw/gmail/refreshToken"],
     "custody_owner": "Bitwarden Secrets Manager, resolved at call time through OpenClaw's own bws SecretRef resolver on the Oracle host; nothing stored, copied or printed",
     "custody_evidence": "openclaw-gmail.mjs lines 4-5 and 29-33; docs/EDGE_GATEWAY_SCOPE.md", "supplied_by": "docs/EDGE_GATEWAY_SCOPE.md", "supplied_by_blob": "3cd068733e8ae5b9f98717d13240f314b0596cd2", "status": "verified_in_code"},
    {"id": "openclaw_drive", "path": "docs/EDGE_GATEWAY_SCOPE.md", "blob": "3cd068733e8ae5b9f98717d13240f314b0596cd2", "kind": "oauth_refresh_token",
     "credentials": ["openclaw/drive/refreshToken"], "custody_owner": "Bitwarden Secrets Manager (a separate read-only Drive grant, whole Drive); the script openclaw-drive.mjs is NOT in this repository",
     "custody_evidence": "docs/EDGE_GATEWAY_SCOPE.md table; docs/DRIVE_RESUMES.md line 6", "supplied_by": "docs/EDGE_GATEWAY_SCOPE.md", "supplied_by_blob": "3cd068733e8ae5b9f98717d13240f314b0596cd2", "status": "verified_in_docs"},
    {"id": "openclaw_calendar", "path": "docs/EDGE_GATEWAY_SCOPE.md", "blob": "3cd068733e8ae5b9f98717d13240f314b0596cd2", "kind": "unknown",
     "credentials": [], "custody_owner": "documented as an accepted read-only OpenClaw capability; the script openclaw-gcal.mjs is NOT in this repository and its credential was not seen",
     "custody_evidence": "docs/EDGE_GATEWAY_SCOPE.md table", "supplied_by": "docs/EDGE_GATEWAY_SCOPE.md", "supplied_by_blob": "3cd068733e8ae5b9f98717d13240f314b0596cd2", "status": "custody_unverified"},
    {"id": "v7_calendar_reader", "path": "lifeos/platform/gcal.py", "blob": "891e6eaad4475438fe7c1f71d863cd7ceb094361", "kind": "service_account",
     "credentials": ["gcal_service_account_json", "gcal_calendar_id"], "custody_owner": "named in gcal.py lines 62-63; where the value is stored was not seen in this review",
     "custody_evidence": "gcal.py lines 62-63", "supplied_by": None, "supplied_by_blob": None, "status": "custody_unverified"},
]

GAPS = [
    {"id": "gmail_credential_in_two_stores", "summary": "V7's Gmail reader takes its OAuth credential from GitHub Actions secrets while OpenClaw's Gmail command resolves its own from Bitwarden: two custody locations for Gmail access. Whether they are the same Google account and grant is not proven.",
     "owner": "Jim", "proof_needed": "Metadata-only read of both grants' account and scopes; no token value", "status": "named_gap"},
    {"id": "one_oauth_client_for_gmail_and_drive", "summary": "The existing LIFE OS Gmail Web OAuth client is reused by the Drive consent tool, so Gmail and Drive grants share one client.",
     "owner": "Jim", "proof_needed": "Read-only client and consent-screen metadata", "status": "named_gap"},
    {"id": "two_drive_grants", "summary": "Two live Drive grants exist: the Close service's full drive credential and OpenClaw's separate read-only grant. Which tenant each serves is not proven.",
     "owner": "Jim", "proof_needed": "Account and scope metadata for both grants; no token value", "status": "named_gap"},
    {"id": "no_tenant_b_consumer", "summary": "No consumer in this repository serves a second (tenant B) Gmail or Drive account. The tenant B routes exist only as fixtures; no real second account is claimed.",
     "owner": "Jim", "proof_needed": "A separately approved decision to connect a second account (not part of this ticket)", "status": "named_gap"},
    {"id": "openclaw_drive_and_calendar_code_off_repo", "summary": "openclaw-drive.mjs and openclaw-gcal.mjs are described in docs/EDGE_GATEWAY_SCOPE.md but are not in this repository, so they cannot be referenced by blob and their credential handling is unverified.",
     "owner": "Jim", "proof_needed": "The scripts' revision on the Oracle host, read-only", "status": "named_gap"},
    {"id": "calendar_credential_location_unseen", "summary": "gcal.py names a service account JSON and a calendar id; where those values are stored was not seen.",
     "owner": "Jim", "proof_needed": "The workflow or secrets reference that supplies them", "status": "named_gap"},
]
