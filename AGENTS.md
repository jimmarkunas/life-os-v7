# LIFE OS V7 — agent guide (read this, not the whole repo)

V7 is the active LIFE OS implementation. V1 (`life-os-automation`) and V2 (`life-os-v2`) are read-only references: extract the smallest proven mechanic only.
Core rule (D54): V7 must run correctly with ChatGPT completely unavailable.

## Map
- **Start with `docs/CODEMAP.md` (or `docs/codemap.json`)**: a generated index of every stage, module (with its one-line purpose), workflow, secret name, database table and decision. It is rebuilt by `python -m lifeos.codemap`, and a test fails when it is stale, so trust it before browsing. Find the file there, then read only that file.
- `lifeos/platform/` shared clients and primitives (Notion, Gmail, Outlook, Graph tokens, Jira, Google Calendar, db, alerts, gate, router, `rest.py` retry helper). Imports nothing from other `lifeos` layers.
- `lifeos/jobs/`, `lifeos/interview/`, `lifeos/jira/`, `lifeos/outlook/`, `lifeos/calendar_bridge/` — one OS per package; may import `platform` only.
- `lifeos/sources/` producers (newsletters, web, openjobs, sponsor register); may import any OS.
- `lifeos/run.py` stage registry: one `lazy("module", "function")` line per stage. `.github/workflows/hourly.yml` runs stages; `workflow_dispatch` is at the 25-input limit: add no input.
- `tests/` mirrors `lifeos/`. Shared fakes live in `tests/kit` (add new fakes there). `tests/contracts/` enforce the rules below.
- Docs: `docs/DECISIONS_INDEX.md` (find a decision), `docs/DECISIONS.md` (grep, never read whole), `docs/SETUP.md` (secrets and manual steps), `docs/ROADMAP.md`.

## Commands
- All tests: `python -m unittest discover -s tests -t .` (about 2 s). A run is good only if it prints a line starting `OK`.
- One file: `python -m unittest tests.jira.test_jira`.
- `git add` new files BEFORE running the suite: the privacy test checks committed/staged files only.

## Rules (each is enforced by a test)
- Layers: platform imports nothing; OS packages import only platform; sources may import any OS.
- Public repo: no secrets, names, addresses or message content in code, tests, logs or errors. Use example.com and invented names. Output is counts and fixed codes only.
- No sending or deleting mail or events (tests forbid DELETE, /send, sendMail, /forward, permanentDelete). Moves and writes are read back.
- Fail closed: a full result or an error, never partial. A dry run is the default; writes need live.
- Isolated jobs (Interview, Jira, Outlook) blank every workflow-level secret they do not use.
- Keep diffs small. No new framework or dependency without asking. Stop and report on a schema change.

## Reporting
Counts only. Say what you could not verify (real data, live runs). One PR per task; do not merge.
