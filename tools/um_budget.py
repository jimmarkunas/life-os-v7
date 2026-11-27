"""LIFE-1003 (REQ-85 T2): the spending model for the isolated Honcho experiment. Pure arithmetic, no network.

The shared meter (lifeos/edge/honcho/meter_proxy.py) has one cap for everything, so without a change to it the experiment's own $0.50 ceiling can only be kept by construction: spending is ARMED in steps, each step has a worst-case
exposure computed here, and a step is armed only when (actual spend so far) + (that exposure) + (the dream's exposure) fits the ceiling. A crash of the guard then leaves only a step already inside the bound.
Every parameter below was read from the installed Honcho v3.6.1 image (Nov 26 2026, read-only) and names its source:
  DERIVER_OUT 2500        deriver.py:158 `max_output_tokens or settings.LLM.DEFAULT_MAX_TOKENS` (2500); the provider enforces max_tokens.
  DERIVER_IN 25000        deriver.py:194 passes `max_input_tokens=settings.DERIVER.MAX_INPUT_TOKENS` (25000) to the call.
  DREAM_OUT 8192          specialists.py:868, 1013: Deduction and Induction `get_max_tokens()` return 8192 (not 2,500; the earlier model was wrong here).
  DREAM_ITER (13, 11)     settings DEDUCTION_MAX_TOOL_ITERATIONS 12 and INDUCTION 10, each plus the forced final call; `omni` runs these two only (card_refresh is a separate dream type).
  DREAM_BASE_IN           measured static prompts: deduction about 5,400 tokens, induction about 1,100; tool definitions allowed for with 8,000 and 4,000.
  DREAM_GROWTH 3334       settings.LLM.MAX_TOOL_OUTPUT_CHARS 10000 (3 bytes per token): each tool result adds at most this to the next call. The specialists do NOT pass max_input_tokens, so nothing else trims the input.
Retries (code constants): queue MAX_RETRYABLE_ATTEMPTS 3; deriver internal retry_attempts 3; Anthropic SDK max_retries 2 (3 attempts); the meter charges a reply that breaks off after a 200 at its FULL reservation, so a broken reply is a billed attempt.
Two profiles are kept apart on purpose: MODELED (a message's billed attempts 9 = queue x deriver; the dream retried only by its own queue entry) and STRICT (27 and 9: the SDK retry on a broken reply as well). STRICT cannot fit any $0.50 ceiling; that is the case for a scoped cap in the meter."""

PRICE_IN, PRICE_OUT = 0.10, 0.50  # USD per million tokens; pinned to the meter's PRICES in a test
DERIVER_IN, DERIVER_OUT = 25_000, 2_500
DREAM_OUT = 8_192
DREAM_ITER = (13, 11)
DREAM_BASE_IN = (8_000, 4_000)
DREAM_GROWTH = 3_334
BILLED = {"modeled": 9, "strict": 27}  # billed attempts for one message
DREAM_ATTEMPTS = {
    "modeled": 1,
    "strict": 9,
}  # a dream is retried only when run_dream itself raises (a failed specialist is caught inside it): 1 in the modeled profile, queue x SDK in the strict one
DREAM_ATTEMPTS_QUEUE = 3  # the queue's own limit, reported beside the profiles
CEILING = 0.50
TRIP = 0.40  # the live brake (extra to the arming rule)
TRANCHE = 3  # messages armed at a time
EXPLICIT_MAX = 45  # below Honcho's threshold of 50, so no automatic dream can ever become due
CHECK_DELTA = 0.0001
MESSAGE_CHARS_MAX = 2_000  # settings.LLM.MAX_MESSAGE_CONTENT_CHARS; the fixtures use at most 300
VOLUME_CHARS_MAX = 12_000  # all message and conclusion text in the workspace; the fixtures hold about 6,000
CALL_IN_MAX = DERIVER_IN  # kept for the tests that name it


def call_worst(tokens_in, tokens_out):
    return tokens_in * PRICE_IN / 1_000_000 + tokens_out * PRICE_OUT / 1_000_000


def message_worst(profile="modeled"):
    """One message armed for derivation: every billed attempt at the largest request the deriver can send."""
    return BILLED[profile] * call_worst(DERIVER_IN, DERIVER_OUT)


def tranche_worst(k=TRANCHE, profile="modeled"):
    return k * message_worst(profile)


def dream_attempt_worst():
    total = 0.0
    for n, base in zip(DREAM_ITER, DREAM_BASE_IN):
        total += call_worst(base * n + DREAM_GROWTH * n * (n + 1) // 2, 0) + n * call_worst(0, DREAM_OUT)
    return total


def dream_worst(attempts=None, profile="modeled"):
    return (DREAM_ATTEMPTS[profile] if attempts is None else attempts) * dream_attempt_worst()


def can_arm(spent_delta, exposure, still_to_come, ceiling=CEILING):
    """True when everything already paid, this step's worst case and the worst case of the steps still planned fit the ceiling."""
    return spent_delta >= 0 and spent_delta + exposure + still_to_come <= ceiling + 1e-12


def numbers(messages=20):
    tranches = -(-messages // TRANCHE)
    return {
        "deriver_call_worst_usd": round(call_worst(DERIVER_IN, DERIVER_OUT), 5),
        "dream_call_output_worst_usd": round(call_worst(0, DREAM_OUT), 5),
        "message_worst_modeled_usd": round(message_worst(), 4),
        "message_worst_strict_usd": round(message_worst("strict"), 4),
        "tranche_worst_usd": round(tranche_worst(), 4),
        "tranches": tranches,
        "dream_attempt_worst_usd": round(dream_attempt_worst(), 4),
        "dream_modeled_1_attempt_usd": round(dream_worst(), 4),
        "dream_queue_3_attempts_usd": round(dream_worst(DREAM_ATTEMPTS_QUEUE), 4),
        "dream_strict_usd": round(dream_worst(profile="strict"), 4),
        "ceiling_usd": CEILING,
        "trip_usd": TRIP,
    }
