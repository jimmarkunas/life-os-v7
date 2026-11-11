# Receipt template (LIFE-605)

Every ticket receipt, every running progress comment and the final summary on a bridge ticket use this shape. Copy it, delete what does not apply, never add a seventh part. Counts and names only; no secret, token, id or address.

The first line is the time: run `python -m lifeos.platform.ct_stamp` in the same turn you post and paste its line (never type a time). Name each ticket and pull request with its plain-words purpose the first time it appears.

```markdown
**<One sentence: what is now true, with the number that proves it>.** <time from ct_stamp>. <agent name>, under <ticket that asked for it>. PR #<n>, merged on green same-head CI (or: not merged, waiting for <who>).

**1. Context in plain words:** <what the ticket or PR is for, as if the reader has not seen it>

**2. What changed:** <table or list: file or system, change, PR link>

**3. How it was verified:** <table: acceptance line, evidence (test name, run id, drift row, read-back), result. A new test shows failing on the old code, then passing. A log line alone is never proof>

**4. What is unverified:** <everything not checked, said plainly; anything not measured is "not verified">

**5. Next steps:** <what happens next and who does it; what it waits on, not a date>

**6. How to verify (click only):** <where to click or which comment to read; never a command for Jim>
```

Rules that go with it: Jim reads in Central Time (CT, 12-hour); say what an item waits on instead of dates or durations in a receipt that others will reuse; one running comment per ticket that is edited, plus one final receipt; find something unexpected, say it under "What is unverified" or in a short "Found, not done" line before part 5.
