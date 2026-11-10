"""LIFE-519 step 7: a pull request that touches a protected privacy file fails the required `unit-tests` check unless it carries the label `protected-file-approved`
(added by Jim, or by an agent only after Jim's yes is on the Jira ticket). Stdlib only; no secret; no network. Prints file NAMES only.

  python .github/protected_files.py         reads the PR's changed files from git (HEAD^1 vs HEAD of the merge commit) and the labels from $PR_LABELS (comma separated)
"""
import os
import subprocess
import sys

PROTECTED = ("docs/PRIVACY.md", "tests/contracts/test_privacy.py", ".claude/settings.json")
LABEL = "protected-file-approved"


def check(changed, labels):
    """-> (ok, message). `changed` is a list of file paths; `labels` a list of label names."""
    hits = sorted(set(changed) & set(PROTECTED))
    if not hits:
        return True, "no protected file touched"
    if LABEL in labels:
        return True, "protected file(s) touched with the approval label: " + ", ".join(hits)
    return False, ("Protected privacy file(s) changed without approval: " + ", ".join(hits) +
                   ". Jim must approve (on the Jira ticket); then the label `" + LABEL + "` is added to this PR and the check runs again.")


def main():
    changed = subprocess.run(["git", "diff", "--name-only", "HEAD^1", "HEAD"], capture_output=True, text=True, check=True).stdout.split()
    labels = [x.strip() for x in os.environ.get("PR_LABELS", "").split(",") if x.strip()]
    ok, message = check(changed, labels)
    print(("::notice::" if ok else "::error::") + message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
