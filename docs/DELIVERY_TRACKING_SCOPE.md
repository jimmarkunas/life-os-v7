# Delivery tracking: scope and roadmap (scoping only, no code)

Status: proposal for Jim's review. Nothing here is built. No schema, secret, workflow or `DECISIONS.md` change is made by this document.
Question answered: can Amazon Orders grow into "all deliveries" (FedEx, UPS, DHL and others) by reading Gmail and Outlook, capturing the tracking number, and following it to its conclusion?

Settled by Jim since the first draft: **USPS is in scope; Jim holds active UPS and FedEx accounts** (so production credentials are available for both); and **he receives few tracking numbers**. Low volume changes two things below: the schema starts at two tables, and the first slice's dry-run count of real tracking numbers is the go/no-go for building the store and card (D2).

## 0. Answer in one paragraph

Yes, and it fits V7 without a new scheduler, datastore engine or paid service. It is a different problem from Amazon Orders, though, and should not be built as an extension of `lifeos/amazon/`. Amazon works because three exact sender addresses carry one order id each, so a sender allowlist is a complete mailbox filter. "All deliveries" has no allowlist: tracking numbers arrive from any merchant, so the extractor (a pure function with checksums) becomes the precision gate, and the carrier's own API becomes the authority on state. The new pieces are (1) tracking-number extraction and validation, (2) carrier status clients for UPS, DHL, USPS and FedEx, (3) a small Hostinger state store with a due-time polling rule, and (4) a card region. Everything else reuses what Amazon and Bills already proved.

Three findings from a read-only, counts-only look at the real mailboxes change the shape of the work:

1. **Carriers do not email Jim.** Over 180 days, Gmail held no messages from UPS, FedEx, DHL or USPS domains, and the connected Outlook mailbox held none either. So "parse the carrier's delivered email" is not a usable path. Tracking numbers have to come from merchant shipping emails, and state has to come from carrier APIs.
2. **Keyword discovery is noisy.** A 365-day Gmail search on shipping phrases returned about 200 threads and the first 50 were almost all non-shipping mail (marketing, recruiters, notifications). Outlook returned nothing. A mail query can bound the window, but only the extractor can decide what is a tracking number.
3. **The mail-forwarding vendor never sent a tracking-number email.** Its "completed action" notices are present, but no tracking-number message from that vendor exists in 365 days of Gmail or in Outlook. The Physical Mail handoff assumes one exists. That assumption is unproven and is the biggest risk to that feature (section 14).

## 1. Product definition

Deliveries answers one question: **which packages are on their way to me, where are they, and which have actually arrived?** It removes a package from view only on carrier-proven delivery (or another explicit terminal state, section 8), never on age or silence.

In scope: discover tracking numbers in Gmail and Outlook mail; identify the carrier; poll the carrier to a terminal state; show open and recently delivered shipments in one Daily Report region; degrade visibly when a carrier or mailbox read fails.

Out of scope: sending, replying, filing or deleting mail; ordering anything; scraping carrier websites or driving a browser; a generic package-tracker platform; ChatGPT at runtime (D54); paid tracking services; push notification on every scan.

## 2. Current V7 map (what exists today)

| Concern | Where | State today |
|---|---|---|
| Amazon mail to order state | `lifeos/amazon/events.py`, `orders.py`, `stage.py` | Gmail only, three exact senders, one order id per message, monotone ORDERED/SHIPPED/DELIVERED. Never reads a tracking number or carrier. |
| Amazon presentation | `lifeos/amazon/card.py`, `lifeos/platform/report_region.py`, `router.AMAZON_REGION` | Reads Notion rows, writes one callout, digest-checks the other regions around the write. |
| Gmail client | `lifeos/platform/gmail.py` | `list_ids_complete` (bounded, fails on overflow), `message_record` (body as text), label calls. Holds `gmail.modify`; Deliveries only needs to read. |
| Outlook client | `lifeos/platform/outlook.py` | Read-only `Mail.Read` by default. `messages()` lists one folder (default Inbox) without bodies; `message_html()` fetches a body separately. Two accounts via `outlook_tokens`. |
| Retry and limits | `lifeos/platform/rest.py`, `limits.py`, `docs/LIMITS.md` | Reusable retry/backoff with fixed codes; provider limits canonized in one place with a "stop on first 429" rule. |
| Daily quota counting | `lifeos/platform/usage.py` (`v7_spend`) | TinyFish-specific, but the count-before-send pattern is the model. |
| Private storage | `lifeos/platform/db.py`, `snapshot_store.py`, `lifeos/jobs/store.py`, `lifeos/jira/store.py` | Hostinger MySQL through an SSH tunnel. Domain-owned tables are an established pattern; `ensure_schema` creates and migrates. |
| Alerts | `lifeos/platform/alerts.py` | Counts-only alerts with ntfy push; state in `v7_alerts`. No delivery detector yet. |
| Scheduling | `lifeos/run.py`, `.github/workflows/domains.yml` | One `lazy(...)` line per stage. Domain jobs run after each hourly tick and see only their own secrets (enforced by `tests/contracts/test_workflow.py`). `hourly.yml` is at the 25-input limit; `domains.yml` is not. |
| Carrier tracking | nowhere | Not in V7, V2 or V1. V1's Amazon contract says never to persist "tracking tokens". |

