# Runs OK today (LIFE-975)

**The fourth Command Center card counts today's hourly ticks that finished without a failure.** It replaced Platform Stability and its seven-day clean-days counter (LIFE-694, built for the cancelled LIFE-686). Code: `lifeos/platform/runs_today.py` (the count), `lifeos/dcc/card.py` (`runs_card`, drawn by the existing Command Center renderer). It reads GitHub's own run history with the dcc job's read-only `GITHUB_TOKEN`; no new workflow, cron, token, store or phone alert.

| Rule | Detail |
|---|---|
| What is a run | a completed run of `hourly.yml` titled `tick` (scheduled, or dispatched with the tick box); runs titled `manual`, test dispatches and cancelled runs are left out |
| Today | since midnight America/Chicago (06:00 UTC in winter, 05:00 in summer) |
| A tick failed when | the tick, or a follow-on run it started, concluded failure, timed out or failed to start |
| Follow-on runs | `domains.yml` runs titled `domains (after tick)`, and `recruiters.yml` and `capacity-card.yml` runs; each belongs to the latest tick that started before it |
| Source unreadable | `—` with `GitHub unreadable`, never a healthy count |

| Card | When |
|---|---|
| `18 / 18` · Runs OK today | every tick today succeeded |
| `16 / 18` · red `2 failed today` | some failed; the label links to the latest failed run, earlier ones as red arrows |
| `16 / 18` · red `Last run failed` | the latest tick failed (nothing has succeeded since); links to it |
| `Paused` · Runs OK today | `hourly.yml` is disabled |
| `0 / 0` · Runs OK today | no tick has finished yet today |

The gray line is `GitHub · <time> CT`, the time the card was written. The alert-gateway record of the retired clean-days counter is untouched.
