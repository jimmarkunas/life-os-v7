# Edge gateway (Oracle VM + OpenClaw): scope and roadmap (scoping only, no code, nothing provisioned)

Status: scoped for Jim's review. Nothing here is built or provisioned, and this document changes no schema, secret, workflow, test or `docs/DECISIONS.md`. It treats the Oracle VM as **a new deployment target for the OpenClaw boundary that the Platform Canon already describes**, not as "LIFE OS in the cloud".

**Update, October 4, 2026 (Jim):** the Oracle setup is **stalled** because the account had to be upgraded; the VM is not yet created. Decision 2 below is therefore made in effect (the account is moving to Pay As You Go), and EDGE-0 cannot start until the upgrade completes and the VM can be launched. Two things are worth checking once the upgrade lands, both unverified: that the upgrade has fully taken effect (a freshly upgraded account can still show free-tier limits or launch errors for a while), and that the A1 shape is offered in the Chicago region (capacity). The Product Backlog row `OPENCLAW-0` now carries one line recording the edge VM as the gateway's always-on home, at Jim's request.

Read first, as the brief asked: the Platform Canon, the Communications Intelligence canon (OpenClaw boundary), the Product Backlog, the Development Policy and Production Contract (only the scheduler and execution rules that touch this), Development Projects, and the current V7 repository.

## 0. In one paragraph

The edge VM is an always-on place for OpenClaw to run: it holds a Telegram connection and, later, other channel transports, answers a few bounded questions, and shows LIFE OS state that V7 has already produced. V7 stays the only owner of business rules, permissions, canonical state and scheduled work. The smallest honest first proof is **Telegram message from Jim, to OpenClaw on the VM, to a read-only look at the cards V7 already writes to the Daily Report, to a reply that carries each card's own freshness.** That needs no new V7 code, no new V7 table, no scheduler and no write permission anywhere, and it can show all four invariants (no second scheduler, no second datastore, no source mutation, domain permissions still in charge) by test.

## 1. Boundary (the established one, restated)

| The edge VM and OpenClaw may own | V7 (and the source systems) stay authoritative for |
|---|---|
| Transport (Telegram first), session mechanics, lightweight routing, bounded tool calls, edge health and logs | Business rules, permissions, domain routing, canonical persistence, durable state, scheduled execution |

Never at the edge: a second scheduler, a second workflow engine, a second datastore, a universal message database, duplicate task or contact storage, or any consequential write. Small and local models may classify, summarize, extract, route, answer read-only questions and draft; they never receive broad mutation access to Jira, Calendar, mail, Notion canonical state or finances (Backlog, Platform Canon, Communications canon).

Two places where the canon's wording needs reading carefully:
- The Communications canon says "`LIFE OS Daily Runs` remains the sole recurring scheduler" in its OpenClaw paragraph. With the approved scheduler scopes (decision D133, production contract 2.15.12 on its branch) that sentence means the ChatGPT scope. OpenClaw owns no recurring scheduling in either scope, and any scheduling or automation feature inside OpenClaw stays switched off (to verify in EDGE-0).
- The Platform Canon requires approval for a new "per-domain technology stack". A Node service on an Oracle VM is one; Jim's brief is the approval, and its external-component rule applies (section 3).

## 2. Where it sits in the roadmap, and the gate

The Product Backlog rows are `OPENCLAW-0` (feasibility: one bounded communications workflow plus one local-model workflow; "no scheduler, datastore, canonical-state or business-policy ownership") and `OPENCLAW-1` (Telegram remote control, bounded LIFE OS tool invocation). Row `OPENCLAW-0` is gated on `J1C` being accepted, and Development Projects (as last edited September 28) shows the Jobs mutation train still active. The brief's Oracle VM, Telegram proof and read-only query are, in effect, the first half of `OPENCLAW-0`.

