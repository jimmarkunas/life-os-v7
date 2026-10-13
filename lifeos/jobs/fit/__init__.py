"""Phase 2 - deterministic job-fit scoring (no LLM anywhere in the chain). See docs/FIT_MODEL.md.

The engine here is generic and public. Everything personal (evidence, corrections, role families, private exclusions)
is injected at run time as a profile (profile.py) and never committed (D16).
"""
MODEL_VERSION = "6"
UNIVERSAL_FIT_FLOOR = 72                  # Jim-approved admission floor for every lane (Jobs OS canon); lanes.py and the Go / No-Go bar both read this one value
GO_THRESHOLD = UNIVERSAL_FIT_FLOOR
