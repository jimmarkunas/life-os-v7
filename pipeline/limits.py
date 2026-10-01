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
TINYFISH_SEARCH_PER_RUN_LENSA = 300      # ours: Lensa + LinkedIn stay at 400 of the 500/hour allowance
TINYFISH_SEARCH_PER_RUN_LINKEDIN = 100
TINYFISH_BROWSER_USD_PER_MINUTE = 0.002  # paid; default cap $0 (budget.browser_cap_usd)
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

# Jobright (logged in with saved secrets)
JOBRIGHT_PER_RUN = 40                    # one login per run
JOBRIGHT_PAUSE_MS = 1500

# Public ATS board APIs (Greenhouse, Lever, Ashby, SmartRecruiters, Workable): unauthenticated
ATS_WORKERS = 8
ATS_TIMEOUT_SECONDS = 10

# Gmail (REST, gmail.modify): per-user quota 250 units/s; list/get = 5 units, batchModify = 50
GMAIL_REQUESTS_PER_SECOND = 10

# GitHub Actions
ACTIONS_CRON = "hourly"
ACTIONS_JOB_MINUTES = 40                 # workflow timeout-minutes (platform max is far higher)

# Retry rule for jobs whose final link cannot be found yet: try again on later runs, then HOLD (kept, never dropped)
RESOLVE_MAX_ATTEMPTS = 6
