# Provider limits (canon)

Numbers live in `lifeos/platform/limits.py` (code imports them) and `tests/test_limits.py` fails if code drifts above a provider
limit. Rule: stay UNDER the published limit with a margin, stop a stage on the first 429/403, never retry harder.

| Provider | Provider limit | Our ceiling | Notes |
|---|---|---|---|
| TinyFish Fetch | 150 URLs/min, 1,000/day | 100/min (batch 10, 6 s apart), 900/day | free; wallet is $35 and must never be spent |
| TinyFish Search | 30 req/min, 500/hour | one request per 4.4 s per lane, two lanes share the key (27/min total); 100 LinkedIn + 40 sponsor discovery + 14 title watch per run (154 of 500/hour; Lensa's 300 removed, D201); each job costs ONE search (D35) | free, but the account needs Search API access (402 otherwise) |
| TinyFish Agent | $0.016/step | forbidden | |
| Notion API | ~3 req/s average | 2.5/s (0.4 s gap), 60 pages/run | free official API only; 100 child blocks per call; 2,000 chars per rich_text |
| LinkedIn guest pages | unpublished | 1 request per 1.2 s, 150/run, stop on first 429 | never logged in |
| Dice tracking redirects | unpublished | 1 request per second, 40/run, stop on first 429 | anonymous GET only; runner access not yet verified |
| Jobright | unpublished | one login per run, 40 jobs/run, 1.5 s pause | saved secrets; free tier blocks the Apply click, so read the "Original Job Post" link |
| Public ATS board APIs | unpublished | 8 workers, 10 s timeout | Greenhouse, Lever, Ashby, SmartRecruiters, Workable; no auth |
| Gmail REST | 250 quota units/s/user | 10 req/s | list/get = 5 units, batchModify = 50 |
| Hostinger MySQL | (shared hosting) | one connection per stage; never hold one open across slow work | lost-connection errors otherwise |
| GitHub Actions | | hourly cron; 30-minute job ceiling; single concurrency group | Variables are NOT masked in logs: every setting is a secret; logs carry counts only |

## Known provider facts (measured, not assumed)
- LinkedIn job search (D203): the public guest search, 4 pages (10 cards each) per run at one request per 1.2 s, AFTER the LinkedIn resolver (both read LinkedIn's guest pages; it answered 429 after about 12 quick requests from a home connection on Oct 17). At most 40 new rows per run. The guest search ignores the remote filter `f_WT` (identical results with and without it), so "remote" is in the keywords.
- Employer boards (D202): about 10,500 Greenhouse, Ashby and Lever boards from the Open Jobs board list plus the extras. 600 due boards per run on 4 workers (measured: 600 real boards in 65 s, no 429); a board with no target job is read once a day, one with a target every 6 hours; 100 admissions per run; three 429 answers from one ATS stop reading it for the run (`skipped_after_rate_limit_429`). Board failures are named: `blocked_403`, `rate_limited_429`, `not_found`, `http_<status>`, `network`, `bad_json`, `bad_shape`.
- Lensa job pages returned 403 to GitHub's datacenter addresses even in a real browser (measured before D200), and the D200 follow confirmed it (Oct 17 tick: 1 blocked, 23 stopped). Lensa answers datacenter addresses with a Cloudflare challenge page; a home connection passes. Lensa is retired (D201).
- LinkedIn's external-apply destination is behind a login wall; V7 resolves those jobs by matching the employer's board.
- Jobright's free tier opens an upgrade dialog on the Apply click; the employer link is the page's "Original Job Post" anchor.

## Web acquisition (US Remote direct boards, ours)
`WEB_BOARDS_PER_RUN` 12 due boards per run (26 ready boards drain in three runs); `WEB_REFRESH_HOURS` 6 for a COMPLETE board, plus a deterministic
0-59 minute slot per board; `WEB_RETRY_HOURS` 1 for a FAILED board (doubling, capped at the refresh interval); `WEB_INGEST_PER_RUN` 300 postings
admitted per run, the rest stay PENDING and their board is due again at once. Boards are different hosts, polled `ATS_WORKERS` at a time, one list
request per board (SmartRecruiters pages of 100, at most 30 pages). Stops a board on 403/429.
