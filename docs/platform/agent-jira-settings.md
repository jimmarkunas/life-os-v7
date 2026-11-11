# Keeping Jira answers small (LIFE-605)

The Atlassian tool in an agent session returns far more than the answer if you let it. Sizes below are characters returned for one real ticket (LIFE-605) on Nov 11 2026, **estimated by reading the returned text and rounded to the nearest 500; they are not counted by a program**. The settings are the part that matters.

| Operation | Default call returns about | With the setting returns about | Setting |
| --- | --- | --- | --- |
| Read one ticket | 7,000 | 1,000 | pass `fields` (for example `["status"]`, `["status","summary"]` or `["description"]`); never omit it; never `expand: changelog` (a change history of a busy ticket is tens of thousands, mostly old description text) |
| Search with JQL | 14,500 for two tickets | 1,500 for two | pass `fields: ["status"]` and look at keys and status only |
| Add a comment | 1 x the comment + 500 | same | no smaller form exists; keep one running comment and edit it rarely, because every edit echoes the whole body |
| Edit a field or the description | 7,000 | same | no smaller form exists in this tool; append a comment instead of rewriting a description; change a description only when the plan itself changed |
| Transition (start 14, Done 15) | 6,000 to 7,000 | same | no smaller form exists; transition once per state change and do not re-read the ticket afterwards |

Rules: never read a whole ticket to find one field; never read a ticket you already read this session unless something may have changed (then read `fields: ["status"]`); a key that moved to the archive (LIFETMP) resolves to a different ticket, so check the title before trusting a status. Where a response cannot be made smaller, the table says so: "after" equals "before".
