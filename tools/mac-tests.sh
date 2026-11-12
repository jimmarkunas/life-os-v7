#!/bin/sh
# LIFE-553 (moved out of AGENTS.md by LIFE-606; LIFE-605 makes this the one local test command that matches CI).
# The Mac's python3 is 3.9 without PyYAML and requirements.lock has Linux hashes, so a bare run fails tests CI passes.
# From the repo root: builds a Python 3.12 environment once from the locked versions, puts it first on PATH, runs the suite; expect OK.
set -e
[ -d ~/.venvs/lifeos-tests ] || { python3.12 -m venv ~/.venvs/lifeos-tests && grep -hE '^[A-Za-z0-9_.-]+==' requirements.lock requirements-test.lock | sed 's/ *\\$//' | ~/.venvs/lifeos-tests/bin/pip install -q -r /dev/stdin; }; PATH=~/.venvs/lifeos-tests/bin:$PATH python -m unittest discover -s tests -t .