## 3. Gap analysis

| Capability | Status | Note |
|---|---|---|
| Gmail acquisition for shipping mail | PARTIAL | Client exists. Needs a broad, bounded, watermark query (D113 pattern) instead of a sender allowlist. |
| Outlook acquisition for shipping mail | PARTIAL | Client exists but lists Inbox only and has no body in the list. D113's lesson applies: the Inbox is not a boundary. |
| Provider-neutral mail record | MISSING | Gmail returns `body_text`; Outlook returns HTML via a second call. Recruiters, Hiring Pipeline and Physical Mail need the same thing. |
| Tracking-number extraction and carrier identification | MISSING | |
| Number validation (check digits) | MISSING | |
| Carrier status clients | MISSING | UPS, DHL, USPS, FedEx. |
| Status normalization and lifecycle | MISSING | |
| Shipment and event storage | MISSING | No table. Amazon rows hold no tracking fields. |
| Poll scheduling (due time, backoff) | MISSING | Web boards (`WEB_REFRESH_HOURS`, `WEB_RETRY_HOURS`) are the nearest pattern. |
| Carrier quota accounting | PARTIAL | Pattern exists in `usage.py`. |
| Terminal and stale handling | MISSING | |
| Card region | PARTIAL | `report_region.replace_text` is reusable; no Deliveries owner in `router.OWNERS`. |
| Alerts | PARTIAL | Layer exists; no detector. |
| Amazon linkage (order and shipment) | MISSING | |
| Tests and fakes | PARTIAL | `tests/kit/amazon.py` shows the style. |
| Workflow job | PARTIAL | `domains.yml` pattern; a new job needs its own `DOMAIN_SECRETS` entry. |

## 4. Evidence ownership

- **Mail owns discovery only.** A message can say "this number belongs to this shipment". It never decides state.
- **The carrier owns state.** Delivered, returned and exception come from the carrier API response.
- **Amazon's own mail keeps owning Amazon order state.** Amazon Logistics shipments (numbers beginning TBA) have no public carrier API; their terminal evidence stays Amazon's delivered email, so they are recorded as untrackable-by-API rather than polled.
- **Hostinger holds a derivative cache** (what was seen, when to poll next, what the carrier last said). It is not a source of truth; Gmail, Outlook and the carrier are.

## 5. Carrier source decision

Primary carrier pages were not reachable from this session's sandbox, so the facts below come from search results and are marked unverified. Phase D0 verifies each against the live portal before any number is canonized in `limits.py`.

| Source | Viable | What it gives | Auth and cost | Verdict |
|---|---|---|---|---|
| UPS Tracking API | Yes | Status and scan events by number | OAuth 2.0 client credentials; free developer account; Jim has an active UPS account | MVP |
| DHL Shipment Tracking (Unified) | Yes | Express, Parcel, eCommerce, Freight status | API key header; free developer registration, no carrier account needed; reported initial quota 250 calls/day and one call per 5 seconds, upgrade on request | MVP, last adapter |
| USPS Tracking API v3 | Yes | USPS and USPS-handed-off status | OAuth 2.0; free developer account; reported default around 60 requests/hour per app | MVP (added by Jim) |
| FedEx Track API | Yes | Status and scan events | OAuth 2.0; free; production credentials reportedly require a FedEx account added to the developer organization, and Jim has an active account | MVP |
| Amazon Logistics | No public API | none | none | Untrackable by API; keep Amazon's delivered mail |
| Carrier notification emails | Viable in principle | state corroboration | none | Not built: zero such mail exists in either mailbox today |
| Free tiers of aggregators (for example 17TRACK, AfterShip, Ship24) | Possible | many carriers behind one key | third-party account, free quota | Rejected for MVP; revisit only if a carrier gap matters |
| Scraping carrier pages or a headless browser | Possible | | | Rejected: brittle, terms risk, contradicts the free-only stance (D11, D13) |
| TinyFish | Possible | | paid wallet | Rejected (D11) |

