"""LIFE-1003 (REQ-85 T2): the spending model for the isolated Honcho experiment. Pure arithmetic, no network.

The shared meter (lifeos/edge/honcho/meter_proxy.py) has one cap for everything, so the experiment's own $0.50 ceiling is kept by construction: spending is ARMED in steps, each step has a worst-case exposure computed here,
and a step is armed only when (actual spend so far) + (that exposure) + (the exposure still to come) fits the ceiling. A crash of the guard can then only leave an already-armed step running, which is inside the bound.
Assumptions (each one is a named constant, checked against the code in a test or recorded as an assumption in the Jira dry run):
  CALL_IN_MAX: the largest request a deriver or dream call sends (observed 4,776 tokens in 290 calls; 12,000 allows 2.5x).
  BILLED_ATTEMPTS: queue (3) x deriver (3) for a message whose answer is rejected after it was paid for; a failed request costs nothing.
  DREAM_ITER: deduction 12 + induction 10 (settings) plus 4 margin calls; tool output per call is capped at 10,000 characters (about 3,334 tokens), so a call's input grows by that much per iteration."""

PRICE_IN, PRICE_OUT = 0.10, 0.50                         # USD per million tokens; pinned to the meter's PRICES in a test
OUT_MAX = 4096                                           # the proxy's fallback when a request names no max_tokens (Honcho's own default is 2500, so this is the safe side)
CALL_IN_MAX = 12_000
BILLED_ATTEMPTS = 9
DREAM_ATTEMPTS = 3                                       # the queue's MAX_RETRYABLE_ATTEMPTS for a dream that raises (a specialist failure is caught inside the dream and does not retry it)
DREAM_BASE_IN = 5_000
DREAM_GROWTH = 3_334
DREAM_ITER = (12, 10, 4)
CEILING = 0.50
TRIP = 0.40                                              # the live brake (extra to the arming rule): at this much spend the guard disarms
TRANCHE = 3                                              # messages armed at a time
EXPLICIT_MAX = 45                                        # below Honcho's threshold of 50, so no automatic dream can ever become due
CHECK_DELTA = 0.0001


def call_worst(tokens_in, tokens_out=OUT_MAX):
    return tokens_in * PRICE_IN / 1_000_000 + tokens_out * PRICE_OUT / 1_000_000


def message_worst():
    """One message armed for derivation: every billed attempt at the largest request."""
    return BILLED_ATTEMPTS * call_worst(CALL_IN_MAX)


def tranche_worst(k=TRANCHE):
    return k * message_worst()


def dream_attempt_worst():
    total = 0.0
    for n in DREAM_ITER:
        tokens_in = DREAM_BASE_IN * n + DREAM_GROWTH * n * (n + 1) // 2
        total += call_worst(tokens_in, 0) + n * call_worst(0, OUT_MAX)
    return total


def dream_worst(attempts=DREAM_ATTEMPTS):
    return attempts * dream_attempt_worst()


def can_arm(spent_delta, exposure, still_to_come, ceiling=CEILING):
    """True when everything already paid, this step's worst case and the worst case of the steps still planned fit the ceiling."""
    return spent_delta >= 0 and spent_delta + exposure + still_to_come <= ceiling + 1e-12


def numbers(messages=20):
    tranches = -(-messages // TRANCHE)
    return {"call_worst_usd": round(call_worst(CALL_IN_MAX), 5), "message_worst_usd": round(message_worst(), 4), "tranche_worst_usd": round(tranche_worst(), 4), "tranches": tranches,
            "dream_attempt_worst_usd": round(dream_attempt_worst(), 4), "dream_worst_usd": round(dream_worst(), 4), "all_seeding_worst_usd": round(tranches * tranche_worst(), 3),
            "ceiling_usd": CEILING, "trip_usd": TRIP}
