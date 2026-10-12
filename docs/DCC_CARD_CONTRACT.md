# Daily Command Center and V7: the card contract (D174; the writer changed to V7 in D175)

> **D175 (Oct 12):** V7 now writes the Daily Command Center itself (stage `dcc-card`). Sections 1 and 4 and the paste-ready block below describe the earlier ChatGPT-written version and no longer apply to writing; sections 2 and 3 still describe the stamps V7's cards carry and how freshness is judged, and V7's own Command Center follows them.

The ChatGPT-native `LIFE OS Daily Runs` task (D58) and V7 are independent schedulers. They meet on one Notion page, the Daily Report, and only through what is written on it. This file is the agreement: what V7 promises about every card it writes, and how the Daily Command Center (DCC) reads those cards and writes its own region. Nothing here lets either side start, call or depend on the other.

## 1. Who writes what

- V7 writes the regions it owns (Calendar, JIRA Execution, Bills: This Week, Mail Alerts, Amazon Orders, Attention, Hiring Pipeline), hourly, after every tick. The DCC never writes them.
- The DCC writes exactly one region: the callout whose first child heading is `ChatGPT · Daily Command Center`. It uses the procedure in D169 (`lifeos/platform/dcc_write.py` is the tested reference): fetch just before, replace only the lines that differ, fetch again, prove every other region is unchanged. One attempt, no retry.
- Nothing else on the page belongs to either side. The old date heading, `Last refreshed` line, `9 AM Delta` and `Run history` were removed on Oct 12 (Jim); do not recreate or mention them.

## 2. What V7 promises about a card

Every V7 region carries a stamp of the form `Updated H:MM AM|PM CT` (Chicago time), in the place shown:

| Region | Where the stamp is |
|---|---|
| Calendar, JIRA Execution | in the heading: `Calendar (Updated 9:05 PM CT)` |
| Bills: This Week, Mail Alerts, Amazon Orders | first line under the heading |
| Hiring Pipeline | first line under its heading, then `· N active` |
| Attention | the callout has no heading; its first line is `Updated … · N active exceptions` |

A card is written only after its stage ran, and the write is read back. A card whose stage failed says so in the same first line and keeps the last good content below it: `DEGRADED · <what> sync failed at H:MM CT · showing last accepted …` (Amazon, Attention, Hiring Pipeline, Mail Alerts), or `STALE · last accepted <date> H:MM CT` (Bills, and cards whose saved snapshot is over 3 hours old). So a current `Updated` stamp means that stage ran and its write was verified in the last run; `DEGRADED` or `STALE` means it did not. A failure also pages the owner's phone (D164, D166, D173), so the DCC is not the alarm.

## 3. How the DCC reads a card

1. Fresh means the stamp is within 90 minutes of the page's fetch time (V7's own daily check uses the same 90). Older: say "stale since H:MM CT". No stamp: say "unknown", never "fresh".
2. A `DEGRADED` or `STALE` line: report the region as degraded (or stale), with its time; the content below is the last accepted state, not current.
3. Do not hedge a fresh card ("unverified", "timestamps do not prove acceptance"). The stamp is V7's own acceptance statement for that card. Say what the card says, with its numbers as written.
4. Amazon: `N in transit` already excludes orders older than 30 days; they are counted on the same line (`N older than 30 days not shown`). Do not call the in-transit list "old orders".
5. Hiring Pipeline: `Awaiting outcome` is derived from calendar events and the opportunity pages; report it as awaiting an outcome, nothing more.
6. Calendar: the stamp covers the card's own refresh. The Outlook to Google copy has its own phone alert (D173); the DCC does not comment on it.
7. Never infer a state a card does not state, and never repair or restate a V7 region.

## 4. What the DCC returns

`PASS`, `NO_ACTION` or `DEGRADED` (D58), with one fixed code when degraded: `ROUTER_TARGET_MISSING`, `ROUTER_TARGET_AMBIGUOUS`, `DCC_READ_FAILED`, `DCC_WRITE_BLOCKED` (the write was refused or errored: record the connector's own error text once), `READBACK_INCOMPLETE`, `DCC_CONTENT_NOT_PRESENT`, `PROTECTED_REGION_VERIFICATION_FAILED`. A change to another region whose own stamp moved while the DCC worked is reported as `external` and is not a failure (D169).

## 5. Limits

- The agreement is one-way in practice: V7 does not see whether the DCC ran. If wanted later, the DCC can start its region with its own `Updated H:MM PM CT` line and V7's daily check-in can report its age; that needs a Notion read in the watchdog (a new secret) and is not done.
- The stamp proves a card's stage ran, not that the world behind it is right (for example, that an old order is really still in transit).

## Paste-ready block for the ChatGPT task instructions

> Read V7 cards by their first line. `Updated H:MM AM/PM CT` within 90 minutes of the page fetch = fresh; older = "stale since"; `DEGRADED · …` or `STALE · …` = degraded or stale (content is last accepted); no stamp = unknown. Do not hedge fresh cards. Report numbers as written. Amazon "N in transit" excludes orders over 30 days (counted separately). Do not mention or recreate the old banner, 9 AM Delta or Run history (removed). Write only the `ChatGPT · Daily Command Center` callout: fetch just before, replace only the lines that differ, fetch again, confirm the DCC text is present and every other region is unchanged or has its own stamp moved (then report it as external); one attempt, never retry; return PASS, NO_ACTION or DEGRADED with one fixed code. On `DCC_WRITE_BLOCKED`, include the connector's exact error once.
