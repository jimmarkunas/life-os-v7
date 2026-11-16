#!/usr/bin/env bash
# LIFE-743: arm auto-merge for a pull request without the stale-head race. After `git push`, GitHub can take a few seconds to show the new head, and `gh pr merge --match-head-commit` fails until it does.
#   sh tools/automerge.sh <pr-number>
# It waits (at most 60 s, LIFEOS_AUTOMERGE_WAIT=seconds to change) until the PR head equals the local HEAD, then runs: gh pr merge <pr> --auto --squash --match-head-commit <sha>
# Exit 0 only when auto-merge is armed. Exit 1: the head never matched (push first, or the branch is not this PR's). Exit 2: usage. It never merges by itself: GitHub merges on green same-head CI.
set -u
pr="${1:-}"
case "$pr" in ''|*[!0-9]*) echo "usage: sh tools/automerge.sh <pr-number>" >&2; exit 2 ;; esac
sha="$(git rev-parse HEAD)" || exit 2
limit="${LIFEOS_AUTOMERGE_WAIT:-60}"
waited=0
while :; do
  head="$(gh pr view "$pr" --json headRefOid --jq .headRefOid 2>/dev/null || true)"
  [ "$head" = "$sha" ] && break
  if [ "$waited" -ge "$limit" ]; then
    echo "automerge.sh: PR $pr head is '${head:-unreadable}', local HEAD is $sha after ${waited} s; push this branch first" >&2
    exit 1
  fi
  sleep 3; waited=$((waited + 3))
done
gh pr merge "$pr" --auto --squash --match-head-commit "$sha" || exit $?
echo "automerge.sh: auto-merge armed on PR $pr at $(printf %s "$sha" | cut -c1-8) (GitHub merges when same-head CI is green)"
