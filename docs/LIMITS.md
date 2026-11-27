# Provider limits (canon)

Numbers live in `lifeos/platform/limits.py` (code imports them) and `tests/test_limits.py` fails if code drifts above a provider
limit. Rule: stay UNDER the published limit with a margin, stop a stage on the first 429/403, never retry harder.

| Provider | Provider limit | Our ceiling | Notes |
|---|---|---|---|
| TinyFish Fetch | 150 URLs/min, 1,000/day | 100/min (batch 10, 6 s apart), 900/day | free; wallet is $35 and must never be spent |
| TinyFish Search | 30 req/min, 500/hour | one request per 4.4 s per lane, two lanes share the key (27/min total); 100 LinkedIn + 40 sponsor discovery + 14 title watch per run (154 of 500/hour; Lensa's 300 removed, D201); each job costs ONE search (D35). The per-run numbers are ceilings in each stage; no counter enforces the provider's 500/hour across stages (`TINYFISH_SEARCH_PER_HOUR`, limits.py:10, is not read by any code) | free, but the account needs Search API access (402 otherwise) |
| TinyFish Agent | $0.016/step | forbidden | |
| Notion API | ~3 req/s average | 2.5/s (0.4 s gap), 120 pages/run (`NOTION_PER_RUN`, limits.py:31); the audit trashes at most 40 pages/run (`AUDIT_PAGES_PER_RUN`, limits.py:32) | free official API only; 100 child blocks per call; 2,000 chars per rich_text |
| LinkedIn guest pages | unpublished | 1 request per 1.2 s, 150/run, stop on first 429 | never logged in |
| Dice tracking redirects | unpublished | 1 request per second (`DICE_GAP_SECONDS`, limits.py:39), 15/run (`DICE_PER_RUN`, limits.py:40; the `resolve-dice` tick step, lifeos/jobs/tick.py:41), stop on first 429 | anonymous GET only; runner access not yet verified. **Parked in practice (LIFE-907):** the resolver stage runs hourly but no source feeds it: Dice and Reed mail are no longer read (LIFE-645) and `SENDER_RULES` (lifeos/sources/newsletters/config.py:15) holds only Lensa, Jobright and LinkedIn alerts; the `dice` row in `us_remote.json` is a discovery helper that nothing reads |
| Jobright | unpublished | one login per run, 40 jobs/run, 1.5 s pause | saved secrets; free tier blocks the Apply click, so read the "Original Job Post" link |
| Public ATS board APIs | unpublished | 8 workers (`ATS_WORKERS`, limits.py:46) for the web stages, 4 workers (`BOARDS_WORKERS`, limits.py:67) for the employer-board window; 10 s timeout | Greenhouse, Lever, Ashby, SmartRecruiters, Workable; no auth |
| Gmail REST | 6,000 quota units/user/min, 1,200,000/project/min (page re-read Nov 27 2026); 403 "User rate limit exceeded" past either | 4,500 units/min (`GMAIL_UNITS_PER_MINUTE`), spent by one process at a time through a lease in the shared store (`lifeos/platform/gmail_gate.py`); burst 120 units, at most 4 calls in flight; the rate halves after a rate-limit answer | per call: list 5, get 20 (60 for full, raw, html and the default), modify 5, trash 20, batchModify 50, send 100, thread get 40 (120 full), labels 1 or 5, profile 1, history 2; an unknown call is priced 20; the lease fails open after 120 s or when the store is unreachable; `LIFEOS_GMAIL_GATE=off` is the kill switch |
| Hostinger MySQL | (shared hosting) | one connection per stage; never hold one open across slow work | lost-connection errors otherwise |
| GitHub Actions | | hourly cron; 30-minute job ceiling; single concurrency group | Variables are NOT masked in logs: every setting is a secret; logs carry counts only |

## Known provider facts (measured, not assumed)
- LinkedIn job search (D203): the public guest search, 4 pages (10 cards each) per run at one request per 1.2 s, AFTER the LinkedIn resolver (both read LinkedIn's guest pages; it answered 429 after about 12 quick requests from a home connection on Oct 17). At most 40 new rows per run. The guest search ignores the remote filter `f_WT` (identical results with and without it), so "remote" is in the keywords.
- Employer boards (D202): 10,692 Greenhouse, Ashby and Lever boards in `boards.json` (5,444 Greenhouse, 3,047 Ashby, 2,201 Lever; seed generated Sep 9 2026 from the Open Jobs board list) plus 3 extras. Up to 1,800 due boards per window on 4 workers (`BOARDS_PER_RUN`; 2,900 in a catch-up window, `BOARDS_CATCHUP_PER_RUN`), oldest due first, in four windows a day (1:00 AM, 7:00 AM, 1:00 PM and 7:00 PM CT, `.github/workflows/boards.yml`, LIFE-854; step timeout 15 minutes, 25 for a catch-up; measured Nov 22 2026: 1,036 boards in 172 s over one held store connection, no 429); a board with no target job is read once a day, one with a target every 6 hours; 1,000 admissions per run (`BOARDS_INGEST_PER_RUN`, limits.py:66; raised from 100 on Oct 25 2026); three 429 answers from one ATS stop reading it for the run (`skipped_after_rate_limit_429`). Board failures are named: `blocked_403`, `rate_limited_429`, `not_found`, `http_<status>`, `network`, `bad_json`, `bad_shape`.
- Lensa job pages returned 403 to GitHub's datacenter addresses even in a real browser (measured before D200), and the D200 follow confirmed it (Oct 17 tick: 1 blocked, 23 stopped). Lensa answers datacenter addresses with a Cloudflare challenge page; a home connection passes. Lensa is retired (D201).
- LinkedIn's external-apply destination is behind a login wall; V7 resolves those jobs by matching the employer's board.
- Jobright's free tier opens an upgrade dialog on the Apply click; the employer link is the page's "Original Job Post" anchor.

## Web acquisition (US Remote direct boards, ours)
`WEB_BOARDS_PER_RUN` 12 due boards per run (limits.py:50). The US Remote list holds 43 sources, 29 of them ready (`us_remote.json`: 28 employers and Apex Systems), so they drain in three runs; the Scale-Up list holds 50 (`scale_up.json`: 38 ready, 12 fallback), four runs; `WEB_REFRESH_HOURS` 6 for a COMPLETE board, plus a deterministic
0-59 minute slot per board; `WEB_RETRY_HOURS` 1 for a FAILED board (doubling, capped at the refresh interval); `WEB_INGEST_PER_RUN` 300 postings
admitted per run, the rest stay PENDING and their board is due again at once. Boards are different hosts, polled `ATS_WORKERS` at a time, one list
request per board (SmartRecruiters pages of 100, at most 30 pages). Stops a board on 403/429.
