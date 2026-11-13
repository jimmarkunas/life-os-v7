#!/bin/sh
# LIFE-663: update YOUR OWN pushed life/* branch after a rebase or amend, with an explicit lease. It is the only force-style push agents may run.
#   sh tools/push_life_branch.sh
# It takes no options: any argument is ignored (counted, never read), so the target cannot be changed from the command line. It pushes ONE ref, the
# current branch, and only when that branch is life/<something>. It refuses main, master, any other branch and a detached HEAD. It never deletes and never
# uses a plus refspec. For an existing remote branch it fetches that branch, then pushes with --force-with-lease pinned to the exact tip it just fetched,
# so a commit someone else pushed after the fetch is never overwritten (git refuses and its message is shown). A branch not yet on origin gets a plain push -u.
# This file is protected (.github/protected_files.py): a change needs Jim's typed yes and the label protected-file-approved.
set -u
REMOTE=origin
extra=$#
fail() { echo "push_life_branch: $1" >&2; exit "${2:-2}"; }

branch=$(git symbolic-ref --short -q HEAD) || fail "refused: HEAD is detached (no branch to push)"
case "$branch" in
  life/?*) ;;
  *) fail "refused: the current branch is '$branch'; only a life/<name> branch can be pushed with this script" ;;
esac
[ "$extra" -eq 0 ] || echo "push_life_branch: ignoring $extra argument(s); the target is always the current branch" >&2

ref="refs/heads/$branch"
new=$(git rev-parse --verify -q "$ref") || fail "cannot read the local branch tip" 3
git ls-remote --exit-code --heads "$REMOTE" "$ref" >/dev/null 2>&1
case $? in
  0) exists=1 ;;
  2) exists=0 ;;
  *) fail "cannot reach $REMOTE (nothing pushed)" 3 ;;
esac

if [ "$exists" -eq 0 ]; then
  git push -u "$REMOTE" "$ref:$ref" || fail "push of the new branch failed (git's message is above)" 4
  echo "push_life_branch: $branch new on $REMOTE, tip $(echo "$new" | cut -c1-12)"
  exit 0
fi

git fetch -q "$REMOTE" "$ref" || fail "fetch of $branch failed (nothing pushed)" 3
old=$(git rev-parse --verify -q FETCH_HEAD) || fail "cannot read the fetched tip" 3
if git push --force-with-lease="$ref:$old" "$REMOTE" "$ref:$ref"; then
  echo "push_life_branch: $branch $(echo "$old" | cut -c1-12) -> $(echo "$new" | cut -c1-12)"
else
  fail "refused by git: the lease on $branch ($(echo "$old" | cut -c1-12)) no longer holds or the push was rejected (git's message is above); fetch, rebase onto the remote tip and run again" 4
fi
