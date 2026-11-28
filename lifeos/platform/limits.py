"""Provider limits, defined once: code imports these, docs/LIMITS.md explains them, tests keep them in sync.

Every outbound call path stays UNDER the provider's published limit, with a margin, and stops on 429/403 instead of
retrying harder. Change a number here only after re-reading the provider's current docs."""

TINYFISH_FETCH_PER_DAY = 1000  # provider limit
TINYFISH_FETCH_DAILY_CAP = 900  # ours (budget.FETCH_DAILY_CAP)
TINYFISH_FETCH_BATCH = 10  # max URLs per call
TINYFISH_SEARCH_PER_MINUTE = 30
TINYFISH_SEARCH_PER_HOUR = 500  # provider limit
TINYFISH_SEARCH_LANES = 2
TINYFISH_SEARCH_GAP_SECONDS = 2.2 * TINYFISH_SEARCH_LANES
TINYFISH_SEARCH_PER_RUN_LINKEDIN = 100
TINYFISH_AGENT_ALLOWED = False  # $0.016/step: forbidden

NOTION_REQUESTS_PER_SECOND = 3
NOTION_BUDGET_PER_MINUTE = 180
NOTION_GAP_SECONDS = 0.4  # ours: 2.5/s in one process
NOTION_RETRY_BACKOFF = (0.5, 1.0, 2.0)
NOTION_RATE_RETRIES = 6
NOTION_WRITE_RATE_RETRIES = NOTION_RATE_RETRIES  # old name kept for its caller
NOTION_RETRY_AFTER_CAP_SECONDS = 60
NOTION_RATE_WAIT_BUDGET_SECONDS = 180
NOTION_RATE_SLOWDOWN = 2.0
NOTION_GAP_MAX_SECONDS = 3.2
NOTION_IDEMPOTENT_METHODS = ("GET", "DELETE")
NOTION_MAX_CHILD_BLOCKS = 100  # per create/append call
NOTION_MAX_RICH_TEXT_CHARS = 2000  # per rich_text item
NOTION_PAGE_SIZE = 100  # query page size
NOTION_PER_RUN = 120  # ours: pages created per call
AUDIT_PAGES_PER_RUN = 40
BILLS_PAID_PER_RUN = 20

LINKEDIN_GUEST_GAP_SECONDS = 1.2
LINKEDIN_SEARCH_PAGES_PER_RUN = 4
LINKEDIN_SEARCH_NEW_PER_RUN = 40
DICE_GAP_SECONDS = 1.0
DICE_PER_RUN = 15
DICE_DEADLINE_MINUTES = 3
DICE_TIMEOUT_SECONDS = 6  # per request
DICE_MAX_HOPS = 4  # redirects followed per link

ATS_WORKERS = 8
ATS_TIMEOUT_SECONDS = 10

WEB_BOARDS_PER_RUN = 12
WEB_BOARD_SECONDS = 30
WEB_REFRESH_HOURS = 6
WEB_RETRY_HOURS = 1
WEB_FULL_EVERY = 4
WEB_BACKOFF_MAX = 3
WEB_INGEST_PER_RUN = 300

BOARDS_PER_RUN = 1800
BOARDS_TICK_DEADLINE_MINUTES = 4
BOARDS_CATCHUP_PER_RUN = 2900
BOARDS_DEAD_AFTER = 5
BOARDS_DEAD_RETRY_DAYS = 7
BOARDS_DEAD_IDS_MAX = 600  # the most ids one pass prints
BOARDS_REFRESH_HOURS = 24
BOARDS_HOT_HOURS = 6
BOARDS_INGEST_PER_RUN = 1000
BOARDS_WORKERS = 4
BOARDS_RATE_LIMIT_STOP = 3
BOARDS_BACKFILL_PER_RUN = 450

RESOLVE_MAX_ATTEMPTS = 6
ENRICH_MAX_ATTEMPTS = 12

# Gmail (quota page re-read Nov 27 2026): 6,000 quota units per user per minute and 1,200,000 per project per minute;
# a 403 "User rate limit exceeded" past either. Every LIFE OS process shares one Gmail user, so one budget covers all.
GMAIL_UNITS_PER_MINUTE_PROVIDER = 6000  # provider limit, per user
GMAIL_UNITS_PER_MINUTE = 4500  # ours: 75 percent, spent by one process at a time (the lease)
GMAIL_BURST_UNITS = 120  # a process may start with two full-format reads in hand
GMAIL_MAX_IN_FLIGHT = 4  # ours: the concurrent-request limit is not published
GMAIL_RATE_SLOWDOWN = 0.5  # the rate is halved after a rate-limit answer
GMAIL_MIN_UNITS_PER_MINUTE = 600  # the floor after repeated slowdowns
GMAIL_LEASE_SECONDS = 8  # a holder that goes quiet loses the lease after this
GMAIL_LEASE_RENEW_SECONDS = 3  # a holder renews at most this often
GMAIL_LEASE_POLL_SECONDS = 1
GMAIL_LEASE_WAIT_MAX_SECONDS = 90  # past this a caller goes ahead without the lease (fail open)
GMAIL_LEASE_HOLD_MAX_SECONDS = 15  # a holder lets go after this long, so a waiter is served
GMAIL_LEASE_REST_SECONDS = 2  # and rests this long (more than the poll), then queues like any other
GMAIL_LEASE_OPEN_SECONDS = 60  # after giving up a wait a caller goes ahead without the lease this long
GMAIL_UNIT_DEFAULT = 20  # a call this table does not know is priced high
GMAIL_UNIT_SEND = 100
GMAIL_UNIT_BATCH_MODIFY = 50
GMAIL_UNIT_PROFILE = 1
GMAIL_UNIT_HISTORY = 2
GMAIL_UNIT_LABEL_READ = 1
GMAIL_UNIT_LABEL_WRITE = 5
GMAIL_UNIT_LIST = 5
GMAIL_UNIT_MODIFY = 5
GMAIL_UNIT_TRASH = 20
GMAIL_UNIT_ATTACHMENT = 20
GMAIL_UNIT_MESSAGE_LIGHT = 20  # messages.get with format metadata or minimal
GMAIL_UNIT_MESSAGE_FULL = 60  # format full, raw or html (and the default, which is full)
GMAIL_UNIT_THREAD_LIGHT = 40
GMAIL_UNIT_THREAD_FULL = 120
GMAIL_UNIT_THREAD_LIST = 10
