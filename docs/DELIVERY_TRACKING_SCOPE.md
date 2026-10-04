# Delivery tracking: scope and roadmap (scoping only, no code)

Status: proposal for Jim's review. Nothing here is built. No schema, secret, workflow or `DECISIONS.md` change is made by this document.
Question answered: can Amazon Orders grow into "all deliveries" (FedEx, UPS, DHL and others) by reading Gmail and Outlook, capturing the tracking number, and following it to its conclusion?

Settled by Jim since the first draft: **USPS is in scope; Jim holds active UPS and FedEx accounts** (so production credentials are available for both); and **he receives few tracking numbers**. Low volume changes two things below: the schema starts at two tables, and the first slice's dry-run count of real tracking numbers is the go/no-go for building the store and card (D2).

**Carrier research folded in (section 5).** Jim supplied a research report dated October 4, 2026 (a ChatGPT read of the official carrier and developer pages, with each fact marked VERIFIED or UNVERIFIED). I read the report, not the carrier pages: this session's sandbox cannot reach them, so every number below is second-hand until slice D0 reads the live portals. The report changed the plan in five ways: the carrier order is USPS, FedEx, UPS, then DHL only if eligible; UPS's agreement forbids building a database from its data, so UPS gets a current-state cache that is purged 30 days after delivery; DHL needs a valid company name on the account, so it is conditional; carrier status lists and check-digit algorithms are not published, so nothing is hard-coded as a closed list; and the schema is two tables with no event history and no call counter.

## 0. Answer in one paragraph

Yes, and it fits V7 without a new scheduler, datastore engine or paid service. It is a different problem from Amazon Orders, though, and should not be built as an extension of `lifeos/amazon/`. Amazon works because three exact sender addresses carry one order id each, so a sender allowlist is a complete mailbox filter. "All deliveries" has no allowlist: tracking numbers arrive from any merchant, so the extractor (a pure function with checksums) becomes the precision gate, and the carrier's own API becomes the authority on state. The new pieces are (1) tracking-number extraction and validation, (2) carrier status clients for USPS, FedEx, UPS and, only if eligible, DHL, (3) a small Hostinger state store with a due-time polling rule, and (4) a card region. Everything else reuses what Amazon and Bills already proved.

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
| Carrier status clients | MISSING | USPS, FedEx, UPS, and DHL only if eligible. |
| Status normalization and lifecycle | MISSING | |
| Shipment storage | MISSING | No table. Amazon rows hold no tracking fields. Current state only, no event history (section 10). |
| Poll scheduling (due time, backoff) | MISSING | Web boards (`WEB_REFRESH_HOURS`, `WEB_RETRY_HOURS`) are the nearest pattern. |
| Carrier quota accounting | NOT NEEDED | At this volume a per-run cap and the stop-on-first-429 rule are enough (section 9). |
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

Source: the October 4, 2026 research report (second-hand, see the header). "Confirmed" below means the report quoted an official page for it; "unpublished" means the carrier does not publish it. Slice D0 re-reads each portal before any number is canonized in `limits.py`.

**Order of build: USPS, then FedEx, then UPS, then DHL only if eligible. Amazon Logistics is not polled.**

