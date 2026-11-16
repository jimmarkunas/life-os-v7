#!/usr/bin/env bash
# LIFE-760: the ONE gate to pass before `git push` and `sh tools/automerge.sh`. It runs what CI runs for this branch: the tests the change can affect (tools/test.sh: tests.testindex select) plus the repo-wide contract tests CI always runs
# (docstyle, privacy, config registry, settings, workflows, bws-plain, the policy manifest hash; tests/testindex.py REPO_WIDE and tests/contracts). One short PASS or FAIL line with seconds.
#   sh tools/preflight.sh        commit first (CI sees commits only); refuses on a dirty tree
#   sh tools/preflight.sh --all  everything, like a merge to main
# On PASS it writes the marker <git dir>/lifeos-preflight-ok holding the HEAD sha; tools/automerge.sh refuses to arm auto-merge unless the marker equals the current HEAD (any new commit needs a new preflight).
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 3
marker="$(git rev-parse --absolute-git-dir 2>/dev/null)/lifeos-preflight-ok"
rm -f "$marker"
case "${1:-}" in ''|--all) ;; -h|--help) sed -n 2,7p "$0"; exit 0 ;; *) echo "preflight.sh: unknown option $1 (try --help)" >&2; exit 2 ;; esac
if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
  echo "preflight: FAIL: uncommitted changes; CI sees commits only. Commit (add files by name), then run again." >&2
  exit 1
fi
git fetch -q origin main 2>/dev/null || echo "preflight: note: could not refresh origin/main; selecting against the last fetched copy"
start="$(date +%s)"
out="$(mktemp)"; trap 'rm -f "$out"' EXIT
bash "$ROOT/tools/test.sh" "$@" > "$out" 2>&1; status=$?
grep -E '^test\.sh: (failing|[0-9]+ tests ran|note|no usable)' "$out"
seconds=$(( $(date +%s) - start ))
sha="$(git rev-parse HEAD)"
if [ "$status" -eq 0 ]; then
  printf '%s\n' "$sha" > "$marker"
  echo "preflight: PASS $(printf %s "$sha" | cut -c1-8) in ${seconds} s; auto-merge may be armed"
  exit 0
fi
echo "preflight: FAIL (exit $status) in ${seconds} s; full output follows" >&2
tail -n 60 "$out" >&2
exit 1
