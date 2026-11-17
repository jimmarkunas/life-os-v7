#!/usr/bin/env bash
# LIFE-605: the ONE local test command. It runs what CI (pr-tests.yml) runs: on a branch, the tests the change can affect (tests.testindex select, plus tests/contracts; with no docs/test-index.json it runs everything, LIFE-644); on main or with --all,
# everything in two parallel shards. It finds a Python 3.12 environment by itself, prints the failing test names and a summary line, and exits non-zero on any failure.
#   sh tools/test.sh            the CI selection for the commits on this branch (needs the changes committed: CI also sees commits only)
#   sh tools/test.sh --all      everything, like a merge to main (parallel shards, one per core up to 8)
#   sh tools/test.sh --quick    the contracts, the repo-wide scanners and the tests that name or mirror each changed file, in parallel (what preflight runs); --quick --list prints them
#   sh tools/test.sh --list     only print which Python and which tests it would use
#   sh tools/test.sh --workflow-gate   (preflight runs it) LIFE-779: the standing pre-approval check on the workflow files this branch changed (tools/workflow_gate.py); exit 5 = ask Jim once
#   sh tools/test.sh --regen    (preflight runs it) write and commit the registry rows a new stage needs, with the same Python; refuses a dirty tree
#   sh tools/test.sh --setup    same as `sh tools/setup-dev.sh`: build or repair ~/.venvs/lifeos-tests from the locked versions (the Mac's own python3 is 3.9; refuses on the Oracle VM)
# Python is looked for in this order: $LIFEOS_PYTHON, the repo .venv (also the main checkout's, for a git worktree), ~/.venvs/lifeos-tests, python3.12, python3.13. It must be 3.12 or newer and import PyYAML.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 3
mode=select
for arg in "$@"; do
  case "$arg" in
    --all) mode=all ;;
    --quick) mode=quick ;;   # LIFE-763: the contracts, the repo-wide scanners and the tests that name or mirror each changed file, in parallel processes; never the whole suite (CI runs that)
    --list) list=1 ;;
    --regen) regen=1 ;;
    --workflow-gate) wfgate=1 ;;
    --setup) setup=1 ;;
    -h|--help) sed -n 2,9p "$0"; exit 0 ;;
    *) echo "test.sh: unknown option $arg (try --help)" >&2; exit 2 ;;
  esac
done

good() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1 && "$1" -c 'import yaml' >/dev/null 2>&1; }

if [ "${setup:-}" = 1 ]; then
  exec bash "$ROOT/tools/setup-dev.sh"   # LIFE-743: the one setup command; also installs ruff and coverage from the locks
fi

common="$(git rev-parse --git-common-dir 2>/dev/null)"
PY=""
for candidate in "${LIFEOS_PYTHON:-}" "$ROOT/.venv/bin/python" "$common/../.venv/bin/python" "$HOME/.venvs/lifeos-tests/bin/python" "$(command -v python3.12 2>/dev/null)" /opt/homebrew/bin/python3.12 "$(command -v python3.13 2>/dev/null)"; do
  [ -n "$candidate" ] && [ -x "$candidate" ] && good "$candidate" && { PY="$candidate"; break; }
done
if [ -z "$PY" ]; then
  echo "test.sh: no usable Python found. Needed: Python 3.12 or newer with PyYAML (CI uses 3.12). Looked at: \$LIFEOS_PYTHON, .venv, ~/.venvs/lifeos-tests, python3.12, python3.13." >&2
  echo "test.sh: on the Mac run 'sh tools/setup-dev.sh' once. On the Oracle VM install nothing without Jim's typed yes; name this missing piece to Jim." >&2
  exit 3
fi
version="$("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"

if [ "${wfgate:-}" = 1 ]; then   # LIFE-779: a workflow change with no new secret and no wider permissions needs no approval; this says so, or names the one thing Jim must answer
  wbase="$(git merge-base HEAD origin/main 2>/dev/null || true)"
  [ -n "$wbase" ] || { echo "workflow gate: no origin/main to compare with"; exit 3; }
  exec "$PY" "$ROOT/tools/workflow_gate.py" --base "$wbase" --head HEAD
fi
[ "${regen:-}" = 1 ] && exec "$PY" "$ROOT/tools/regen.py" --commit   # LIFE-765: what preflight runs so a new stage needs no hand-run regen
branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
cores="$(getconf _NPROCESSORS_ONLN 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 2)"; [ "$cores" -gt 8 ] && cores=8; [ "$cores" -lt 2 ] && cores=2   # LIFE-763: shards = cores (2 to 8), not a fixed 2
if [ "$mode" = quick ]; then
  export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" PATH="$(dirname "$PY"):$PATH"
  qbase="$(git merge-base HEAD origin/main 2>/dev/null || true)"
  [ -n "$qbase" ] || { echo "test.sh: note: no origin/main to compare with; use --all"; exit 3; }
  [ "${list:-}" = 1 ] && { echo "test.sh: python $version at $PY; quick selection:"; exec "$PY" -m tests.quickselect select --base "$qbase"; }
  echo "test.sh: python $version at $PY; running the quick gate (contracts, repo-wide scanners, tests that name or mirror the changed files)"
  exec "$PY" -m tests.quickselect run --base "$qbase" --jobs "$cores"
fi
selected="$(mktemp)"; trap 'rm -f "$selected" "$selected".*' EXIT
if [ "$mode" = all ] || [ "$branch" = main ] || ! git rev-parse --verify -q origin/main >/dev/null 2>&1; then
  echo FULL > "$selected"
else
  base="$(git merge-base HEAD origin/main)"
  [ "$base" = "$(git rev-parse HEAD)" ] && echo FULL > "$selected" || "$PY" -m tests.testindex select --base "$base" > "$selected" 2>/dev/null || echo FULL > "$selected"
fi
if [ "$(head -n 1 "$selected")" = FULL ]; then what="everything ($cores parallel shards)"; else what="$(wc -l < "$selected" | tr -d ' ') selected test targets"; fi
if [ "$mode" != all ] && [ -n "$(git status --porcelain 2>/dev/null)" ]; then echo "test.sh: note: uncommitted changes are not part of the selection (CI sees commits only); commit first, or use --all"; fi
echo "test.sh: python $version at $PY; running $what"
[ "${list:-}" = 1 ] && exit 0

start="$(date +%s)"; status=0
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" PATH="$(dirname "$PY"):$PATH"
if [ "$(head -n 1 "$selected")" = FULL ]; then
  pids=""; log=""
  for k in $(seq 1 "$cores"); do
    "$PY" -m tests.shard "$k" "$cores" > "$selected.$k" 2>&1 & pids="$pids $!"
    log="$log $selected.$k"
  done
  for pid in $pids; do wait "$pid" || status=$?; done
else
  # shellcheck disable=SC2046
  "$PY" -m unittest $(cat "$selected") > "$selected.1" 2>&1 || status=$?
  log="$selected.1"
fi
# shellcheck disable=SC2086
ran="$(grep -hE '^Ran [0-9]+ tests?' $log | awk '{n += $2} END {print n + 0}')"
# shellcheck disable=SC2086
grep -hE '^(FAIL|ERROR):' $log | sed -E 's/ \(.*//' | sort -u | sed 's/^/test.sh: failing: /'
echo "test.sh: ${ran} tests ran, exit ${status}, $(( $(date +%s) - start )) s ($what)"
exit "$status"