| Carrier | API and auth | Account Jim needs | Limits | Terms that shape the design | Not found | Verdict |
|---|---|---|---|---|---|---|
| **USPS** | Tracking 3.2 (`/tracking/v3r2/tracking`). OAuth client credentials; token about 8 hours, honor the returned expiry. Up to 35 numbers per request. Tracking 3.0 retires July 31, 2027, so build on 3.2 only. | A USPS Business Account through the Customer Onboarding Portal, then an app; Tracking is in the default product. Free. | Hourly quota enforced (429) but the number is unpublished | The friendliest: terms name "personal or commercial use" and allow storage for uses related to your own mailing or shipping; a database for third-party use is prohibited | 404 "Package with tracking number not found" | **First** |
| **FedEx** | Track v1.0.0 (`POST /track/v1/trackingnumbers`). OAuth, token lasts 1 hour. Up to 30 numbers per request. | A developer project inside a portal organization with Jim's existing FedEx account associated. Personal accounts are allowed and free, but organization setup asks for a company name. | 100,000 requests a day per project; 1,400 per 10 seconds; 429 on a breach. The token endpoint is stricter by public IP: 3 hits a second for 5 seconds, or 1 a second for 2 minutes, returns 403 for 10 minutes | "Limit the number of times a package is tracked to what is necessary" and drop delivered packages from batches. No stated private-database terms (unverified) | HTTP 404 **or** an application alert `TRACKING.DATA.NOTFOUND`; handle both | **Second** |
| **UPS** | Track v1 (`/api/track/v1/details/{number}`), one number per request. OAuth client credentials (a bearer token; use the returned expiry). Required headers include `transId` and `transactionSrc`. Legacy access keys are gone; the developer portal needs MFA. | A UPS.com login **and a UPS shipper account** (the application is linked to it). Whether an individual without a business is accepted for production is unpublished. | Requests per minute enforced (error 10429) but unpublished; data rolls off after 120 days | **The API agreement forbids compiling a database from UPS data and caps storing tracking data at nine months after the request.** | HTTP 404 per the spec, but unofficial reports of 200 with a warning: parse both | **Third, as a current-state cache only** (below) |
| **DHL** (Unified tracking) | v1.5.8, static `DHL-API-Key` header, no OAuth. | A key that DHL reviews by hand, checking for a valid company name and ideally a matching email domain. An individual with no company is a real risk. | 250 calls a day, one call per 5 seconds | The published push terms say to delete tracking data 30 days after delivery and not pass it to third parties; whether pull has the same wording is unconfirmed, so follow it anyway | 404 | **Last, only if DHL approves the account**; skip otherwise |
| DHL eCommerce Americas v4 | OAuth for shippers | An authorized pickup account | unpublished | Returns only packages in the caller's own pickup accounts | n/a | **Rejected** for inbound packages (it cannot see them) |
| Amazon Logistics | none | n/a | n/a | The two official Amazon tracking APIs cover only shipments bought through Amazon Shipping, or Amazon Business orders with the right role. Neither can look up a consumer parcel by number. | n/a | **Not polled**; Amazon's own delivered mail stays the terminal evidence |
| Carrier notification emails | Viable in principle | none | n/a | none | n/a | Not built: zero such mail exists in either mailbox |
| Free aggregators | Ship24 free plan: 10 shipments a month, or 100 calls a month; no overage, tracking stops at the cap. 17TRACK: 100 free quotas a month (its own pages conflict at 50). AfterShip: API needs a paid plan. | A third-party account | as stated | The tracking numbers go to a third party | n/a | **Fallback only**, for a carrier with no direct access (most likely DHL); Jim decides (section 19) |
| Scraping carrier pages, a headless browser, TinyFish | Possible | | | | | Rejected: brittle, terms risk, contradicts the free-only stance (D11, D13) |

