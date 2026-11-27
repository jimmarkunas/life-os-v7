# Proof and tests, task-specific rules (policy section 9)

Moved here from `docs/life-os-development-policy.md` section 9 with no change in meaning. Read it when a change touches runtime code, Jobs stages or Notion pages.

- **Fix on the real failing shape.** A defect seen live is fixed and proved on that input or a structure-keeping fixture. For Jobs qualification: real job description → requirement rows → non-null Fit → correct disposition.
- **Runtime-facing changes:** prove the production entry point calls the change, its inputs load, the happy path completes, missing input fails closed, writes use the existing owner, and read-back is wired.
- **Jobs increments:** test for the future source count before copying a pattern.
- **Full width:** a page V7 creates under a page uses `template=notion.FULL_WIDTH`; a repo test enforces it.
- **Code comments say what and why now;** ticket history lives in Jira and git, not in code.
