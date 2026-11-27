# Agents sharing the repository (policy section 3)

Jim, Nov 27 2026 (LIFE-1096). MC01, OC and Codex work in the same repository at the same time. These rules keep them from colliding. No bot accounts, tokens or keys are added: all agents still push as the one existing identity.

## Agent trailer

Every commit and every pull request description carries one line `Agent: MC01`, `Agent: OC` or `Agent: Codex` (a human-made pull request may use `Agent: Jim`). Put it last in the commit message, after any `Co-Authored-By` line. The pull request also carries the label `agent:mc01`, `agent:oc` or `agent:codex` (`gh pr create --label agent:mc01`). The check `tools/agent_trailer_check.py` runs in the required `unit-tests` job and fails a pull request that has a commit or a description without the trailer. It never reads or writes secrets and does not run on the merge queue. It applies to pull requests opened from Nov 27 2026, 6:00 PM CT; a pull request opened earlier is never checked, so work in flight is not held up.

## One Production Contract editor at a time

Before editing `docs/life-os-production-contract.md`, the agent posts a claim on Buzz community `pilot` with its ticket key. The claim covers one pull request and ends when that pull request merges (or is closed). An unmerged claim older than 30 minutes may be taken over after one ping on Buzz to its holder. Only the contract file is covered, not the code around it. The version bump stays manual.

## Paths line in every ticket

Each ticket lists the paths it will touch, on one line (`Paths:` in the ticket template). An agent about to start a ticket whose paths overlap a ticket that is In Progress waits, or is sequenced by Claude (the orchestrator).

No merge queue: GitHub offers it only to organization-owned repositories and this one is a personal repository (Jim, Nov 27 2026). A pull request that is behind main is rebased and re-checked as before.
