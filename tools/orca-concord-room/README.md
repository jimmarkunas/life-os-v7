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
- Reachability while idle is a Concord limit, not a room one. `concord adapters status` (Concord 0.20.0) reports Claude Code as `native-monitor` with `idle` delivery, and Codex as `managed-controller` with only `pull, busy` until Concord's managed Codex app-server is running (Codex has to be launched through `concord adapters codex`). So an idle Codex, chat or terminal, sees a message only when it next acts. (This corrects an earlier line here that said a Codex terminal can be reached while idle.)

## v0.12.0: @mentions and removing agents

- **@mention:** type `@` in the message box and a list of the agents present pops up, with **@everyone** (or `@all`) first. Up/Down moves, Enter or Tab picks, Esc closes. The message is sent to every agent mentioned, one Concord message each with the same text; with no mention it goes to the agent chosen in the **To** box. A line under the box says who it will go to. Names are matched longest first (`@Claude Code 2` is not read as `@Claude Code 1`), case-insensitively; an e-mail address is not a mention. After sending, the note says how many recipients are not live and so only see it when they next act.
- **Default recipient:** the **To** box now lists live agents first, then idle, then away, and defaults to the first, so a message goes to an agent that can hear it (it used to default to whichever agent was first, which could be a long-idle session).
- **Remove an agent from the room:** each agent chip has an **x**. It hides the agent from the room, the **To** box, the **@** list, **@everyone** and handoff targets, and says so. It does not stop the agent, and messages already sent stay in the history. A **N removed · show** link shows the hidden ones dimmed, each with a bring-back button. Stored as a list of agent ids in the new `hiddenAgents` setting (same `settings:own` access; no new capability).
- Helpers for @mentions are in `panel/mentions.js` (pure, no DOM), unit-tested in a Node `vm`; the panel and the center-tab editor both load it before `room.js`.

## Install on a fresh machine, and roll it back

Written after simulating both in a throwaway HOME (LIFE-416 GWT-E1/E2). Concord 0.20.0 is the version this was built and tested against; 0.21.0 exists and has not been checked for the hooks-marker bug below, so pin 0.20.0.

**You need:** Orca 1.4.246 or later with plugins on, Claude Code and Codex installed and logged in, Node 22 or later, git, and a checkout of life-os-v7 (the plugin and the safe-setup script live in `tools/orca-concord-room`). The Jira buttons also need Orca's Jira connection.

1. **Install Concord, pinned, outside the repo:** `V=0.20.0; npm install --prefix ~/.local/share/concord "@concord-ai/concord-mcp@$V"`
2. **Put it on your PATH and link its skill:** `ln -s ~/.local/share/concord/node_modules/.bin/concord ~/.local/bin/concord`, the same for `concord-mcp`, and `ln -s ~/.local/share/concord/node_modules/@concord-ai/concord-mcp/plugin/concord-relay ~/.claude/skills/concord-relay`. `~/.local/bin` must be on PATH. Concord telemetry is on by default; set `CONCORD_TELEMETRY_DISABLED=1` to turn it off.
3. **Enroll a repository, from its root, with the safe wrapper (never bare `concord setup`):** `<life-os-v7>/tools/orca-concord-room/scripts/concord-setup-safe.sh --no-adapters`. It backs up `~/.codex/config.toml` and `~/.claude/settings.json` to `~/.local/state/concord-room/setup-backups/<time>/` and reports any change. Exit 1 means a user-level config was changed or created: read the diff and keep the printed restore or undo command.
4. **Install the room:** `orca plugin install <life-os-v7>/tools/orca-concord-room`, then open **Concord Room** from the + menu and set its repository.
5. **Start agents** with **＋ Agent**. Use **Council member** for Codex: Concord cannot wake an idle Codex chat. Concord has no command-line way to send a message; agents send through its MCP tool (`update_work`) and the room sends for you.

