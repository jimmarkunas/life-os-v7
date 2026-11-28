# Local tests and the time receipt (policy section 3)

Jim, Nov 27 2026 (LIFE-1099). One run took 24 minutes, most of it a full 7,000-test suite run twice locally while CI runs the same suite on every pull request.

## Rules

1. Run tests locally only with `sh scripts/test-local`: the tests selected for the files changed against main, plus the contracts and repo-wide scanners. It prints `Local tests: <count> tests, <seconds> s`. `sh tools/test.sh --all` and any selection that comes out as the full suite are refused locally (exit 4) unless the `CI` variable is set; CI runs the full suite.
2. Never run a command you expect to take over 3 minutes locally; push and let CI do it.
3. Test once through the real interface (the MCP layer, the API, the UI, as the ticket applies) before opening the pull request, not after the release.
4. Do not send identical parallel requests to test duplicate handling; write a lock test instead.

## Receipt

Every pull request description carries two lines (the PR template has them):

```
Local tests: <the test-local line, or none>
Time: total N min, local tests N min, CI waits N min, rework N min
```

If rework plus avoidable waits exceed 25 percent of the run, add one sentence naming the cause. No new Jira tickets for this. The check `tools/agent_trailer_check.py` (in the required `unit-tests` job) fails a pull request opened from Nov 27 2026, 8:00 PM CT whose description lacks either line.
