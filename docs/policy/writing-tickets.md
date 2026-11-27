# Writing tickets, extra rules (policy section 7)

Moved here from `docs/life-os-development-policy.md` section 7 with no change in meaning. Read it when a ticket replaces cancelled work or changes UI.

**Reuse, never redo:** a ticket that replaces cancelled work reads the old tickets' comments, PRs and data first. Its Before section cites what already exists (merged PRs, live data, stored identifiers, Done evidence) and its steps resume from there.

**UI first:** for new or changed UI, show the smallest working preview in the real surface (with synthetic data where live data is not authorized), and get Jim's acceptance before expanding the backend. For changed results in an existing UI, show current versus intended output. A UI-free change names its downstream outcome and proof.