How I propose to reconcile that without breaking the gate: **EDGE-0 (infrastructure only, no access to anything in LIFE OS) is non-overlapping infrastructure** (Development Projects allows that) and can start now; **EDGE-1 (the first LIFE OS read) waits for Jim to say "prioritize OPENCLAW-0"**, because the Backlog says to promote an item only when he explicitly prioritizes it. If he would rather not split it, say so and both wait.

## 3. What is known, and how well (second-hand until EDGE-0)

I delegated the web research and read the report; this session's network blocks Oracle, Telegram, GitHub docs and Cloudflare pages, so only raw GitHub files were read directly. VERIFIED means the OpenClaw repository itself or an official page supports it; UNVERIFIED means a search summary, press piece or forum only.

**OpenClaw** (repository `openclaw/openclaw`; Backlog already lists it as the approved donor candidate)
- MIT license (copyright OpenClaw Foundation). VERIFIED. The Platform Canon's reuse rule (pin the exact version and license; GPL or AGPL is reference-only) is satisfied on license; the version to pin is chosen in EDGE-0.
- Node.js; needs Node 24.16 or later (22, 23 and 25 are unsupported). Linux ARM64 is supported, and the repository carries an Oracle A1.Flex guide for Ubuntu 24.04 aarch64 that opens only the Tailscale UDP port. VERIFIED. Practical memory about 2 GB (third-party estimate, UNVERIFIED); 12 GB is ample.
- Telegram: long polling is the default and needs no inbound port; webhooks are optional and need public HTTPS. Access control by numeric user id (`dmPolicy` allowlist plus `allowFrom`), with groups separately controlled. VERIFIED.
- Tools are typed functions with allow and deny lists, and **deny wins**. The documented read-only agent allows only `read` and denies write, edit, patch, exec, process and browser, with the workspace mounted read-only. VERIFIED.
- Models: Ollama through its native API (a `/v1` URL breaks tool calling) and hosted providers, with primary and fallback routing in config. VERIFIED. That fits "Macs for inference, VM stays light".
- Config is a JSON5 file with `${VAR}` substitution; the Telegram token comes from an environment variable or file; conversation transcripts and credentials are stored on disk under the OpenClaw state directory, which the docs say may contain secrets (permissions 700 and 600). VERIFIED.
- **Security posture to plan around:** the sandbox is **off by default**, default command execution runs **on the host**, plugins run in-process with full privileges, the docs call the sandbox "not a perfect security boundary", the security model is "one trusted operator per gateway", and the advisory list is long (the latest from September 2026 includes two rated High). VERIFIED for the first four, advisory list VERIFIED, older individual CVEs UNVERIFIED.

