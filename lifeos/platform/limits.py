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
TINYFISH_SEARCH_SOLO_AFTER_SECONDS = 540   # Lensa only: LinkedIn's 100 searches at the shared pace are done well inside 9 minutes
LENSA_DEADLINE_MINUTES = 15               # ours: Lensa stops starting batches after this (a run must not hold the one-run-at-a-time queue for 30 minutes); the rest waits for the next run
TINYFISH_SEARCH_PER_RUN_LENSA = 300      # ours: Lensa + LinkedIn stay at 400 of the 500/hour allowance
TINYFISH_SEARCH_PER_RUN_LINKEDIN = 100
TINYFISH_AGENT_ALLOWED = False           # $0.016/step - forbidden

# Notion (free official API, integration token)
NOTION_REQUESTS_PER_SECOND = 3           # provider average limit
NOTION_GAP_SECONDS = 0.4                 # ours: 2.5/s
NOTION_MAX_CHILD_BLOCKS = 100            # per create/append call
NOTION_MAX_RICH_TEXT_CHARS = 2000        # per rich_text item
NOTION_PAGE_SIZE = 100                   # query page size
NOTION_PER_RUN = 60                      # ours: pages created per run

# LinkedIn (public guest pages only; never logged in)
LINKEDIN_GUEST_GAP_SECONDS = 1.2         # ours; stop the stage on the first HTTP 429
LINKEDIN_PER_RUN = 150
DICE_GAP_SECONDS = 1.0                   # polite spacing between anonymous tracking-link requests
DICE_PER_RUN = 40                        # bounded before the shared resolver deadline

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
WEB_INGEST_PER_RUN = 300                 # new or changed postings admitted into Jobs OS per run; the rest stay PENDING

# Gmail (REST, gmail.modify): per-user quota 250 units/s; list/get = 5 units, batchModify = 50
GMAIL_REQUESTS_PER_SECOND = 10

# GitHub Actions
ACTIONS_CRON = "hourly"
ACTIONS_JOB_MINUTES = 40                 # workflow timeout-minutes (platform max is far higher)

# Retry rule for jobs whose final link cannot be found yet: try again on later runs, then HOLD (kept, never dropped)
RESOLVE_MAX_ATTEMPTS = 6
ENRICH_MAX_ATTEMPTS = 12                 # ours: a page that stays unreadable this many hourly runs goes on HOLD (visible, not retried)
