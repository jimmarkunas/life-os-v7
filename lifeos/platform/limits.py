"""Provider limits, canonized once. Code imports these; docs/LIMITS.md explains them; tests keep them in sync.
Rule: every outbound call path stays UNDER the provider's published limit, with a margin, and stops on 429/403 instead
of retrying harder. Change a number here only after re-reading the provider's current docs."""

# TinyFish (Jim's plan; wallet is $35 and must never be spent)
TINYFISH_FETCH_PER_MINUTE = 150          # provider limit
TINYFISH_FETCH_PER_DAY = 1000            # provider limit
TINYFISH_FETCH_DAILY_CAP = 900           # ours (budget.FETCH_DAILY_CAP)
TINYFISH_FETCH_BATCH = 10                # max URLs per call
TINYFISH_SEARCH_PER_MINUTE = 30          # provider limit (free, needs Search API access)
TINYFISH_SEARCH_PER_HOUR = 500           # provider limit
TINYFISH_SEARCH_LANES = 2                # resolve lanes that run at the same time and share the one key
TINYFISH_SEARCH_GAP_SECONDS = 2.2 * TINYFISH_SEARCH_LANES   # ours: 13.6/min per lane, 27/min across both
TINYFISH_SEARCH_GAP_SOLO = 2.2             # ours: 27/min when a lane has the key to itself (the 30/min provider limit)
TINYFISH_SEARCH_PER_RUN_LINKEDIN = 100
TINYFISH_AGENT_ALLOWED = False           # $0.016/step - forbidden

# Notion (free official API, integration token)
NOTION_REQUESTS_PER_SECOND = 3           # provider average limit
NOTION_GAP_SECONDS = 0.4                 # ours: 2.5/s
NOTION_MAX_CHILD_BLOCKS = 100            # per create/append call
NOTION_MAX_RICH_TEXT_CHARS = 2000        # per rich_text item
NOTION_PAGE_SIZE = 100                   # query page size
NOTION_PER_RUN = 120                     # ours: pages created per call; Jim raised it from 60 on Oct 25 2026 so the drain pass outpaces new READY jobs
AUDIT_PAGES_PER_RUN = 40                 # ours: pages the precision audit reads and trashes per run (two calls each), so it ends inside its 180 s tick budget (D246)
BILLS_PAID_PER_RUN = 20                  # paid commands are individually read back and verified

# LinkedIn (public guest pages only; never logged in)
LINKEDIN_GUEST_GAP_SECONDS = 1.2         # ours; stop the stage on the first HTTP 429
LINKEDIN_PER_RUN = 150
LINKEDIN_SEARCH_PAGES_PER_RUN = 4          # D203: guest search pages per run (10 cards each), one per LINKEDIN_GUEST_GAP_SECONDS; LinkedIn answered 429 after about 12 quick requests (Oct 17)
LINKEDIN_SEARCH_NEW_PER_RUN = 40           # D203: new rows per run, so search never crowds the newsletter in the shared resolve lane
DICE_GAP_SECONDS = 1.0                   # polite spacing between anonymous tracking-link requests
DICE_PER_RUN = 15                        # Dice is low value: a small bounded batch, never a long job
DICE_DEADLINE_MINUTES = 3                # stop starting batches after this; the workflow step is also capped at 4 minutes
DICE_TIMEOUT_SECONDS = 6                 # per request
DICE_MAX_HOPS = 4                        # redirects followed per link

# Jobright (logged in with saved secrets)
JOBRIGHT_PER_RUN = 40                    # one login per run
JOBRIGHT_PAUSE_MS = 1500

# Public ATS board APIs (Greenhouse, Lever, Ashby, SmartRecruiters, Workable): unauthenticated
ATS_WORKERS = 8
ATS_TIMEOUT_SECONDS = 10

# Web acquisition (US Remote direct boards): ours, spread over the hourly runs
WEB_BOARDS_PER_RUN = 12                  # due boards polled per run (26 ready boards drain in three runs)
WEB_REFRESH_HOURS = 6                    # a COMPLETE board is due again after this
WEB_RETRY_HOURS = 1                      # a FAILED board is retried after this (doubling, capped at WEB_REFRESH_HOURS)
WEB_FULL_EVERY = 4                       # D216: every 4th read of a quiet board is a full diff, whatever its validators or fingerprint say
WEB_BACKOFF_MAX = 3                      # D216: a board that stays unchanged is read up to 3x less often (a daily seed board: every 3 days, LIFE-76 Part 2); back to 1x on any change
WEB_INGEST_PER_RUN = 300                 # new or changed postings admitted into Jobs OS per run; the rest stay PENDING

# Employer boards (D202): the Open Jobs board list (CC0) plus the extras, read by the same web pass; ours, polite to three shared hosts
BOARDS_PER_RUN = 900                     # due seed boards read per run; Jim raised it from 600 on Oct 25 2026 (the drain read ~1,050 boards an hour for 19 ticks with 0 HTTP 429s)
BOARDS_DEAD_AFTER = 5                    # a board that has answered not_found this many times in a row is listed in `dead_ids` (counts line) for a skip-list review; nothing is skipped by this number
BOARDS_DEAD_IDS_MAX = 600                # the most ids one pass prints
BOARDS_REFRESH_HOURS = 24                # a COMPLETE board that lists no target job is due again after this (also caps a failing board's retry)
BOARDS_HOT_HOURS = 6                     # a board that lists a target job is due again after this, like a priority board
BOARDS_INGEST_PER_RUN = 250              # postings admitted per run; the rest stay PENDING and drain next run. Jim raised it from 100 on Oct 25 2026 (1,294 pending at noon)
BOARDS_WORKERS = 4                       # most seed boards share the Greenhouse, Ashby and Lever API hosts
BOARDS_RATE_LIMIT_STOP = 3               # this many 429 answers from one ATS stops reading that ATS for the run
BOARDS_BACKFILL_PER_RUN = 450            # D217: never-read seed boards a backfill run reads (only when BOARDS_BACKFILL=on): ~10,500 drain in about a day of ticks

# Gmail (REST, gmail.modify): per-user quota 250 units/s; list/get = 5 units, batchModify = 50
GMAIL_REQUESTS_PER_SECOND = 10

# GitHub Actions
ACTIONS_CRON = "hourly"
ACTIONS_JOB_MINUTES = 40                 # workflow timeout-minutes (platform max is far higher)

# Retry rule for jobs whose final link cannot be found yet: try again on later runs, then HOLD (kept, never dropped)
RESOLVE_MAX_ATTEMPTS = 6
ENRICH_MAX_ATTEMPTS = 12                 # ours: a page that stays unreadable this many hourly runs goes on HOLD (visible, not retried)
