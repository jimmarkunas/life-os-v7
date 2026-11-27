#!/bin/sh
# Installs the git hooks that keep personal email addresses and real Notion ids out of this public repository:
# pre-commit scans the staged changes, commit-msg scans the message. One install covers every worktree of the clone.
set -eu
root=$(git rev-parse --show-toplevel)
hooks=$(cd "$root" && git rev-parse --path-format=absolute --git-path hooks)
mkdir -p "$hooks"
install_one() {
  name=$1; body=$2
  if [ -e "$hooks/$name" ] && ! grep -q "public-repo-guard" "$hooks/$name"; then
    echo "install_hooks: $hooks/$name already exists and is not ours; add this line to it by hand: $body" >&2
    return 1
  fi
  printf '#!/bin/sh\n# public-repo-guard (tools/install_hooks.sh)\n%s\n' "$body" > "$hooks/$name"
  chmod 755 "$hooks/$name"
}
guard='"$(git rev-parse --show-toplevel)/lifeos/platform/public_repo_guard.py"'
install_one pre-commit "exec python3 $guard --staged"
install_one commit-msg "exec python3 $guard --message \"\$1\""
echo "install_hooks: pre-commit and commit-msg installed in $hooks"
