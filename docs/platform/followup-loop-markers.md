# How a Followup open loop starts (LIFE-716, Nov 17 2026)

Followup turns an item **you wrote yourself** into an open loop only when its title **starts with a marker**. Nothing else ever makes a loop: unread mail, a finished meeting, an open Jira ticket or a plain Todoist task never does. Mail and calendar items are never read for loops (that needs message bodies or attendees, which Followup does not read).

Where it looks: Todoist tasks, Jira tickets, Notion pages and Interview OS pages in the workspaces already connected (Personal, Bytalos). Never TGP.

| Title starts with | Becomes | Whose move |
|---|---|---|
| `Waiting on …`, `Waiting for …`, `WFR: …` | waiting on someone | them |
| `Follow up …`, `Follow-up …`, `FU: …` | a follow-up you owe | you |
| `I owe …`, `Owe …`, `Promised …`, `Promise: …` | a promise | you |
| `Deliverable: …` | a deliverable | you |
| `Value-add: …` | a value-add after an interaction | you |

* The words after the marker are the **outcome** (the Inbox subject). The same outcome in two systems is one loop; the same person with a different outcome is a different loop.
* The item's **due date** is the checkpoint. With a due date the loop is active: Needs Action Today (due), Waiting/Aging (marker says waiting) or Upcoming. **Without a due date it is only a suggestion** (Review Needed): kept apart, never counted, never sent to your Inbox.
* Active loops are sent to the Agent Inbox as items from **Followup** (one per loop, updated in place). Jira stays the only task list: the Inbox item links to the evidence and never copies the ticket or task.
* A loop closes only on strong proof (the outcome ended, or you resolve it). Completing the task is not proof.
