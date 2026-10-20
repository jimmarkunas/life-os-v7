# Notion integrations (connections) — canonical map

What each Notion connection in Jim's workspace (The Austin Consulting…) is, which V7 credential holds its token, what uses it, and which Notion objects it must be
shared with. Read this before asking Jim to share a page: name the exact connection from this table, never "the integration whose token is X".

Sources: Jim's Notion **Settings → Connections** list (screenshot, Oct 20 2026) and the token consumers in `.github/workflows/*.yml` on main `5a6549b`. Notion
page and data-source ids for production objects stay secrets; they are named here, not quoted.

| Notion connection (as listed) | Created | V7 credential | Where the token lives | Used by | Must be shared with |
|---|---|---|---|---|---|
| **VS Code** (verified, note 1) | Mar 8 | `NOTION_API_TOKEN` | Bitwarden `53dc3d04-dbb2-45fe-8185-b4db015eedcb` | `hourly.yml` Jobs pipeline (publish, enrich, fit, audit, Network Leads blocks, Last Seen, retention, dispatch-only reports); `domains.yml` hiring (Ledger reads) and recruiters `job-page-outreach` (Ledger page tables); `network.yml` (connections CSV); `diag.yml` sandbox check | **Job Ledger** database; **LI Connection Database & Integration** page; the private report pages its dispatch-only reports write; for LIFE-10 §2 sandbox proof, **🧪 TEST — Job Ledger Apply UI · ExampleCo (SYNTHETIC)** (shared Oct 20 2026) |
| **LIFE OS Jira Card** | Oct 2 | `NOTION_JIRA_TOKEN` | Bitwarden `53f47cb9-f6bb-4cde-87f4-b4db015ef928` | every Daily Report card writer (Jira, Agenda/Calendar, Bills card, Amazon card, Mail Alerts, Hiring card, Attention card, Daily Command Center, Communications, Oracle Cloud); Attention; MegIBOW | **Daily LIFE OS Report** page; **Attention** data source; **MegIBOW** dashboard page and **MegIBOW Review** database |
| **LIFE OS Recruiters** | Oct 6 | `NOTION_RECRUITERS_TOKEN` | Bitwarden `b7b921fa-3f62-478a-a4f5-b4db015efcf7` | `recruiters.yml` and `domains.yml` recruiters (mail to Recruiters; Mark as Sent rows read by `job-page-outreach`) | **Recruiters** data source; **Job Ledger** database (note 2) |
| **LIFE OS Amazon** | Oct 3 | `NOTION_AMAZON_TOKEN` | Bitwarden `46fe195b-82e2-416e-8272-b4db015eea12` | `domains.yml` / `amazon.yml` Amazon Orders, Amazon card read, retention | **Amazon Orders** data source |
| **LIFE OS Bills** | Oct 3 | `NOTION_BILLS_TOKEN` | Bitwarden `815268ba-fa49-4461-afb3-b4db015ef189` | `bills.yml` / `domains.yml` Bills snapshot and Paid processor | **Bill Tracker** data source |
| **LIFE OS Interview OS** | Oct 1 | `NOTION_INTERVIEW_TOKEN` | Bitwarden `092b44ae-32cb-453b-8cc2-b4db015ef571` | `hourly.yml` interview stage, probe and acceptance; `domains.yml` hiring | **Hiring Pipeline** page (and only the Interview objects it owns); **Job Ledger** database, read only (note 3) |
| **LIFE OS Close (S2)** | Oct 16 | not in GitHub | root-only encrypted credential on the Oracle VM (D198) | the private Close / Mark as Sent service | **Job Ledger** database and **Hiring Pipeline** page (D198); **Recruiters** data source (D221 Mark as Sent creates its rows; proven by the Oct 20 2026 canary row) |
| **VS Code Test** | Mar 8 | none | OAuth app, any workspace | not used by V7 | nothing for V7 |

**Note 1 — VS Code is `NOTION_API_TOKEN`, verified.** On Oct 20 2026 the read-only `diag` check `notion-sandbox-access` with `NOTION_API_TOKEN` got 404 on all
three sandbox reads (run 39367878512); Jim then connected only **VS Code** to the sandbox page and the same check got 200 on all three (run 39368717318).

**Note 2 — LIFE OS Recruiters needs the Job Ledger too (LIFE-10, Oct 20 2026).** `job-page-outreach` reads Recruiters with this connection and filters on the `Job`
relation, which points into the Job Ledger. Notion hides relation values that point into a database the connection cannot see, so without the Ledger share every
Mark as Sent row looked unlinked and the stage reported `jobs=0` (runs 39363196870 and 39371532312, LIFE-10 comment 10802). Jim connected LIFE OS Recruiters to the
LIFE OS Job Ledger; the next dry run saw both rows (run 39372809707, comment 10804) and the live run wrote both tables (run 39374802877, comment 10811).

**Note 3 — LIFE OS Interview OS reads the Job Ledger (LIFE-42 D231, Oct 20 2026).** The manual `interview-acceptance` stage confirms a
Scheduling Pending request's exact job by reading the Ledger rows for that company (Company, Job, Stable Job Key) with this connection.
It never writes the Ledger. Jim connected LIFE OS Interview OS to the LIFE OS Job Ledger on Oct 20 2026.

## Rules

- Share each page or database only with the connection in its row. Never add a production page to a second connection to work around a 404.
- A 404 `object_not_found` with a valid token usually means "not shared with this connection", but a wrong or deleted id gives the same 404 (Tech Lead review
  5580763064). Before changing any access, confirm the exact object id (and that it is not in trash), then fix it with the one share in this table.
- A relation filter or relation value read through a connection that cannot see the related database comes back empty, not refused (note 2).
- Sharing a page shares its children; a top-level private page inherits nothing.
- A new V7 consumer of Notion reuses a connection from this table or gets Jim's explicit approval for a new one, and this table is updated in the same change.
