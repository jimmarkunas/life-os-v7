#!/usr/bin/env python3
"""Remove git worktrees whose work is already merged into main.

Safe by construction: a worktree is removed only if ALL hold
  - it is not the primary checkout or the current directory's worktree
  - it lives under /private/tmp/claude-501/, <LIFEOS>/wt-*, <LIFEOS>/worktrees/ or life-os-v7/.claude/worktrees/
  - it is not locked, is on a named branch, and has no uncommitted/untracked files
  - the branch tip equals the head commit of a MERGED GitHub PR with that branch name
    (works for squash merges, which `git branch --merged` cannot see), or the tip
    is already an ancestor of origin/main.

Default is a dry run. Pass --apply to remove. Branches are never deleted.
Usage: tools/prune_merged_worktrees.py [--apply] [--repo OWNER/NAME] [--allow-prefix PATH ...]
"""
import argparse
import json
import os
import subprocess
import sys

ALLOWED_PREFIXES = (
    "/private/tmp/claude-501/",
    "/Users/jimmarkunas/Developer/LIFEOS/wt-",
    "/Users/jimmarkunas/Developer/LIFEOS/worktrees/",
    "/Users/jimmarkunas/Developer/LIFEOS/life-os-v7/.claude/worktrees/",
)


def run(*args, cwd=None, check=True):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        sys.exit(f"{' '.join(args)} failed: {r.stderr.strip()}")
    return r


def worktrees():
    out = run("git", "worktree", "list", "--porcelain").stdout
    cur = {}
    for line in out.splitlines() + [""]:
        if not line:
            if cur:
                yield cur
            cur = {}
            continue
        k, _, v = line.partition(" ")
        cur[k] = v or True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--repo", default="jimmarkunas/life-os-v7")
    ap.add_argument("--allow-prefix", action="append", default=[],
                    help="extra path prefix to consider (repeatable); default scope is unchanged without it")
    a = ap.parse_args()

    top = run("git", "rev-parse", "--show-toplevel").stdout.strip()
    run("git", "fetch", "--quiet", "origin", "main", check=False)
    prs = json.loads(run("gh", "pr", "list", "--repo", a.repo, "--state", "merged",
                         "--limit", "2000", "--json", "headRefName,headRefOid").stdout)
    merged = {}
    for p in prs:
        merged.setdefault(p["headRefName"], set()).add(p["headRefOid"])

    remove, keep = [], []
    for i, w in enumerate(worktrees()):
        path, head, branch = w["worktree"], w.get("HEAD", ""), w.get("branch", "")
        branch = branch.removeprefix("refs/heads/") if branch else ""
        if i == 0 or os.path.realpath(path) == os.path.realpath(top):
            continue  # primary checkout / current worktree
        if not path.startswith(ALLOWED_PREFIXES + tuple(a.allow_prefix)):
            keep.append((path, "outside allowed locations")); continue
        if "locked" in w:
            keep.append((path, "locked")); continue
        if not branch:
            keep.append((path, "detached HEAD")); continue
        if not os.path.isdir(path):
            remove.append((path, branch, "directory missing (prune)")); continue
        if run("git", "status", "--porcelain", cwd=path, check=False).stdout.strip():
            keep.append((path, "uncommitted changes")); continue
        in_main = run("git", "merge-base", "--is-ancestor", head, "origin/main", check=False).returncode == 0
        if head in merged.get(branch, ()) or in_main:
            remove.append((path, branch, "merged"))
        else:
            keep.append((path, f"{branch}: not merged"))

    print(f"REMOVE ({len(remove)}):")
    for p, b, why in remove:
        print(f"  {p}  [{b}] {why}")
    print(f"KEEP ({len(keep)}):")
    for p, why in keep:
        print(f"  {p}  -- {why}")
    if not a.apply:
        print("\nDry run. Re-run with --apply to remove.")
        return
    for p, b, _ in remove:
        r = run("git", "worktree", "remove", p, check=False)
        print(("removed " if r.returncode == 0 else f"FAILED ({r.stderr.strip()}) ") + p)
    run("git", "worktree", "prune")


if __name__ == "__main__":
    main()