Quota arithmetic, to be re-checked in D0: with about five open shipments polled every three hours, that is roughly 40 calls a day across all carriers, well under every reported ceiling. Jim reports few tracking numbers, so real volume will be lower still; a per-run cap and the stop-on-first-429 rule are enough at first, with no call-counter table.

## 6. Architecture and layering

```text
Gmail / Outlook (read only)
        |  bounded census, watermark + overlap (D113 pattern)
        v
provider-neutral message record  --->  seen table (message key -> outcome)
        |
        v
tracking extractor (pure)  ->  candidates: carrier?, number, evidence, check digit ok?
        |  accept / review / ignore
        v
shipment store (Hostinger)  <----  poll due shipments (<= 1 call each per tick)
        ^                                 |
        |                          carrier clients (platform, policy-free)
        |                                 v
        +------ normalized status + events, read back after every write
        |
        v
Deliveries card region  +  alerts (exception, credential failure)
```

Layering rules (enforced by `tests/contracts/test_boundaries.py`): platform imports nothing from other `lifeos` layers; OS packages import only platform; sources may import any OS.

- **`lifeos/platform/carriers/`**: policy-free carrier clients and number validators. `track(carrier, number) -> CarrierStatus`. This is the one shared surface other features reuse. Physical Mail can call it directly without importing the Deliveries package.
- **`lifeos/deliveries/`**: policy. Extraction, accept/review rules, due-time polling, lifecycle, store, card.
- **Provider-neutral mail record**: introduced inside this feature's first consumer slice, not as a standalone framework PR. It already has several consumers waiting (see `docs/BIG_FEATURES_ROADMAP.md`).

## 7. Contracts (architecture level)

```python
class Carrier(Enum):      UPS, FEDEX, DHL, USPS, AMAZON_LOGISTICS, UNKNOWN
class ShipmentState(Enum):
    PRE_TRANSIT, IN_TRANSIT, OUT_FOR_DELIVERY, AVAILABLE_FOR_PICKUP, EXCEPTION,
    DELIVERED, RETURNED,                       # carrier-proven terminals
    UNTRACKABLE, NOT_FOUND, STALE,             # bounded-failure terminals, surfaced once as "needs review"
    REVIEW                                     # ambiguous identity; never polled

@dataclass(frozen=True)
class TrackingCandidate:      carrier: Carrier | None; number: str; evidence: Evidence; check_ok: bool
@dataclass(frozen=True)
class CarrierStatus:          state: ShipmentState; code: str; occurred_at: datetime | None
                              eta: date | None; events: tuple[CarrierEvent, ...]
def extract(message) -> list[TrackingCandidate]                      # pure, never returns body text
def track(carrier, number, *, session) -> CarrierStatus             # platform; fixed error codes only
def next_poll(state, failures, now) -> datetime | None              # pure; None when terminal
def reconcile(existing, status, now) -> (row, change)               # pure, terminal states sticky
```

## 8. Extraction, lifecycle and closure

**Extraction (pure, fail closed).** Accept a candidate only when it passes a gate; everything else is counted and dropped or sent to REVIEW.

- A carrier tracking link whose host is the carrier's own, carrying the number in its query string: accept after the number's format is valid. Merchant click-tracking redirect links are never followed (the number simply is not found and the message is counted as no-tracking).
- A well-formed number with the carrier named within a short distance of it: accept.
- UPS numbers in the 1Z form with a valid check digit: accept on their own (very high precision).
- A bare run of digits with no carrier context: REVIEW and never polled. A 12 or 15 digit number is not proof of a carrier.
- More than one carrier fits one number, or one message carries several numbers with conflicting context: REVIEW.
- Check-digit validators per carrier (UPS mod 10 on the 1Z form, FedEx mod 11 for 12 digits and mod 10 for 15, USPS mod 10 on the long form, DHL Express mod 7) are to be confirmed in D1 against each carrier's published test numbers. A wrong validator can only cause a false REVIEW, never a wrong poll.
- Messages sent by Jim are skipped. Bodies are held in memory only and never stored or logged.