**Oracle Cloud**
- Oracle's own free-tier README still says Ampere A1 allows 4 cores and 24 GB. A July 2026 press report says Oracle cut it to **2 cores and 12 GB** with enforcement in August, with over-limit instances disabled and later deleted unless the account is upgraded. UNVERIFIED. **Jim's planned VM (2 OCPU, 12 GB) is exactly inside the lower figure**, so plan for it.
- Idle reclamation: per search summaries of Oracle's page, an Always Free instance is treated as idle when over 7 days its CPU, network and (on A1) memory use are all below 20 percent, and idle instances can be stopped; upgrading to Pay As You Go is the reported way to avoid it. UNVERIFIED. **A quiet gateway fits that description**, so this is a real operating risk.
- Chicago A1 "out of host capacity" is a common forum complaint; Oracle's advice is to retry or try another availability or fault domain. UNVERIFIED, no frequency data.
- Free allowances reported (VERIFIED in Oracle's README, possibly stale): 10 TB a month outbound, 200 GB block volume with 5 backups, a Vault with 150 secrets, 5 Bastions.

**Reaching the VM from GitHub Actions without opening port 22 to the internet.** GitHub-hosted runner address ranges run to thousands of entries and change weekly, so allowlisting them is not workable. Options, all of which avoid a public SSH port: Tailscale with an ephemeral key in the workflow (a maintained action; the VM runs Tailscale; this is what OpenClaw's own Oracle guide uses), the OCI Bastion service (sessions up to 3 hours; the runner needs OCI credentials), Cloudflare Tunnel and Access (needs a Cloudflare-managed domain), or pull-based deployment (the VM fetches releases, so no inbound path and no credentials in CI). UNVERIFIED for the Bastion and Cloudflare details.

**Telegram:** long polling is outbound-only; webhooks need ports 443, 80, 88 or 8443 over HTTPS; there is no platform-level private bot, so restriction is a numeric user-id allowlist in code or config. Long polling is the default here, so the VM needs no public inbound port at all.

**GitHub secrets on a public repository.** A repository secret is readable by any workflow in the repository (fork pull requests get none). An environment secret goes only to jobs that name that environment, after its protection rules (up to six required reviewers, branch limits) pass, and environments work in public repositories. UNVERIFIED (docs blocked).

## 4. How the edge reaches LIFE OS: the one real design question

V7 is a set of batch jobs on GitHub Actions. There is no always-on V7 service for an edge to call. Three shapes, smallest first:

| Shape | What happens | New V7 surface | Verdict |
|---|---|---|---|
| **1. Read V7's presentation surfaces** | The edge reads the cards V7 already writes to the Daily Report (calendar, bills, Jira, Amazon; later others) through a **dedicated read-only Notion integration** shared with only those pages. Each card heading already carries its own update time or STALE or DEGRADED label (D119, D121). | None in `lifeos/`. A new Notion integration, its page shares, a token that lives only on the VM. | **Use for EDGE-1.** No new datastore, scheduler or write path; answers are exactly as fresh as V7's last tick and say so. |
| **2. Ask V7 to run a read-only query** | The edge starts a V7 workflow (an Actions-only token, as the outside timer does under D31) that runs a fixed, allowlisted query against Hostinger and returns the answer through a private channel. | A new manual workflow, a fixed query list, and a return path (a Notion page or one mailbox row; never a public issue comment, since the repository is public). | Only when a question cannot be answered from the cards. Not before there is one. Needs its own decision. |
| **3. Run V7 code on the VM** | The VM holds database credentials and runs V7 read-only. | A second runtime and a second place holding secrets. | **Reject.** It breaks V7's isolated-secret model and the "V7 runs on Actions" rule. |

Consequential actions (mark a bill paid, move a job, accept an invite) are never taken at the edge in any shape. When they come, they go through a V7 domain job that holds the permission, and the edge only asks.

## 5. Authorization and safety model for the first proof

- **Who:** Telegram direct messages only, from one numeric user id (allowlist, `dmPolicy`), groups disabled. The id and the bot token live on the VM only, never in the repository.
- **What the gateway may call:** one read tool over an allowlist of page ids, and nothing else: no command execution, no browser, no file write, no web search. Tool policy denies by default, with the sandbox turned on (it is off by default, and the docs say commands run on the host otherwise).
- **No source mutation, provably:** the Notion integration has read-content capability only, and EDGE-1's acceptance includes attempting a write and seeing it refused.
- **Domain permissions still govern:** any message asking for a change gets a fixed refusal that names the V7 path. No model decides that.
- **No model needed for the first proof.** `/today`, `/bills`, `/jira`, `/calendar` return the card text and its freshness deterministically. A local model on a Mac (over Tailscale, when the Mac is on) can summarize the same read-only text later (EDGE-4); if the Mac is off the deterministic answer still works (the D54 spirit: unavailable-safe).
- **Data at rest on the VM:** OpenClaw keeps transcripts and credentials on disk. Transcripts would contain card text, so retention is short and bounded, permissions are 700 and 600, and the state directory is disposable except for configuration, which lives in the repository as code. No LIFE OS data is stored beyond that.
- **Pinned and watched:** pin the exact OpenClaw version, review it before first use, and watch its advisories; given the volume, an update is routine maintenance, not an exception.

## 6. Smallest infrastructure and V7 surfaces

| Surface | Where | Notes |
|---|---|---|
| The VM | Oracle Cloud, Chicago region, Ubuntu 24.04, 2 OCPU and 12 GB (A1) | Reclamation, capacity and the possible allowance cut are the open operating risks (decisions 2 and 3). |
| Admin and deploy access | Tailscale (recommended) or OCI Bastion; public SSH closed | Jim's plan was to tighten SSH ingress; since GitHub runners cannot be allowlisted, "tighten" ends at "closed", and access moves to a private path. |
| Edge configuration as code | `infra/edge/` in this repository (OpenClaw config without secrets, a systemd unit with restart-always, a bootstrap script, a short runbook) and a section in `docs/SETUP.md` | Public repository: no addresses, ids, bot name or tokens. The VM name `lifeos-edge-01` is fine. |
| Deploy workflow | A new manual-only `edge-deploy.yml`, no schedule, no `lifeos.run`, using the private path above | Needs a contract test that pins it to manual dispatch and to the one secret. `hourly.yml` gains nothing and `domains.yml` is untouched. |
| The SSH key secret | `LIFEOS_EDGE_SSH_PRIVATE_KEY` is now a **repository** secret | Recommend moving it to a GitHub **Environment** named for the edge, with Jim as required reviewer and the main branch as the only allowed branch, **before** any workflow references it. As a repository secret it is readable by any workflow in the repository, including one on a pushed branch. Today nothing references it, so it is inert. |
| Notion access | A new read-only integration shared with the Daily Report page only | Its token lives on the VM only. |
| Health | Systemd restart and failure push through the existing push channel; optional dead-man check (EDGE-2) | See below. |
| **Not needed in EDGE-0 or EDGE-1** | Any `lifeos/` code, `hourly.yml` input, new V7 table, new V7 job, new Hostinger access | |

**Health, and the one decision it needs.** A dead VM cannot report itself. The smallest way to notice is a dead-man's switch: the VM sends a small "alive" message to a push topic on a schedule, and the existing alarm-only watchdog (D52; no secrets beyond the topic, `issues: write` only) raises the alert if the last message is too old. That extends D52's watchdog to a second thing, so it is a decision, not a default; the alternative is a free external monitor, with V7 uninvolved. A heartbeat timer on the VM is host maintenance, not a LIFE OS scheduler, and the test for "no second scheduler" says so explicitly.

## 7. Roadmap

| Slice | Outcome | What changes | Schema | Proof | Gate | Size |
|---|---|---|---|---|---|---|
| **EDGE-0 Host and a silent gateway** | VM running, locked down, OpenClaw (pinned) answering `/ping` and nothing else, only to Jim | `infra/edge/` files, `docs/SETUP.md` section, an optional deploy workflow with its contract test; Jim does the Oracle and Tailscale console steps | none | No public inbound port; password logins off; survives a reboot; a service failure pushes a message; a non-allowlisted account gets no reply; no secret in the repository; tool list is empty; OpenClaw's own scheduling features are off; **no access to anything in LIFE OS** | Jim's decisions 2 to 4; non-overlapping infrastructure | M |
| **EDGE-1 Read-only LIFE OS query** | `/today`, `/bills`, `/jira`, `/calendar` return the card text with its freshness, DEGRADED or STALE label included | The read-only Notion integration and shares; one read tool in the OpenClaw config; a short test script | none | The four invariants by test: (1) no second scheduler (the VM's timers list holds only host maintenance and the heartbeat); (2) no second datastore (state directory holds configuration, pairing and short, expiring transcripts only); (3) no mutation (a write attempt with the token is refused); (4) permissions (a non-allowlisted user, and a request to change something, both get nothing or a fixed refusal) | **Jim says "prioritize OPENCLAW-0"** | M |
| **EDGE-2 Health** | A silent VM is noticed within a set time | A heartbeat timer and either a watchdog extension or an external monitor | none | Stop the service and see the alert; restart and see it clear | Decision 6 | S |
| **EDGE-3 On-demand V7 query (only if needed)** | A question the cards cannot answer is answered from Hostinger, privately | A manual V7 workflow, a fixed query list, a private return path | possibly one tiny mailbox row | Only the listed queries run; nothing public carries a result | A concrete unanswerable question, and its own decision | M to L |
| **EDGE-4 Local-model assist** | Summaries or classification of the same read-only text by a model on a Mac | OpenClaw model config pointing at the Mac over the private network, with deterministic fallback | none | Mac off still gives the deterministic answer; model output never widens tool access | EDGE-1 | S to M |
| **Other channels (later, one at a time)** | Messages, WhatsApp, Discord, FaceTime attention, each proven on its own | Outside V7 until a design exists for crossing the Mac-to-GitHub boundary (Communications canon) | none | per channel | Backlog rows `OPENCLAW-1`, `COMM-1` | per channel |

First slice block:

```text
FIRST BUILD SLICE:
EDGE-0 host and a silent gateway (infrastructure only)

WHY FIRST:
It is non-overlapping infrastructure, needs no V7 code, no schema and no access to LIFE OS, and it settles the questions that decide everything after it: whether Oracle will give and keep the VM, how admin and deploy access work with the port closed, and whether OpenClaw runs cleanly and safely on ARM64 with the sandbox on.

EXPECTED FILES:
infra/edge/* (config without secrets, a systemd unit, a bootstrap script, a runbook), a docs/SETUP.md section, and, if a deploy workflow is wanted, .github/workflows/edge-deploy.yml with a contract test

SCHEMA:
NONE

ACCEPTANCE:
The EDGE-0 proof list in section 7, run and reported by count and fixed codes only.

DO NOT BUILD YET:
Any LIFE OS read, the Notion integration, any model, any other channel, any write path, any query workflow, any V7 job or table.
```

## 8. Not verified, and surprises

- **Oracle:** the free-tier allowance (4 and 24, or 2 and 12), the idle rule's exact thresholds, whether upgrading exempts an instance, and Chicago capacity are all from summaries, press or forums. Read the live Oracle pages at the start of EDGE-0 before committing to the free tier.
- **OpenClaw:** the repository's own files were read, but the product was not run. The read-only recipe and Telegram allowlist are documented, not tested; whether any built-in scheduling or automation feature exists, and how to switch it off, is unchecked. The sandbox's real strength is unmeasured.
- **GitHub and Telegram documentation pages** were blocked from this session; those facts rest on the research report's summaries and the OpenClaw docs.
- **The VM, VCN and key** were described by Jim; I did not see or touch them. I have not read or printed the SSH key secret and never will.
- **The Notion read-only integration** was reasoned from Notion's integration model and the existing Daily Report card pattern, not tried here.
- **Surprises worth knowing:** the free allowance may have been halved to exactly what Jim planned; a quiet gateway looks idle to Oracle; OpenClaw needs a newer Node than Ubuntu's own package provides and its safety is opt-in (sandbox, deny lists); a repository secret in a public repository is wider-reachable than it feels.

## 9. Decisions Jim must make

1. **Priority: settled (Jim, October 5).** Start EDGE-0 as non-overlapping infrastructure as soon as the VM exists, and hold EDGE-1 until he says "prioritize OPENCLAW-0".
2. **Oracle account type: upgrading (Jim, October 4).** Pay As You Go with a small budget alert, so the VM is not stopped for idleness or disabled if the free allowance changes; resources stay within the free allowances. The upgrade is what has stalled the setup.
3. **Admin and deploy access: settled (Jim, October 5).** Tailscale with port 22 closed, and the SSH key moved into a GitHub Environment with him as required reviewer before any workflow uses it.
4. **Read path for EDGE-1.** A dedicated read-only Notion integration shared with the Daily Report page only (recommended), rather than any path into Hostinger.
5. **First model.** None in EDGE-1; a local model on a Mac only in EDGE-4 (recommended).
6. **Health.** A heartbeat checked by extending the existing alarm-only watchdog (a small amendment to D52), or an external monitor with V7 uninvolved.
7. **Notion records: done.** One line was added to the `OPENCLAW-0` row of the Product Backlog (October 4, 2026), recording the edge VM, its stalled status and this scope.
