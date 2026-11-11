# Branch protection on `main` (LIFE-604 item 10)

What GitHub enforces on `main` of `life-os-v7`. These settings live only in GitHub: no file in the repository sets them and no test can read them (the default workflow token cannot, and no extra token is created for this). This page is the written record; the click path below is how anyone checks it.

## Settings as read back (Nov 11 2026, after LIFE-578 turned on "enforce for admins")

| Setting | Value | Why |
| --- | --- | --- |
| Require a pull request before merging | on, 0 approving reviews | every change is a pull request; the check, not a person, is the gate |
| Required status check | `unit-tests` (the job in `.github/workflows/pr-tests.yml`) | a merge needs a green same-head run of the unit suite |
| Require branches to be up to date before merging ("strict") | **off** | decision recorded below |
| Do not allow bypassing the above settings (enforce for administrators) | **on** | an admin merge past a red check was possible before LIFE-578 (PR #931 merged with a failing check) |
| Allow force pushes | off | history on `main` is never rewritten |
| Allow deletions | off | |
| Require linear history / conversation resolution | off | not used |

## How to check it (click path, no commands)

1. Open GitHub, repository `life-os-v7`, tab **Settings**.
2. In the left menu choose **Branches**.
3. Under "Branch protection rules" open the rule for `main` (the Edit button).
4. Compare each row of the table above with what the page shows. The three that matter most: the required check named `unit-tests` is ticked, "Do not allow bypassing the above settings" is ticked, force pushes are not allowed.

## Decision: "require branches to be up to date" stays off

With it on, every pull request would have to be updated with `main` and re-tested after each other merge, and a second pull request waiting behind a first would fail its check each time. The check already runs on the pull request head merged with its base (see `pr-tests.yml`), and every merge to `main` runs the full suite again, so a bad combination shows up on `main` within minutes. This is the default recorded in LIFE-604 approval 4; Jim can turn it on by ticking "Require branches to be up to date before merging" on the same page. Nothing else in the repository depends on it.

## Unverified

Jim has not yet walked the click path above; the values in the table were read through the GitHub API by an administrator session on the date at the top. If a row differs on the page, the page is right and this file is stale.