**Lifecycle.** Carrier-proven terminals: `DELIVERED`, `RETURNED`. `DELIVERED` never regresses. Delivered is never inferred from age, silence or an expected-delivery date. Bounded-failure terminals, proposed defaults held as named constants in `limits.py`:

- `NOT_FOUND`: carrier does not recognize the number after about 7 days of polling (covers a freshly created label that never activates, and mis-extracted numbers).
- `STALE`: no new carrier event for about 21 days while non-terminal.
- `UNTRACKABLE`: carrier has no API we hold credentials for (including Amazon Logistics).

The bounded-failure terminals appear once in "needs review" and then drop out of active polling. They are never rendered as delivered.

## 9. Poll algorithm and budgets

- Each tick selects due, non-terminal shipments ordered by next poll time, capped per run. One call per shipment per tick at most.
- Proposed intervals: pre-transit 6 hours, in transit 3 hours, out for delivery 1 hour, exception 3 hours; failures back off from 1 to 6 hours.
- Per-carrier daily call count is reserved in Hostinger before sending (the `usage.py` pattern), pessimistically. Shipments over budget are deferred, not failed.
- The first 429 or 403 from a carrier stops that carrier for the run (the `LIMITS.md` rule); other carriers continue.
- Three consecutive failures mark the shipment degraded on the card. The last accepted state stays visible and labelled.
- All writes are transactional with an authoritative read-back; replaying the same carrier response is a no-op.

## 10. Persistence

V7's rule is that a schema change stops work and is reported to Jim. This is that report. Options:

| Option | Fit | Verdict |
|---|---|---|
| A. Extend the Amazon Orders Notion data source with tracking columns | Breaks the schema check in `lifeos/amazon/stage.py` (`SCHEMA_TYPES`); Notion allows about 3 requests per second and has no good append-only event log; every poll becomes a Notion write | Reject |
| B. New Notion "Deliveries" database | Same polling and rate-limit problems; adds a second Notion integration and token | Reject for state; optional later as a human view |
| C. New Hostinger tables, domain-owned (recommended) | Same approved datastore and pattern as Jobs and Jira; fits polling state and event history; Notion remains presentation only | Recommend |
| D. `snapshot_store` single-row JSON | Fits "replace each run" snapshots, not per-shipment due times or dedupe | Reject |

Recommended minimal schema. Because volume is low, **start with the first two tables only**; add the others when a real need appears (a carrier quota that needs counting, or history worth keeping):

- `v7_shipments`: shipment key (hash of carrier and number, unique), carrier, tracking number, state, last carrier status code and time, expected delivery date, first seen, last polled, next poll, consecutive failures, terminal time, optional order reference (Amazon link), origin (Gmail, Outlook, Amazon, other feature), short display label.
- `v7_shipment_events` (deferred): append-only carrier events, unique on (shipment, event hash), bounded retention after terminal.
- `v7_shipment_mail`: seen table of (provider, message key) with outcome (ACCEPTED, NO_TRACKING, REVIEW). It stops bodies being refetched every tick and makes replay free.
- `v7_carrier_calls` (deferred): (day, carrier, calls), the quota counter.

Tracking numbers live only in this private database. They are never logged, never in Git, and tests use only documented synthetic or carrier-published sample numbers.

## 11. Presentation

