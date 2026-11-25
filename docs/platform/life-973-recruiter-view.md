# LIFE-973: the Recruiter Outreach and Hiring Pipeline cards are live Notion views

Jim, Nov 25 2026. Both cards on the Daily Report are linked database views Jim edits in place: Recruiter Outreach (the Recruiters data source, `Action Status = Needs Action`) and Hiring Pipeline (the Job Ledger).
V7 no longer writes a table in either callout. What it still does, every hourly run:

| What | Where | Rule |
|---|---|---|
| Count line `N need action · M overdue` | the Recruiter Outreach callout, block named by the `RECRUITER_COUNT_BLOCK_ID` settings row | only the gray text before the link `All recruiter records ↗` is replaced (`lifeos/recruiters/outreach_card.py`); needs `--live` and `RECRUITER_OUTREACH=on` |
| Status sync | Recruiters rows (`lifeos/recruiters/stage.py`) | Completed / Dismissed / Declined: Done on, Active off, Needs review off. Needs Action: Done off, Active on. Waiting and blank rows are not synced. Action Status is the only source of truth |
| Reply-by date | Recruiters rows | a Needs Action row with no Follow-up Date gets one business day after First Contact, else after Last Contact (Central Time). A set date is never replaced |
| Company / Role / Client | Recruiters rows (`lifeos/recruiters/fill.py`, `backfill.py`) | company: signature, display name (`Name | Agency`), non-free-mail sender domain; role: subject title, then the first title in the body; client: when the message names it. Words from the message only; `Not stated` when it says nothing; a set value is never replaced. Rows older than the 14-day scan are read again through their Source URL, 15 a run. The log carries the row id and the source of each value, never a name |
| Decision sync | Job Ledger (`lifeos/hiring/decision_sync.py`, in the hiring stage) | a row whose Hiring Decision is set and that lacks Decision On, a history entry or Freshness Status Closed gets exactly what is missing (Decision On = today in Chicago; one `hand-set` history line; Closed). Hiring Decision itself is never changed |

## The Close service stays up for the other screens

The Close service on Oracle (`lifeos-close.socket`, `:47443`) also serves Mark as Sent (`/sent`), the referral status (`/referral`), Build resume (`/resume`), Manage and Reopen (`/manage`), Mark Complete (`/complete`) and the Alert settings (`/alerts`).
Stopping the whole service would end those, so this change retires only what LIFE-973 names: the opportunity Close screen (`/?o=`, now a 404), the recruiter screens (`/recruiter`, `/brief`), and the card redraw worker (`lifeos-close-redraw.socket` and `.service`).
Switching the whole service off is a separate decision for Jim.

## Deploy and rollback on Oracle (`ssh opc@lifeos-edge-01`, sudo as opc)

Deploy: the release timer ships the merged tree (`sudo systemctl start lifeos-close-release.service`), and `install-close.sh install` removes the redraw units. By hand, the same result:

    sudo systemctl disable --now lifeos-close-redraw.socket lifeos-close-redraw.service
    sudo rm -f /etc/systemd/system/lifeos-close-redraw.socket /etc/systemd/system/lifeos-close-redraw.service
    sudo systemctl daemon-reload
    sudo rm -rf /run/lifeos-close-redraw /etc/lifeos-close/redraw.json

Proof: `systemctl list-units 'lifeos-close*'` shows only `lifeos-close.socket` and the release timer; `curl` of `/recruiter?r=x` and `/?o=x` from the tailnet answers 404.

Rollback: set `/opt/lifeos-close/current` back to the previous release (`readlink /opt/lifeos-close/previous`; `sudo ln -sfn "$(readlink /opt/lifeos-close/previous)" /opt/lifeos-close/current.new && sudo mv -T /opt/lifeos-close/current.new /opt/lifeos-close/current`), pause the auto-release timer
(`sudo systemctl stop lifeos-close-release.timer`), and reinstall the redraw units from the previous release's `install-close.sh install`. Revert the merge commit for the Daily Report side.

## Settings rows

New: `RECRUITER_COUNT_BLOCK_ID` (the callout `4233c5a059268087b278cf739e1159f7`). Now unused and safe to retire by hand: `HIRING_TABLE_BLOCK_ID`, `HIRING_STATUS_BLOCK_ID`, `V7_CLOSE_BASE_URL` (the Close links are gone).
