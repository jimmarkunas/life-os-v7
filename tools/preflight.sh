#!/usr/bin/env bash
# LIFE-760: the ONE gate to pass before `git push` and `sh tools/automerge.sh`. LIFE-763: the default is the QUICK gate, a few seconds to about a minute in parallel processes: every contract test (docstyle, privacy, config registry, settings, workflows, net size,
# ruff advisory, the policy manifest hash), the repo-wide scanners (tests/testindex.py REPO_WIDE) and the tests that name or mirror each changed file (tests/quickselect.py). It never runs the whole suite: CI does (Jim, Nov 16 2026). One short PASS or FAIL line with seconds.
#   sh tools/preflight.sh        commit first (CI sees commits only); refuses on a dirty tree
#   sh tools/preflight.sh --all  everything in parallel shards, like a merge to main (only when you want it; CI runs it anyway)
# LIFE-765: before testing it runs tools/regen.py --commit (a new stage's registry rows, one commit of generated files only), so a new stage is its code and its workflow step.
# LIFE-779: it also runs the workflow gate (tools/workflow_gate.py): no new secret, no wider permissions = pre-approved, nothing to ask; otherwise it stops with `[waiting on] Jim: <the one thing>` until LIFEOS_JIM_APPROVED holds the recorded [approvals] line.
# LIFE-908: a stop that is only a wider `permissions` block already recorded as a CHECKED item in the claimed ticket's Jim must do first section passes with no typed yes: the gate reads that ticket's live issue (Jira settings, or the unmodified Atlassian MCP result saved to a file named by LIFEOS_JIRA_READ, at most 15 minutes old). Anything the item does not name still stops.
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
if [ -f "$ROOT/tools/regen.py" ]; then   # LIFE-765: a new stage's registry rows are written and committed here, so nobody runs tools/regen.py by hand
  if ! regen_out="$(bash "$ROOT/tools/test.sh" --regen 2>&1)"; then
    echo "preflight: FAIL: tools/regen.py needs you (a stage with a loop and no try needs a verdict, or it could not commit):" >&2
    printf '%s\n' "$regen_out" | tail -n 20 >&2
    exit 1
  fi
  printf '%s\n' "$regen_out" | grep -E '^regen: committed' || true
fi
git fetch -q origin main 2>/dev/null || echo "preflight: note: could not refresh origin/main; selecting against the last fetched copy"
start="$(date +%s)"
if ! gate_out="$(bash "$ROOT/tools/test.sh" --workflow-gate 2>&1)"; then   # LIFE-779: the standing pre-approval; a workflow change stops only for a wider permission, a new secret or credential, a deletion or spend
  printf '%s\n' "$gate_out" >&2
  echo "preflight: FAIL: a workflow change needs Jim's yes (the [waiting on] line above). If the ticket's Jim must do first section already records it (checked, naming this path, job and scopes), read the ticket with the Atlassian MCP, save the unmodified result to a file and run: LIFEOS_JIRA_READ=<file> sh tools/preflight.sh. Otherwise ask once, record one [approvals] line, then run: LIFEOS_JIM_APPROVED='<that line>' sh tools/preflight.sh" >&2
  exit 1
fi
printf '%s\n' "$gate_out" | grep -E '^workflow gate: (ok|Jim approved)' || true
out="$(mktemp)"; trap 'rm -f "$out"' EXIT
if [ "${1:-}" = --all ]; then bash "$ROOT/tools/test.sh" --all > "$out" 2>&1; status=$?; else bash "$ROOT/tools/test.sh" --quick > "$out" 2>&1; status=$?; fi
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
