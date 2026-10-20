# Notion integrations (connections) — canonical map

What each Notion connection in Jim's workspace (The Austin Consulting…) is, which V7 credential holds its token, what uses it, and which Notion objects it must be
shared with. Read this before asking Jim to share a page: name the exact connection from this table, never "the integration whose token is X".

Sources: Jim's Notion **Settings → Connections** list (screenshot, Oct 20 2026) and the token consumers in `.github/workflows/*.yml` on main `5a6549b`. Notion
page and data-source ids for production objects stay secrets; they are named here, not quoted.

| Notion connection (as listed) | Created | V7 credential | Where the token lives | Used by | Must be shared with |
|---|---|---|---|---|---|
| **VS Code** (verified, note 1) | Mar 8 | `NOTION_API_TOKEN` | Bitwarden `53dc3d04-dbb2-45fe-8185-b4db015eedcb` | `hourly.yml` Jobs pipeline (publish, enrich, fit, audit, Network Leads blocks, Last Seen, retention, dispatch-only reports); `domains.yml` hiring (Ledger reads) and recruiters `job-page-outreach` (Ledger page tables); `network.yml` (connections CSV); `diag.yml` sandbox check | **Job Ledger** database; **LI Connection Database & Integration** page; the private report pages its dispatch-only reports write; for LIFE-10 §2 sandbox proof, **🧪 TEST — Job Ledger Apply UI · ExampleCo (SYNTHETIC)** (shared Oct 20 2026) |
| **LIFE OS Jira Card** | Oct 2 | `NOTION_JIRA_TOKEN` | Bitwarden `53f47cb9-f6bb-4cde-87f4-b4db015ef928` | every Daily Report card writer (Jira, Agenda/Calendar, Bills card, Amazon card, Mail Alerts, Hiring card, Attention card, Daily Command Center, Communications, Oracle Cloud); Attention; MegIBOW | **Daily LIFE OS Report** page; **Attention** data source; **MegIBOW** dashboard page and **MegIBOW Review** database |
| **LIFE OS Recruiters** | Oct 6 | `NOTION_RECRUITERS_TOKEN` | Bitwarden `b7b921fa-3f62-478a-a4f5-b4db015efcf7` | `recruiters.yml` and `domains.yml` recruiters (mail to Recruiters; Mark as Sent rows read by `job-page-outreach`) | **Recruiters** data source |
| **LIFE OS Amazon** | Oct 3 | `NOTION_AMAZON_TOKEN` | Bitwarden `46fe195b-82e2-416e-8272-b4db015eea12` | `domains.yml` / `amazon.yml` Amazon Orders, Amazon card read, retention | **Amazon Orders** data source |
| **LIFE OS Bills** | Oct 3 | `NOTION_BILLS_TOKEN` | Bitwarden `815268ba-fa49-4461-afb3-b4db015ef189` | `bills.yml` / `domains.yml` Bills snapshot and Paid processor | **Bill Tracker** data source |
| **LIFE OS Interview OS** | Oct 1 | `NOTION_INTERVIEW_TOKEN` | Bitwarden `092b44ae-32cb-453b-8cc2-b4db015ef571` | `hourly.yml` interview stage, probe and acceptance; `domains.yml` hiring | **Hiring Pipeline** page (and only the Interview objects it owns) |
| **LIFE OS Close (S2)** | Oct 16 | not in GitHub | root-only encrypted credential on the Oracle VM (D198) | the private Close / Mark as Sent service | **Job Ledger** database and **Hiring Pipeline** page (D198) |
| **VS Code Test** | Mar 8 | none | OAuth app, any workspace | not used by V7 | nothing for V7 |

**Note 1 — VS Code is `NOTION_API_TOKEN`, verified.** On Oct 20 2026 the read-only `diag` check `notion-sandbox-access` with `NOTION_API_TOKEN` got 404 on all
three sandbox reads (run 39367878512); Jim then connected only **VS Code** to the sandbox page and the same check got 200 on all three (run 39368717318).

## Rules

- Share each page or database only with the connection in its row. Never add a production page to a second connection to work around a 404.
- A 404 `object_not_found` with a valid token means "not shared with this connection", not "missing token". Fix it with the one share in this table.
- Sharing a page shares its children; a top-level private page inherits nothing.
- A new V7 consumer of Notion reuses a connection from this table or gets Jim's explicit approval for a new one, and this table is updated in the same change.
