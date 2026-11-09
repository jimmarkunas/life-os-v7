# Concord Room for Orca — pilot v0.1.0

**Product:** [LOS-95](https://jimmarkunas.atlassian.net/browse/LOS-95) · **Delivery:** [LIFE-411](https://jimmarkunas.atlassian.net/browse/LIFE-411)

A **live view of real messages** inside Orca, with a composer (v0.2.0) for sending prompts to agents that are present. It uses the existing Concord CLI and local work-state, not a duplicate backend, mock feed, second database, or other chat platform. Claude Code and Codex send and answer messages via Concord MCP in their Orca agent sessions. The panel displays their identities, content, receipt states, message IDs, and reply relationships. Plugin capability is limited to reading its own settings; its Node worker executes only a fixed, shell-free, read-only Concord CLI command.

## Required host and authorization gates

- Orca desktop >=1.4.246, actual version to be verified on the Mac.
- Node.js 22+ for tests and Concord's documented version requirement.
- Concord CLI installed and both Claude Code and Codex adapters configured and reachable: do **not** assume a green unit test proves this.
- Select one disposable local Git repository, not a production/active LIFE OS worktree. Avoid global mode, Concord Cloud, Oracle, new services, or new paid capacity.
- Inspect every change `concord setup` proposes, especially `AGENTS.md`, Claude instruction files, and global `~/.codex/config.toml`. Back up or use an isolated HOME/config if needed. Do not overwrite established development policy or silently trust hooks.
- Orca must approve any trusted-worker plugin; the plugin cannot approve itself. Read the worker code and the requested capabilities first. Do not install into a production repo before the scope, host, workspace and Jira authorization have been independently checked.

## Local reproduction in Orca (Claude Code implementer)

1. **Read-only host check:** `orca --version`, `command -v orca`, `node --version`, `command -v concord`, `concord --version`, `concord adapters status` and `concord adapters doctor`. Record results. Stop if missing.
2. Inspect `orca-plugin.json`, `worker/main.mjs`, the current Orca plugin API, repo path and `concord setup` file effects. Run `node --test tools/orca-concord-room/test/worker.test.mjs` and `orca plugin check tools/orca-concord-room`. Report PASS only when both actually pass on the Mac.
3. In Orca Settings > Plugins > Concord Room set **repoRoot** to the exact absolute directory containing the isolated Concord store, and **concordExecutable** to the executable found by `command -v concord`. These are settings, not shell commands.
4. Start `orca plugin dev watch tools/orca-concord-room` from the branch checkout. Review and approve the trusted-worker permission in Orca. Open the **Concord Room** panel.
5. Start independently identified Claude Code and Codex sessions in the same disposable repository through Orca, with both Concord MCP integrations active. Confirm `concord adapters status` and `concord who`. Claude sends Codex a *new* Concord `update_work` prompt with a unique `idempotency_key` and exact reachable `to_agent_id`. Codex receives it via its own real adapter, then answers using `update_work` reply to the original message ID. Do **not** copy the content manually between chats.
6. Verify in the Orca panel **both** message records, separate agents, timestamps, IDs, genuine delivery/read receipts and reply relation. Read `concord -C <repo-root> messages --json --limit 80` independently and compare IDs and states. `queued_pull` is NOT proof of live delivery. Restart the panel and confirm continuity.
7. Record the actual host/version, adapter status, reproduced commands, exact non-sensitive message IDs, panel screenshot, test results and Jim's UAT decision in LIFE-411 and LOS-95. Do not disclose secret values, private chat content or client data.

## Acceptance for this single vertical slice

- [ ] Orca native plugin check and load succeeds on the actual Mac.
- [ ] Claude Code and Codex register distinct real sessions with reachable delivery.
- [ ] Claude sends a real prompt; Codex independently receives it and replies with **zero** human relay.
- [ ] Both messages appear in the SAME Orca Concord Room panel with matching IDs/receipts from the CLI.
- [ ] Missing Concord, malformed CLI output or invalid repository fails closed; stale data is not shown as healthy.
- [ ] Restart retains the original Concord messages with no duplicates, and Jim accepts the observed outcome.

## Limitations and STOP

This v0.1.0 panel is **read-only**; prompts are authored in native Orca agent chats, not by typing into this panel. Future human composer, protected agent delegation, cross-tenant enforcement and STOP/claims are separate LOS-95 milestones. Concord message presence does not establish Jira authorization or enforce one writer. Do not assume human-readable source screenshots, PR/CI success, or protocol fixtures equal a live two-agent result. If live adapter delivery fails, label it BLOCKED with the exact reason—no manual relay and no fake PASS.

**Rollback:** Stop the dev watcher; disable/uninstall the plugin; inspect configuration diffs and reverse only the isolated pilot's known changes. No broad reset of user-level Claude/Codex/Orca settings.

**Sources:** [Orca plugin authoring](https://github.com/stablyai/orca/blob/main/docs/reference/plugin-authoring.md), [Concord MCP](https://github.com/Get-Concord-AI/concord-mcp), [Concord Codex integration](https://github.com/Get-Concord-AI/concord-mcp/blob/main/docs/codex.md).


## v0.2.0: composer (human to agent)

- The panel lists agents currently present (`concord who`) and lets you send one a prompt. It sends through Concord's own MCP `update_work` (operation `prompt`) as a human sender, `human:<macOS username>` unless you set **Your sender id**. It never sends as an agent identity.
- The worker refuses empty or over-4000-character text, an absent or malformed recipient, sending to yourself, and a malformed sender id. It reports success only when Concord returns a message ID, and shows Concord's own delivery state (`queued_pull` is not delivered).
- The worker derives `concord-mcp` from the folder of the configured Concord executable (or looks up `concord-mcp` on PATH).
- Replies to a human sender are stored as `sent`; a human has no live inbox, so there are no read receipts for you.
- Orca did not ask for a fresh approval for this version, because its declared access (`settings:own`) is unchanged and trusted-worker code can already run processes. Review the worker code before trusting this plugin.
- Not yet covered: chat-vs-terminal labels, other repositories, Codex chat agents (see the LOS-95 follow-up tickets).

## v0.3.0: chat layout

- The panel is now a plain HTML frame (`panel/index.html`) instead of Orca's component panel, so it can behave like a normal chat: messages run oldest to newest, the view stays at the newest message unless you scroll up (a "New messages" button appears), and the composer is pinned to the bottom (Enter sends, Shift+Enter adds a line).
- It talks only to this plugin's own worker through `orca.plugin.request`; message text is always inserted with `textContent`, never as HTML. The worker and manifest access are unchanged (`settings:own`).
- Orca cannot script HTML panels from the command line (`orca plugin shot --act` refuses them), so the page was exercised in a browser against a stand-in bridge: typed send, error path (draft kept, nothing added), auto-scroll, and roster/recipient list.

## v0.5.0: center tab and Jira tickets

- **Center column:** Orca plugin panels open only in the right sidebar, and a plugin cannot add a "+" menu entry. The supported way into the center column is a custom editor: open any file ending in `.concord-room` (for example `Concord Room.concord-room`, an empty file in the repo) in Orca and the room opens as a full-width tab. It is the same page and script as the sidebar panel (`editor/main.ts` loads `panel/index.html` and `panel/room.js`); scripts must be external files because inline scripts do not run in the editor frame.
- **Create a Jira ticket:** the "+ Jira" button opens a review sheet (project, type, title, description pre-filled from the last N messages, labels, optional parent). Nothing is created until you click Create. The worker runs `orca jira create` through **Orca's own Jira connection**, so the plugin holds no Jira credentials. Creating a ticket is an external write to your Jira, made only by that click; it needs an explicit confirm flag, validates every field, and fails closed (it shows what Orca returned if no issue key came back, so check Jira before retrying).
- Built-in single-agent chats are Orca's own UI; a plugin cannot add buttons there.