**Roll it back, in this order** (the order matters: the wrapper's backups and your agent configs must be restored before anything is deleted):

1. **Restore the configs first.** For each file the wrapper reported as CHANGED, copy the backup back (for example `cp -p ~/.local/state/concord-room/setup-backups/<time>/config.toml ~/.codex/config.toml`); for each one reported CREATED, remove it. Compare with `shasum -a 256` against the backup. If you installed before v0.16.0 your backups are in `~/.local/share/concord/setup-backups`: copy them somewhere else before step 4, because step 4 deletes them. Skipping this leaves Concord hook commands in `~/.codex/config.toml` pointing at a binary that no longer exists.
2. **Remove the room:** `orca plugin uninstall local.concord-room`, and remove a disposable repository's Orca registration.
3. **Remove the links:** `~/.local/bin/concord`, `~/.local/bin/concord-mcp`, `~/.claude/skills/concord-relay`.
4. **Remove Concord:** `~/.local/share/concord` and `~/.concord`. Keep `~/.concord-pilot` if you want the council state and handoff records (`handoffs/council.json`); delete it only if you do not.
5. **Check:** the four files `~/.claude.json`, `~/.claude/settings.json`, `~/.codex/config.toml` and `~/.codex/AGENTS.md` should match their pre-setup hashes. In the simulation `concord setup` changed only `~/.codex/config.toml`, so that is the one to check hardest.

**Known bug this works around:** Concord 0.20.0's `setup` rewrites the Concord hooks block in `~/.codex/config.toml` and drops the opening marker and two comment lines. Setup does not undo that and neither does rolling Concord back, so restoring from the backup is the only fix.

## v0.16.0: answer a member's question, read-only watch, known limits

- **Answer a council member's question.** A member can ask the coordinator something (Orca's `orchestration ask`). The room shows it with an **Answer** button; your next message goes back through `orchestration reply` and Orca wakes the member with it. A question can be answered once, and not after the member is released.
- **Read-only watch.** Orca has no permission flag for a worker, and changing Orca's default agent arguments is a security setting this plugin does not touch, so members stay read-only only because they are told to. The room now watches instead: it notes the repository's changed files (`git status` plus each file's modified time and size) when a question goes out and compares them when the answer comes back. If files changed, a line from the room says which ones. It cannot tell who changed them, so the line says it may have been you or another agent in the same worktree. It is a warning, not a lock.

## Known limits (tracked, not fixed here)

- **Orca leaves an idle terminal after setup bootstrap (LIFE-419).** Blocked upstream: stablyai/orca #25071 and #25252. Nothing the plugin can do.
- **A chat cannot be messaged from the Orca CLI.** `orca chat` only renames; stablyai/orca #33114 asks for list, read and send. The council uses Orca workers (`orchestration worker-start`, `send`, the Run mailbox) instead, so it needs no private API.
- **Claude terminals can stop at “Bypass Permissions”.** Orca starts some Claude launches (including Agent Teams, `orca claude-teams`) with `--dangerously-skip-permissions`, and Claude asks the person at the keyboard to accept that. Only you can accept it; the room and its agents never do. Launching without that flag works.
- **Council coordinator tab.** The “Concord Room council (leave alone)” terminal must stay open. Closing it ends the Run; members are marked released and a new Run starts with the next member.
- **Council members use each agent's default model** unless a later version lets you pick one.
- **Released member chats stay open in Orca** as ordinary chats; Orca has no CLI to archive a chat.

## v0.15.0: council rounds (the room relays answers between members)

Council members cannot talk to each other: an Orca worker only reports to its coordinator, and Concord cannot wake an idle Codex. So the room is the go-between. When you address **two or more council members** (the To box, or @mentions), a **Round** menu appears next to it: **off**, **+1 reply to each other** or **+2 replies**. With a round on, the question goes to every chosen member as usual; when all of them have answered, the room sends each member the *other* members' answers and asks it to respond (agree, disagree, change its mind), then repeats up to the number you picked. Everything uses the same `orchestration send` and Run mailbox as a normal question.

- **In the room** each relay shows as a short line from you (“Round 2: relayed 1 other answer and asked for a response.”), not the full relayed text, so it is not passed off as something you wrote. The full relay (each other member's answer, cut at 2,500 characters) is what the member receives.
- **Limits:** at most 3 follow-ups; a member never receives its own answer back; a member that has not answered after 10 minutes is dropped from that round, and a round with fewer than two answers ends early with a note; releasing a member drops it from the round; a failed send stops the round with a visible note.
- **Cost:** every member reads every other member's answer each round, so tokens grow with members × rounds.
- **Safety:** `council.json` reads and writes now queue behind each other in the worker, so a send and a sync cannot overwrite each other.

## v0.14.0: council members (Orca's own orchestration as the harness)

The room is now the coordinator, the way a shell was when this was proven by hand. **＋ Agent → Council member** starts a kept-open Claude or Codex worker with `orca orchestration worker-start --keep-open`. Your message to it goes out as `orchestration send`; Orca wakes the worker even when its chat was idle, and its answer comes back as a `worker_done` in the Run's mailbox, which the room reads with `orchestration inbox` and shows as a normal reply. No Concord receiver, no terminal typing, no private Orca RPC. Concord stays for claims, overlap warnings and handoff evidence; a council member also registers in Concord, and the room shows it once, as the member.

- **State** lives in `<handoffDir>/council.json` (coordinator terminal, Run id, members, the question/answer log). It is a cache of what Orca holds; deleting it only forgets the room's view.
- **Coordinator tab.** Orca insists the coordinator is a terminal and types its “you have an orchestration message” line there, so the room opens one silent terminal titled “Concord Room council (leave alone)” in this repository and reuses it. Closing it ends the council's Run: members are marked released and a new one is made on the next start.
- **×** on a council member releases it (`worker-release`): its chat stays open in Orca as an ordinary chat.
- **Permissions.** Members are told to answer read-only and one `worker_done` per question. They run with the same Orca agent permissions as any chat you start, so that is an instruction, not a lock.
- **If a member ends a turn without reporting**, Orca sends an escalation; the room reads the answer from the member's chat (`worker-read`) and marks it as unreported.
- **Not yet:** a member's own questions (`ask`) are shown but cannot be answered from the room.

## v0.13.0: message an agent and it answers (the room wakes it through its terminal)

- **The problem:** Concord delivers to an idle agent only while a live receiver is attached to its session. A **Claude chat** gets one from Concord's own hooks (a Stop hook arms a "rewake" waiter) and answers an idle message within seconds, but only while Orca keeps that chat's session running: in the pilot a working chat's receiver disappeared about 31 minutes after its last turn, and a room-launched chat's about 33 minutes after its last turn, and each turn restarts that clock. A **Codex chat** never gets one. And even when Concord delivers to a listening Claude, it arrives as an untrusted *peer* message that the agent may decline. Buzz solves this with a resident harness (`buzz-acp`) that starts the agent's turn on every @mention; this version covers the cases Concord cannot with Orca's own terminal API.
- **How it works:** after a message is recorded in Concord (so it shows in the room), the worker types it into the agent's own Orca terminal (`orca terminal send`, the same call the handoff delivery uses). Text typed there is a prompt from you, so the agent wakes, answers, and replies to the Concord message so the answer lands in the room. Proven live: an idle terminal Claude answered 12 seconds after a room message.
- **Which terminal is whose:** an agent started from **＋ Agent** is told to run `echo "$ORCA_TERMINAL_HANDLE"` (Orca sets it in every terminal) and to report it in its `start_work` task text, `Available in the Concord Room (terminal term_<uuid>)`. The worker reads that from Concord's `inspect_work` and checks it against Orca: the terminal must be open and be the same kind of agent (a Codex cannot claim a Claude's terminal). Only an **idle** terminal is typed into; a busy agent picks the message up through Concord's own hooks.
- **Reach per agent:** the room shows who can be woken (`terminal`, else a Concord `promptable` receiver) and marks the rest **can’t wake** on the chip, in the **To** box and in the **@** list, warns before sending, and after sending says how each recipient was reached. The **To** box defaults to an agent that can answer. This replaces the 0.12.1 by-kind guess (Concord's `adapters status`), which wrongly treated every Claude as wakeable, including ones whose session had ended.
- **Limits:** a **chat** is only as wakeable as its session is alive (Claude: by Concord's hooks while the session runs; Codex: never when idle), because Orca has no supported way to type into a chat (upstream request: stablyai/orca #33114); a Claude terminal started from the room first stops at Claude's "Bypass Permissions" confirmation, which only you can accept in its tab; an agent that was not started from **＋ Agent** has not reported its terminal, so it is not marked wakeable until it does. Messages typed into a terminal are audited (agent and outcome, never the text).

## v0.12.1: say which agents Concord can wake

- **Why:** an agent shown as "live" is not necessarily listening. "live" only means it ran recently. Concord can wake an idle agent only when its harness adapter reports the `idle` capability: `concord adapters status` says Claude Code has it (`native-monitor`) and Codex does not (`pull, busy` only), so a message to an idle Codex is queued (`queued_pull`) until that Codex next takes a turn. The room used to show "Codex 1 (live)" and no warning, so a message to it looked like it should be answered.
- **What changed:** the worker reads `concord adapters status` (cached for a minute; any failure means "unknown", nothing is marked) and sends the room a `wake` map by agent kind. Agents Concord cannot wake are marked **pull-only** on their chip, in the **To** box and in the **@** list; a line under the box says so before you send (`Codex 1 can't be woken while idle: it reads this the next time it takes a turn`), and after sending the note says it again and how to get an answer now (type in that agent's own chat or terminal). An **away** agent is also called out after sending. Claude Code is not marked.
- It reads only a supported command's output, not Concord's database.

## v0.10.0: Concord Room in the + menu

- The tab bar **+** menu and the command palette now have a **Concord Room** entry (Orca's own `contributes.newFiles`, as the plugin-authoring guide describes). It creates an untitled `.concord-room` file in the active workspace, which opens in the center column in the room editor. Opening an existing `.concord-room` file still works. No new access: the approval is for the same worker and settings, but a new version always needs a new approval when installed.