- A new region, proposed heading "Deliveries", owner `v7-deliveries`, written by `lifeos/deliveries/card.py` with `report_region.replace_text`. It needs a router entry, a new callout that Jim creates once on the Daily Report page (V7 never creates a missing block), and a `DELIVERIES_CARD_BLOCK_ID` secret.
- Content: one summary line with freshness and counts, then open shipments (carrier, state, expected date, last scan time, link to the carrier's tracking page), then delivered in the last 7 days, then needs-review. Carrier or mailbox failure renders `DEGRADED` with the last accepted rows still shown, exactly as the Amazon card does.
- Amazon-linked shipments stay on the Amazon card and are not listed twice. Whether to merge both regions into one is decision 3.

## 12. Failure semantics

Fixed codes only, counts-only output. A mailbox read error is DEGRADED and leaves state untouched. A carrier outage keeps the last accepted state. An unreadable shipment table fails closed. One carrier failing never affects another carrier, the Amazon job, or the Jobs pipeline (the domain job is isolated and warns, as Amazon's does).

## 13. Privacy

The repository is public (`docs/PRIVACY.md`). No real tracking number, sender, subject, merchant, address or mail identifier appears in code, tests, fixtures, logs, errors or docs. Output is counts and codes. Fixtures use carrier-published test numbers or invented numbers. Tracking numbers and display labels live only in the private database and the private Notion page.

## 14. Relationship to the Physical Mail handoff

Physical Mail's forward-then-track stage has the same carrier-status requirement, and its handoff stops with `RUNTIME_PATH_INCOMPLETE_STOP` because no approved carrier source exists. `lifeos/platform/carriers/` is that source. Physical Mail should call it directly and keep its own derivative state; it should not build another tracker, and it does not need the Deliveries package.

The handoff also assumes a "tracking number received" email from the forwarding vendor. No such message exists in either mailbox. The remaining possibilities are that the number is visible only inside the vendor's portal, or that it arrives from a sender or wording the searches did not match. Until Jim supplies one sanitized example or confirms it is portal-only, the tracking stage of Physical Mail cannot be built, and the deterministic open-chain stage can ship without it. Jim reports receiving few tracking numbers, so this stage is the lowest priority; the next time a mail item is forwarded, noting where the number appears (an email, the portal, or nowhere) settles the question.

## 15. Roadmap (each slice has a product boundary of its own)

| Slice | Outcome | Likely files | Schema | Tests | Depends on | Size | PRs |
|---|---|---|---|---|---|---|---|
| **D0 Access spike** | Credentials (UPS, FedEx, USPS first; DHL last) and egress proven for each carrier from the GitHub runner with no personal data: OAuth token fetch plus a documented sample number returns a structured status or a structured not-found. Live quotas and terms read from the portals. Sanitized response fixtures captured. | `lifeos/deliveries/probe.py` (counts only), `docs/SETUP.md` section, `docs/LIMITS.md` rows | none | probe against fakes | Jim creates developer credentials for UPS, FedEx and USPS in their portals (he already holds the UPS and FedEx accounts) | S | 1 |
| **D1 Extractor and census (dry run)** | Dry run reports, by carrier class, how many mailbox messages yielded accepted, review or no-tracking results over about 90 days. No writes. **The accepted count is the go/no-go for D2.** | `lifeos/deliveries/extract.py`, `numbers.py`, `census.py`, `lifeos/platform/carriers/numbers.py`, provider-neutral message record | none yet | table-driven extractor tests, check-digit tests, lookalike and conflict cases, replay | none | M | 1 |
| **D2 UPS end to end** | A UPS package found in mail is tracked to delivered and appears in the Deliveries region. | `lifeos/platform/carriers/ups.py`, `lifeos/deliveries/{store,reconcile,stage,card}.py`, router entry, `domains.yml` job, `test_workflow.py` secrets entry, `run.py` lines | `v7_shipments`, `v7_shipment_mail` (events and call counter deferred) | extraction through card with fakes; transition, terminal and degraded cases; budget and 429 stop | D0, D1, Jim's schema approval, Jim creates the callout | M | 1 |
| **D3 FedEx, USPS, DHL adapters** | The other carriers join the same loop, in that order. | one small module per carrier | none | per-carrier response fixtures | D2 | S each | 1 to 3 |
| **D4 Outlook source** | Outlook mail (chosen accounts) feeds the same census; one shipment across both mailboxes. | Outlook all-folders listing and HTML-to-text moved into platform, account selection | none | cross-provider dedupe | D2 | M | 1 |
| **D5 Amazon link** | Amazon shipments with carrier numbers show carrier-level state on the Amazon card; Amazon Logistics shown as untrackable. | link rule, card change | one nullable column on shipments (already allowed for) | no double listing | D2 and decision 3 | S | 1 |
| **D6 Alerts and review policy** | Exception, credential-failure and stale detectors through the platform alerts layer. | `lifeos/deliveries/alerts.py` | none | detector and dedupe tests | D2 | S | 1 |
| **D7 Physical Mail hook** | Physical Mail consumes `platform/carriers`. | in the Physical Mail domain | none | forward-to-delivered chain | D2 and a tracking-message fixture or decision | S | with that feature |

## 16. UAT matrix

1. A merchant shipping email with a UPS number produces one shipment.
2. The same message read twice, or the same number in a second message, still produces one shipment.
3. A shipping email and a later out-for-delivery email for one number update one row.
4. A number that passes its check digit but the carrier does not recognize becomes NOT_FOUND after the window and is never shown as delivered.
5. A bare digit run with no carrier context becomes REVIEW and is never polled.
6. In transit, then out for delivery, then delivered: the shipment closes and shows under delivered for 7 days.
7. A delivered shipment never returns to active if the carrier later reports an earlier scan.
8. Return-to-sender reaches RETURNED.
9. An exception state surfaces on the card and raises one alert.
10. A carrier outage keeps the prior state and shows DEGRADED; no delivery is inferred.
11. A 429 stops that carrier for the run and defers its shipments; other carriers continue.
12. The daily budget is exhausted: remaining shipments are deferred, not failed.
13. The same number in Gmail and Outlook is one shipment.
14. An Amazon Logistics number is recorded as untrackable and not polled.
15. A message Jim sent containing a number is ignored.
16. An Amazon-linked shipment is not listed twice across regions.
17. The suite's privacy test passes; no log or error contains a number, merchant or address.

## 17. Rejected architecture

No new scheduler, queue or watchdog. No scraping or browser automation. No TinyFish and no paid tracking service. No aggregator as the default path. No Notion table as the polling store. No mail sending, filing, labelling or deletion for this feature. No ChatGPT dependency at runtime. No cross-OS package imports. No generic package-tracking platform. No multi-user support.

## 18. Decision-log proposals (draft; `docs/DECISIONS.md` is not edited)

- Deliveries is a separate domain from Amazon Orders; the carrier clients live in `platform` because Physical Mail also needs them.
- The carrier is the authority on shipment state; mail is discovery only; Delivered is never inferred.
- Shipment state lives in domain-owned Hostinger tables as a derivative cache; Notion is presentation only.
- Carrier APIs on free developer accounts only; quotas canonized in `limits.py` after reading the live portals.
- Amazon Logistics shipments are untrackable by API and keep Amazon's delivered mail as their terminal evidence.

## 19. Decisions Jim must make

1. **Settled: USPS is in the carrier list.**
2. **Settled: Jim holds active UPS and FedEx accounts**, so production credentials can be issued for both.
3. **One region or two.** A new "Deliveries" callout next to Amazon (recommended for now, because the Amazon card went live recently), or merge both into one later.
4. **Tracking numbers in private storage.** V1's production contract says never to persist "tracking tokens". Confirm that carrier tracking numbers in the private Hostinger database are allowed, and that the wording is clarified in the contract. That edit belongs to `life-os-automation` and needs Jim to name it as an explicit governance change.
5. **Which Outlook accounts feed Deliveries** (personal only, or also work).
6. **Approve the Hostinger tables** in section 10 (a schema change under V7's rules): two tables to start, asked for only after the D1 census says D2 is worth building.
7. **Closure thresholds.** Accept or change the proposed NOT_FOUND (about 7 days) and STALE (about 21 days) windows.

## 20. First build slice

```text
FIRST BUILD SLICE:
D0 access spike, then D1 extractor and dry-run census

WHY FIRST:
Carrier access is the single unproven dependency (free terms, credentials, egress from the runner), and the extractor is the precision gate that decides whether discovery works on real mail. Neither needs a schema change, and D0 also unblocks Physical Mail. With few tracking numbers expected, the dry-run count also decides whether the store and card (D2) are worth building at all.

EXPECTED FILES:
lifeos/deliveries/probe.py, lifeos/platform/carriers/numbers.py, lifeos/deliveries/extract.py, tests/deliveries/*, docs/SETUP.md and docs/LIMITS.md additions

SCHEMA:
NONE

ACCEPTANCE:
Each carrier returns a structured result from the runner without personal data; extractor table tests pass including lookalike, conflict and replay cases; a dry run over real mail prints counts only.

DO NOT BUILD YET:
The store, the card, Outlook, the Amazon link, alerts, and any Physical Mail integration.
```
