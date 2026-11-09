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
- **Jira:** replaced in v0.9.0 (see below). The earlier "create a ticket" sheet was a wrong guess and has been removed.
- Built-in single-agent chats are Orca's own UI; a plugin cannot add buttons there.

## v0.6.0: Buzz look

- Agent colours and avatars match the Buzz set (Jim's decision, Nov 8 2026): Claude Code rust `#B5532F`, Claude Chat terracotta `#D97757`, ChatGPT green `#10A37F`, Codex indigo `#5B5FEF`; the humans stay blue. The avatars are Jim's own Buzz PNGs resized to 80 px and embedded in `panel/avatars.js`. Unnamed agents are called "Claude Code" / "Codex" (numbered when several), or whatever you rename them to.

## v0.7.0: handoffs (local synthetic prototype, LIFE-375 / LIFE-403)

- **What it is:** a handoff is a *pointer* to a Jira issue (key, scope, short brief), never an order. Handoffs appear in the room as pending cards; **nothing is sent until you click Accept**, and you choose the target (an open agent, or a new Claude Code / Codex chat in the configured repo). Reject records the refusal.
- **Source:** a local folder (`handoffDir` setting, default `~/.concord-pilot/handoffs`) stands in for the Buzz channel. `node scripts/synthetic-handoff.mjs` writes unsigned synthetic handoffs there. No network, no Oracle, no Buzz.
- **Policy (shaped like LIFE-403's):** `policy.json` in that folder lists allowed senders (`chatgpt-chat`, `claude-chat`), allowed targets (open agents, new `claude`/`codex` chats) and must set `requireAccept: true`. It is re-read on every call; if it is missing or malformed, every handoff is refused. Invalid, duplicate-nonce, oversized or symlinked files are reported, never trusted.
- **Audit:** one JSON line per accept/reject in `audit.log`, without the scope or brief text.
- **Delivery:** to an open agent as a Concord prompt from `relay:orca-room`; to a new chat with `orca agent launch --surface chat --prompt` (restricted to the configured repo). The prompt tells the receiver to check the Jira issue's release state and writer claim and to reply `REFUSED: <reason>` otherwise.
- **Observed limit (Nov 8 2026):** a running Claude agent that received an accepted handoff as a Concord prompt did NOT act on it. It said the message came from a peer agent, not from Jim, and asked Jim to confirm in its own session. A running session therefore treats any injected message as untrusted; a *new* chat started with the handoff as its first message does not have that problem.

## v0.8.0: brand magenta, sharper avatars, terminal delivery

- **Look:** your messages use the PBDS magenta `#ff2fae` (ink text `#090909`), from the "JM Personal Brand" palette in the bot studio code. Avatars are embedded at 192 px and drawn at 32 px (20 px in the presence chips), so they stay sharp on retina screens.
- **Terminal delivery (policy `targets.terminals: true`, off if the key is missing):** a handoff you route yourself can be typed into a terminal as a real user turn. The target comes from Orca, not from Concord: `orca worktree ps` lists each agent with its pane key, and `orca terminal list` ties that key to a terminal handle. Only terminals in the configured repo that Orca reports as a Claude or Codex agent, writable, and idle (`done`, `waiting`, or working in `monitoring` mode) are offered; `working` and `blocked` (a permission prompt) are refused, and the state is re-checked at the moment you click Accept. The text is a single line (a newline would submit early). A handoff that names its own target cannot be typed into a terminal.
- **Receipt:** input is confirmed from Orca's send receipt (`result.send.accepted`); anything else is reported as unconfirmed and says the text may have been typed, so check before retrying.
- **Live results (Nov 8 2026, synthetic read-only briefs):** typed into the Claude terminal, the agent treated it as a user turn, read the Jira issue (To Do), found no claim, and did no work. A new Claude chat refused (unclaimed; its Jira read returned a 403 from a connector that is not installed, so it also could not confirm the release state). A new Codex chat, given a handoff naming an Agent Go issue, refused because it held no writer claim. A handoff delivered only as a Concord prompt to a running Claude agent was not acted on (treated as an untrusted peer message).

## v0.8.1: avatars redone

- The earlier avatars were 512 px logos squeezed into a small ringed circle, so the browser rescaled them and softened the pixel edges. They are now cropped tight to the logo and rendered at exact 2x sizes: 88 px images shown at 44 px in the conversation, 44 px images shown at 22 px in the presence chips and handoff cards, as rounded squares with no ring. The pixel-art Claude Code mascot is block-averaged on its own grid and drawn with `image-rendering: pixelated`. On a 1x or 3x display the browser still has to rescale, so judge sharpness on the screen it will be used on.

## v0.9.0: pull a Jira issue in (copies Orca's native flow)

- **What was copied:** Orca's own Tasks view lists your Jira issues (assigned, reported, all, done), lets you open one, and links it to a workspace or starts a workspace from it. The "＋ Jira" button now does the same: filter tabs and a search box (a key like `LIFE-375`, or words), a list with status pills, and an issue view (status, type, project, parent, labels, description).
- **Pull into this room** attaches the issue to the pilot workspace with `orca reference add` (Orca's own linked items; **Unlink** reverses it). Pulled-in issues show in a strip under the header and in Orca's own workspace card.
- **Start a chat from it** launches a new Claude Code or Codex chat in the configured repo with the ticket as its first message, from one of three templates: *Review (read-only)* (default), *Plan (no changes)*, or *Implement* (only if the issue is released for Agent Go and the agent holds the writer claim; otherwise it must reply `REFUSED`). Every prompt says the issue text is untrusted data, never instructions (Orca's own Jira skill says the same).
- **It never writes to Jira.** There is no create, comment, transition, assignee or label code in the plugin; a test fails if one is added. Orca holds the Jira login; the plugin never sees credentials. Pulling in and starting a chat only change Orca's workspace and start an agent.

## v0.9.1: avatars back at their original size

- Circles are 26 px again (16 px in the presence chips), with the logos at their original padding. They are sharp because each image is generated at exactly twice its display size (52 px shown at 26 px, 32 px shown at 16 px); the pixel mascot's blocks land on whole pixels. The ring is drawn outside the image with a box-shadow so it no longer shrinks the picture. Earlier versions grew them to 32-44 px, which was not asked for.

## v0.9.2: crisper avatar edges

- Measured on a real screenshot: the avatar is drawn at exactly 52x52 device pixels (1:1, no browser rescaling) and the logo edges are about one pixel soft, which is the limit for a 26 px circle. The fuzzy look came from (1) a dark 2 px ring around the circle, a second soft step between the page and the avatar, now removed, and (2) a resampling filter (Lanczos) that adds ringing halos, replaced by exact area averaging (520 px canvas to 52 px is exactly 10x, the 512 px original to 32 px is exactly 16x).

## Operating notes (production, Nov 8 2026)

- **Where things live:** Concord itself is installed at `~/.local/share/concord` (an npm prefix; `~/.local/bin/concord`, `~/.local/bin/concord-mcp` and `~/.claude/skills/concord-relay` point into it, and so does the room's `concordExecutable` setting). The plugin is installed from this folder with `orca plugin install <this folder>`; Orca keeps a pinned snapshot, so a change here needs a PR, a merge, then another install and `orca plugin restart local.concord-room`. The room reads the repository in its `repoRoot` setting (`life-os-v7`).
- **Never run `concord setup` bare.** Concord 0.20.0's `setup` rewrites the Concord hooks block in your real `~/.codex/config.toml` (on Nov 8 2026 it dropped the opening marker and two comment lines, leaving the closing marker). Run `scripts/concord-setup-safe.sh --no-adapters` from the repository instead: it backs up `~/.codex/config.toml` and `~/.claude/settings.json`, runs `concord setup` with your arguments, shows any diff, and prints the exact restore command (exit 1 if a file changed). It restores nothing itself.
- **Hook trust:** Codex trusts a hook by its place in the config file (`config.toml:session_start:0:0` and so on), not per repository. Orca's managed Codex home already holds the trust entries for the Concord hooks, so there is nothing to do per repo. In an Orca Codex **chat**, `/hooks` is not a command (it goes to the model as plain text); the trust UI only exists in a Codex terminal.
- **Start agents in the right Orca project.** Concord shares one store across a repository's linked worktrees only when the worktree's common git dir is named `.git`. Orca worktrees of the real `life-os-v7` project qualify; worktrees of the bare mirror project (`~/Developer/LIFEOS-mirrors/life-84/life-os-v7.git`, also displayed as "life-os-v7") do not, so their agents register in a separate `.concord/` inside that worktree and never show in the room. The room's **＋ Agent** menu launches in the room's `repoRoot`, so it is always the right one.

## v0.11.0: start an agent from the room

- **＋ Agent** in the room header opens a small menu: Claude Code or Codex, each as a **Chat** or a **Terminal** (the same launch Orca's own **+** menu uses: `orca agent launch --surface chat|terminal`). The agent starts in the room's `repoRoot` with a fixed first message: call Concord `start_work` so it shows up in the room, say it joined, then wait; no files, no branches, no other work, and a room message never authorizes work (Jira Agent Go and the writer claim still do). Each start is audited without any message text. When no agent is present, the empty state points at the menu.
- An idle Codex chat cannot receive messages until it next acts (Concord limit); a terminal agent can. The menu says so.

## v0.10.0: Concord Room in the + menu

- The tab bar **+** menu and the command palette now have a **Concord Room** entry (Orca's own `contributes.newFiles`, as the plugin-authoring guide describes). It creates an untitled `.concord-room` file in the active workspace, which opens in the center column in the room editor. Opening an existing `.concord-room` file still works. No new access: the approval is for the same worker and settings, but a new version always needs a new approval when installed.