**Rules that follow from the research.**
- **No closed status lists.** The carriers do not publish a stable, complete status or event enumeration (UPS, FedEx, USPS, DHL all unconfirmed, and DHL says new values may appear without notice). The normalized states in section 7 are ours; each carrier adapter maps only the statuses D0 actually observed, and **an unrecognized status keeps the previous state, records the raw code, counts as "unmapped" in the report, and is never terminal**.
- **Terminal only on an explicit match.** `DELIVERED` and `RETURNED` are set only when the adapter recognizes the carrier's delivered or returned indication; the adapter's mapping is checked in D0 against real responses.
- **Defensive not-found.** Each carrier's not-found surface (HTTP 404, an application alert, a 200 with a warning) is detected in the adapter and mapped to one internal "not found" result, so the `NOT_FOUND` rule (section 8) behaves the same everywhere.
- **FedEx token and IP penalty.** One token per run, reused for the run (the run is far under an hour); the token request is never repeated per shipment. A 403 from the token endpoint stops FedEx for that run.
- **Fewer calls.** FedEx and USPS accept batches (30 and 35 numbers), so one call per run per carrier covers every due shipment; UPS is one number per request. Delivered shipments leave the batch (FedEx's own rule, adopted for all carriers).
- **Nothing assumed about GitHub's IPs.** No carrier documents blocking hosted runners, and none promises it works, so D0 proves it from a real runner for each carrier rather than assuming either way.
- **No fixed polling interval is published** by any carrier, so the intervals in section 9 stay conservative defaults.

**UPS current-state cache (the agreement's constraint).** For UPS shipments store only what the card needs: carrier code and time of the last status, expected date, the poll schedule. No scan history, no event table. Purge a UPS row (the number and everything else) 30 days after its terminal time, which is far inside the nine-month cap; the purge runs in the same stage. This is a small but real terms risk in a "database from UPS data" reading, so UPS is built last among the direct carriers and **Jim decides whether to accept it** (section 19). If he does not, UPS packages are shown as "tracking number found, UPS not polled" with a link to UPS's tracking page.

**Quota arithmetic.** With about five open shipments polled every three hours, that is on the order of 40 calls a day across all carriers (fewer with batching), against FedEx's 100,000, UPS's unpublished per-minute cap, USPS's unpublished hourly cap and DHL's 250. Jim reports few tracking numbers, so real volume is lower still: a per-run cap and the stop-on-first-429 rule are enough, with no call-counter table. The report's own conclusion matches: at this volume rate limits are irrelevant; credential eligibility, data-use terms and Amazon and DHL authorization scope are the real constraints.

**Public tracking links.** Only DHL documents a parameterized tracking link. For the others, no carrier promises a stable query-string template, so the card links each carrier's official tracking page and shows the number as text. A parameterized link can be added later if a carrier documents one.

**Number formats the carriers confirm** (all used only as hints, section 8): UPS `1Z` plus 16 characters (18 in all), and also 12-digit, `T` plus 10 digits and 9-digit forms; FedEx Express 12 digits, a 14-digit enterprise label form, and legacy Ground 15 digits (the report could not confirm longer forms); USPS API accepts 4 to 34 alphanumeric characters, with 22-digit numbers as published examples; DHL Express 10 digits; DHL eCommerce 10 to 39 characters. **No carrier publishes its check-digit algorithm in the sources found.** Carrier-published sample numbers (UPS `1Z9999999999999999`, DHL `7777777770`, a USPS 22-digit example) are the only numbers safe to use in tests.

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
class Carrier(Enum):      USPS, FEDEX, UPS, DHL, AMAZON_LOGISTICS, UNKNOWN
class ShipmentState(Enum):
    PRE_TRANSIT, IN_TRANSIT, OUT_FOR_DELIVERY, AVAILABLE_FOR_PICKUP, EXCEPTION,
    DELIVERED, RETURNED,                       # carrier-proven terminals
    UNTRACKABLE, NOT_FOUND, STALE,             # bounded-failure terminals, surfaced once as "needs review"
    REVIEW                                     # ambiguous identity; never polled

@dataclass(frozen=True)
class TrackingCandidate:      carrier: Carrier | None; number: str; evidence: Evidence; check_ok: bool
@dataclass(frozen=True)
class CarrierStatus:          state: ShipmentState; code: str; occurred_at: datetime | None     # code is the carrier's raw value; an unmapped code keeps the prior state
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
- UPS numbers in the 18-character `1Z` form: accept on their own (very high precision).
- A bare run of digits with no carrier context: REVIEW and never polled. A 12 or 15 digit number is not proof of a carrier.
- More than one carrier fits one number, or one message carries several numbers with conflicting context: REVIEW.
- **A check digit is a hint, never a gate.** No carrier publishes its algorithm in the sources found, so a validator can only come from D1 testing a commonly cited algorithm against the carrier-published sample numbers and real carrier answers. A failing check digit with strong carrier context still gets one poll, because the carrier's answer is the real validator (a carrier that recognizes a number proves the extraction, and one that does not leads to `NOT_FOUND`); a passing check digit with no carrier context is still REVIEW.
- Messages sent by Jim are skipped. Bodies are held in memory only and never stored or logged.

**Lifecycle.** Carrier-proven terminals: `DELIVERED`, `RETURNED`. `DELIVERED` never regresses. Delivered is never inferred from age, silence or an expected-delivery date. Bounded-failure terminals, proposed defaults held as named constants in `limits.py`:

- `NOT_FOUND`: carrier does not recognize the number after about 7 days of polling (covers a freshly created label that never activates, and mis-extracted numbers).
- `STALE`: no new carrier event for about 21 days while non-terminal.
- `UNTRACKABLE`: carrier has no API we hold credentials for (including Amazon Logistics).

The bounded-failure terminals appear once in "needs review" and then drop out of active polling. They are never rendered as delivered.

**Retention.** A terminal shipment stays on the card for 7 days, and its row is purged 30 days after its terminal time for every carrier (the strictest carrier rule is UPS's, and DHL's push terms say the same 30 days). Source messages are not re-read after that, because the census window is about 90 days and `v7_shipment_mail` remembers each message already handled; a message older than the window is simply not listed.

## 9. Poll algorithm and budgets

- Each tick selects due, non-terminal shipments ordered by next poll time, capped per run. A shipment is polled at most once per tick: FedEx and USPS due shipments go in one batched request per run (up to 30 and 35), UPS one request per shipment.
- Proposed intervals: pre-transit 6 hours, in transit 3 hours, out for delivery 1 hour, exception 3 hours; failures back off from 1 to 6 hours.
- The per-run cap is the budget; there is no call counter table. Shipments over the cap are deferred, not failed.
- The first 429 or 403 from a carrier stops that carrier for the run (the `LIMITS.md` rule); other carriers continue. A 403 from FedEx's token endpoint means its 10-minute IP penalty, so FedEx is skipped until the next run.
- The OAuth token is fetched once per run per carrier and reused.
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

Recommended minimal schema: **two tables, and only two.** Event history and a call counter are dropped, not deferred: event history would conflict with UPS's agreement and the 30-day purge, and at this volume the per-run cap replaces a counter. Add a table only if a real need appears and Jim approves it.

- `v7_shipments`: shipment key (hash of carrier and number, unique), carrier, tracking number, state, last raw carrier status code and its time, expected delivery date, first seen, last polled, next poll, consecutive failures, terminal time, optional order reference (Amazon link), origin (Gmail, Outlook, Amazon, other feature), short display label. Current state only; no scan history.
- `v7_shipment_mail`: seen table of (provider, message key) with outcome (ACCEPTED, NO_TRACKING, REVIEW). It stops bodies being refetched every tick and makes replay free. It holds no tracking number.

Tracking numbers live only in this private database. They are never logged, never in Git, and tests use only documented synthetic or carrier-published sample numbers.

## 11. Presentation

- A new region, proposed heading "Deliveries", owner `v7-deliveries`, written by `lifeos/deliveries/card.py` with `report_region.replace_text`. It needs a router entry, a new callout that Jim creates once on the Daily Report page (V7 never creates a missing block), and a `DELIVERIES_CARD_BLOCK_ID` secret.
- Content: one summary line with freshness and counts, then open shipments (carrier, state, expected date, last scan time, the number as text with a link to the carrier's official tracking page), then delivered in the last 7 days, then needs-review. Carrier or mailbox failure renders `DEGRADED` with the last accepted rows still shown, exactly as the Amazon card does.
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
| **D0 Access spike** | Credentials (USPS first, then FedEx, then UPS; DHL only if eligible) and egress proven for each carrier from the GitHub runner with no personal data: OAuth token fetch plus a carrier-published sample number returns a structured status or a structured not-found. Live quotas, the UPS and USPS terms, and each carrier's not-found surface read from the portals. Sanitized response fixtures captured, and the status mapping for each carrier recorded from what was actually observed. | `lifeos/deliveries/probe.py` (counts only), `docs/SETUP.md` section, `docs/LIMITS.md` rows | none | probe against fakes | Jim creates developer credentials (section 21) | S | 1 |
| **D1 Extractor and census (dry run)** | Dry run reports, by carrier class, how many mailbox messages yielded accepted, review or no-tracking results over about 90 days. No writes. **The accepted count is the go/no-go for D2.** | `lifeos/deliveries/extract.py`, `numbers.py`, `census.py`, `lifeos/platform/carriers/numbers.py`, provider-neutral message record | none yet | table-driven extractor tests, check-digit tests, lookalike and conflict cases, replay | none | M | 1 |
| **D2 First carrier end to end** | A package found in mail is tracked to delivered and appears in the Deliveries region, for the carrier (USPS or FedEx) with the most census hits; USPS if tied. | `lifeos/platform/carriers/{usps,fedex}.py` (one of them), `lifeos/deliveries/{store,reconcile,stage,card}.py`, router entry, `domains.yml` job, `test_workflow.py` secrets entry, `run.py` lines | `v7_shipments`, `v7_shipment_mail` (two tables; no events, no counter) | extraction through card with fakes; transition, terminal, unmapped-status and degraded cases; per-run cap and 429 stop; 30-day purge | D0, D1, Jim's schema approval, Jim creates the callout | M | 1 |
| **D3 Remaining carriers** | The other direct carriers join the same loop: the second of USPS and FedEx, then UPS (current-state cache, 30-day purge, only if Jim accepts the UPS terms and has a shipper account), then DHL only if DHL approves the account. | one small module per carrier | none | per-carrier response fixtures | D2 | S each | 1 to 3 |
| **D4 Outlook source** | Outlook mail (chosen accounts) feeds the same census; one shipment across both mailboxes. | Outlook all-folders listing and HTML-to-text moved into platform, account selection | none | cross-provider dedupe | D2 | M | 1 |
| **D5 Amazon link** | Amazon shipments with carrier numbers show carrier-level state on the Amazon card; Amazon Logistics shown as untrackable. | link rule, card change | one nullable column on shipments (already allowed for) | no double listing | D2 and decision 3 | S | 1 |
| **D6 Alerts and review policy** | Exception, credential-failure and stale detectors through the platform alerts layer. | `lifeos/deliveries/alerts.py` | none | detector and dedupe tests | D2 | S | 1 |
| **D7 Physical Mail hook** | Physical Mail consumes `platform/carriers`. | in the Physical Mail domain | none | forward-to-delivered chain | D2 and a tracking-message fixture or decision | S | with that feature |

## 16. UAT matrix

1. A merchant shipping email with a UPS number produces one shipment.
2. The same message read twice, or the same number in a second message, still produces one shipment.
3. A shipping email and a later out-for-delivery email for one number update one row.
4. A number the carrier does not recognize becomes NOT_FOUND after the window and is never shown as delivered, whichever way the carrier signals it (HTTP 404, an application alert, or a 200 with a warning).
5. A bare digit run with no carrier context becomes REVIEW and is never polled.
6. In transit, then out for delivery, then delivered: the shipment closes and shows under delivered for 7 days.
7. A delivered shipment never returns to active if the carrier later reports an earlier scan.
8. Return-to-sender reaches RETURNED.
9. An exception state surfaces on the card and raises one alert.
10. A carrier outage keeps the prior state and shows DEGRADED; no delivery is inferred.
11. A 429 stops that carrier for the run and defers its shipments; other carriers continue.
12. The per-run cap is reached: remaining shipments are deferred, not failed.
13. The same number in Gmail and Outlook is one shipment.
14. An Amazon Logistics number is recorded as untrackable and not polled.
15. A message Jim sent containing a number is ignored.
16. An Amazon-linked shipment is not listed twice across regions.
17. The suite's privacy test passes; no log or error contains a number, merchant or address.
18. A carrier status the adapter has never seen keeps the shipment's previous state, records the raw code, and is counted as unmapped; it never closes a shipment.
19. A UPS row, and any other terminal row, is purged 30 days after its terminal time and the number is gone; the seen-message row stays and holds no number.
20. FedEx: one token request serves every shipment in the run; a 403 from the token endpoint skips FedEx for the run and leaves the other carriers polling.
21. A failing check digit with a carrier named beside the number is polled once; a passing check digit with no carrier context is REVIEW and never polled.

## 17. Rejected architecture

No new scheduler, queue or watchdog. No scraping or browser automation. No TinyFish and no paid tracking service. No aggregator as the default path (a free-plan fallback only, if Jim approves it). No Notion table as the polling store. No mail sending, filing, labelling or deletion for this feature. No ChatGPT dependency at runtime. No cross-OS package imports. No generic package-tracking platform. No multi-user support.

## 18. Decision-log proposals (draft; `docs/DECISIONS.md` is not edited)

- Deliveries is a separate domain from Amazon Orders; the carrier clients live in `platform` because Physical Mail also needs them.
- The carrier is the authority on shipment state; mail is discovery only; Delivered is never inferred.
- Shipment state lives in domain-owned Hostinger tables as a derivative cache; Notion is presentation only.
- Carrier APIs on free developer accounts only (USPS, FedEx, UPS; DHL only if approved); quotas canonized in `limits.py` after reading the live portals.
- No closed carrier status lists: an unmapped status never closes a shipment, and a check digit is a hint, never a gate.
- Shipment state is current-state only (no event history), and terminal rows are purged 30 days after their terminal time, which also satisfies UPS's agreement.
- Amazon Logistics shipments are untrackable by API and keep Amazon's delivered mail as their terminal evidence.

## 19. Decisions Jim must make

Settled: USPS is in the carrier list; Jim holds active UPS and FedEx accounts; he receives few tracking numbers; the carrier research is delivered.

1. **Which carriers, in what order.** Recommended: USPS, then FedEx, then UPS if you accept decision 2, then DHL only if it approves you. Skip DHL unless you have a company name to put on its form.
2. **Accept the UPS terms risk?** UPS's agreement forbids building a database from UPS data and caps stored tracking data at nine months. The mitigation is a current-state cache purged 30 days after delivery. Recommended: yes, built last. If no, UPS packages show the number and a link only.
3. **Is your UPS account a shipper account?** UPS's developer application is tied to one. Your login and a UPS shipping account number are the test; if you only have a free delivery-notification profile, you may need to add a shipper account (free to open, per the report; unconfirmed).
4. **Free aggregator fallback.** Allow Ship24's free plan (10 shipments or 100 calls a month, no overage) as a fallback for a carrier with no direct access? It sends those tracking numbers to a third party. Recommended: not now; revisit if DHL is declined and DHL packages matter.
5. **One region or two.** A new "Deliveries" callout next to Amazon (recommended for now, because the Amazon card went live recently), or merge both into one later.
6. **Tracking numbers in private storage.** V1's production contract says never to persist "tracking tokens". Confirm that carrier tracking numbers in the private Hostinger database are allowed, and that the wording is clarified in the contract. That edit belongs to `life-os-automation` and needs Jim to name it as an explicit governance change.
7. **Which Outlook accounts feed Deliveries** (personal only, or also work).
8. **Approve the Hostinger tables** in section 10 (a schema change under V7's rules): two tables, asked for only after the D1 census says D2 is worth building.
9. **Closure thresholds.** Accept or change the proposed NOT_FOUND (about 7 days) and STALE (about 21 days) windows, and the 7-day display and 30-day purge.

## 20. First build slice

```text
FIRST BUILD SLICE:
D0 access spike (USPS and FedEx first), then D1 extractor and dry-run census

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

## 21. What Jim prepares for carrier credentials

Nothing is needed until slice D0 starts, and then only for the carriers you chose. Each step is free per the report; slice D0 confirms on the live portals. The secret names are proposals and go in only when D0 runs.

- **USPS** (first): create a USPS Business Account through the USPS Customer Onboarding Portal ("Log In/Create USPS Business Account"), create an app, and copy the consumer key and secret. Tracking is in the default product, so no extra approval is listed. Proposed secrets: `USPS_CLIENT_ID`, `USPS_CLIENT_SECRET`.
- **FedEx**: create or sign in to a FedEx Developer Portal account, create a project (the portal asks for an organization and company name; use what you use for the account), associate your existing FedEx account number, and copy the production API key and secret. Proposed secrets: `FEDEX_CLIENT_ID`, `FEDEX_CLIENT_SECRET`.
- **UPS** (only after decision 2 and 3): create an application at the UPS developer portal (MFA applies), linked to your shipper account, and copy the client ID and secret. Proposed secrets: `UPS_CLIENT_ID`, `UPS_CLIENT_SECRET`.
- **DHL** (only if you have a company name): request a Shipment Tracking (Unified) key; DHL reviews it by hand. Proposed secret: `DHL_API_KEY`.

All of these would join one `deliveries` entry in `DOMAIN_SECRETS`, so no other job sees them. Never paste a secret into chat or a file in the repository; they go into the repository's secret store when D0 starts.
