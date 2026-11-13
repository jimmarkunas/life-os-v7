#!/usr/bin/env bash
# LIFE-605: the ONE local test command. It runs what CI (pr-tests.yml) runs: on a branch, the tests the change can affect (tests.testindex select, plus tests/contracts; with no docs/test-index.json it runs everything, LIFE-644); on main or with --all,
# everything in two parallel shards. It finds a Python 3.12 environment by itself, prints the failing test names and a summary line, and exits non-zero on any failure.
#   sh tools/test.sh            the CI selection for the commits on this branch (needs the changes committed: CI also sees commits only)
#   sh tools/test.sh --all      everything, like a merge to main
#   sh tools/test.sh --list     only print which Python and which tests it would use
#   sh tools/test.sh --setup    build ~/.venvs/lifeos-tests once from the locked versions (the Mac's own python3 is 3.9; never run this on the Oracle VM without Jim's typed yes)
# Python is looked for in this order: $LIFEOS_PYTHON, the repo .venv (also the main checkout's, for a git worktree), ~/.venvs/lifeos-tests, python3.12, python3.13. It must be 3.12 or newer and import PyYAML.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 3
mode=select
for arg in "$@"; do
  case "$arg" in
    --all) mode=all ;;
    --list) list=1 ;;
    --setup) setup=1 ;;
    -h|--help) sed -n 2,9p "$0"; exit 0 ;;
    *) echo "test.sh: unknown option $arg (try --help)" >&2; exit 2 ;;
  esac
done

good() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1 && "$1" -c 'import yaml' >/dev/null 2>&1; }

if [ "${setup:-}" = 1 ]; then
  command -v python3.12 >/dev/null || { echo "test.sh --setup: python3.12 is not installed on this host; install Python 3.12 first (on the Mac with Homebrew, the formula named python 3.12)" >&2; exit 3; }
  [ -d "$HOME/.venvs/lifeos-tests" ] || { python3.12 -m venv "$HOME/.venvs/lifeos-tests" && grep -hE '^[A-Za-z0-9_.-]+==' requirements.lock requirements-test.lock | sed 's/ *\\$//' | "$HOME/.venvs/lifeos-tests/bin/pip" install -q -r /dev/stdin; } || exit 3
  echo "test.sh: environment ready at $HOME/.venvs/lifeos-tests"; exit 0
fi

common="$(git rev-parse --git-common-dir 2>/dev/null)"
PY=""
for candidate in "${LIFEOS_PYTHON:-}" "$ROOT/.venv/bin/python" "$common/../.venv/bin/python" "$HOME/.venvs/lifeos-tests/bin/python" "$(command -v python3.12 2>/dev/null)" "$(command -v python3.13 2>/dev/null)"; do
  [ -n "$candidate" ] && [ -x "$candidate" ] && good "$candidate" && { PY="$candidate"; break; }
done
if [ -z "$PY" ]; then
  echo "test.sh: no usable Python found. Needed: Python 3.12 or newer with PyYAML (CI uses 3.12). Looked at: \$LIFEOS_PYTHON, .venv, ~/.venvs/lifeos-tests, python3.12, python3.13." >&2
  echo "test.sh: on the Mac run 'sh tools/test.sh --setup' once. On the Oracle VM install nothing without Jim's typed yes; name this missing piece to Jim." >&2
  exit 3
fi
version="$("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"

branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
selected="$(mktemp)"; trap 'rm -f "$selected" "$selected".*' EXIT
if [ "$mode" = all ] || [ "$branch" = main ] || ! git rev-parse --verify -q origin/main >/dev/null 2>&1; then
  echo FULL > "$selected"
else
  base="$(git merge-base HEAD origin/main)"
  [ "$base" = "$(git rev-parse HEAD)" ] && echo FULL > "$selected" || "$PY" -m tests.testindex select --base "$base" > "$selected" 2>/dev/null || echo FULL > "$selected"
fi
if [ "$(head -n 1 "$selected")" = FULL ]; then what="everything (two parallel shards)"; else what="$(wc -l < "$selected" | tr -d ' ') selected test targets"; fi
if [ "$mode" != all ] && [ -n "$(git status --porcelain 2>/dev/null)" ]; then echo "test.sh: note: uncommitted changes are not part of the selection (CI sees commits only); commit first, or use --all"; fi
echo "test.sh: python $version at $PY; running $what"
[ "${list:-}" = 1 ] && exit 0

start="$(date +%s)"; status=0
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" PATH="$(dirname "$PY"):$PATH"
if [ "$(head -n 1 "$selected")" = FULL ]; then
  "$PY" -m tests.shard 1 2 > "$selected.1" 2>&1 & first=$!
  "$PY" -m tests.shard 2 2 > "$selected.2" 2>&1 & second=$!
  wait "$first" || status=$?
  wait "$second" || status=$?
  log="$selected.1 $selected.2"
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
