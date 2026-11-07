# Script-pack runner (LIFE-310)

Jim approves a host change with `run <LIFE-key> <sha8>` and runs ONE fixed command on `lifeos-edge-01`. He never pastes output: the agent reads a result file.
Decision (Jim, Nov 5 2026, 3:21 PM CT): the runner is a single root-owned program run with one `sudo` command. No sudoers allowlist, no standing root rights, no timer, no watcher, no second scheduler.

## What Jim does
1. The agent publishes a pack and prints its full sha256 in the Jira receipt. Jim replies `run LIFE-NNN <first 8 characters>`.
2. Jim copies the pack and runs the one command (the receipt gives the exact lines):
   ```
   scp -r <pack-folder> opc@lifeos-edge-01:/tmp/lifeos-pack-LIFE-NNN
   ssh -t opc@lifeos-edge-01 'sudo /usr/local/sbin/lifeos-run LIFE-NNN <sha8>'
   ```
3. The terminal shows exactly one line: `RESULT ok` or `RESULT fail <code>`. Nothing else is needed from Jim.
4. The agent reads `/var/lib/lifeos-runner/results/LIFE-NNN.json` (names and counts only).

`lifeos-run help` lists every pack staged on the host with its full sha256, so the number Jim approved can be checked against the number on the host.

## Pack format
A folder with `pack.json`, `steps/*.sh` and optionally `files/`. Nothing else is allowed at the top level; symlinks and special files are refused; at most 200 files and 5 MB.
```json
{"schema": 1, "ticket": "LIFE-NNN",
 "steps": ["steps/10-do-it.sh", "steps/20-check.sh"],
 "restore": {"files": ["/etc/example.conf"], "services": ["example.service"]},
 "step_timeout_seconds": 600}
```
- **Pack sha256**: sha256 over the sorted (path, sha256 of bytes) pairs. Permissions and timestamps do not count. `lifeos-run hash DIR` prints it; `lifeos-run check DIR` validates a pack without running it.
- **restore** is the pack's declaration of everything it changes. Files must be absolute, existing regular files or paths that did not exist (they are removed on a restore). Never allowed: `/etc/sudoers*`, `/etc/shadow`, `/etc/ssh/`, `/root/.ssh`, `/etc/credstore*`, `/proc`, `/sys`, `/dev`, `/boot`, the runner itself and its state.
- **Steps** run as root, in order, with `bash`, no stdin, a clean environment (`PATH`, `LANG`, `HOME`, `LIFE_KEY`, `PACK_DIR`), their own process group and a timeout (default 600 s, at most 1,800 s). A step may print `READBACK name=value` lines (names and counts only, redacted, 200 characters each, 100 lines at most); all other output is discarded after counting.

## What the runner checks (all must hold, or one `RESULT fail <code>` line and nothing runs)
| Code | Meaning |
|---|---|
| `E_USAGE` | Not `LIFE-<digits>` plus 8 lowercase hex characters |
| `E_NOTROOT` | Not run with `sudo` |
| `E_BUSY` | Another run is in progress |
| `E_UNKNOWN_TICKET` | No pack staged for that ticket |
| `E_PACK` | Unsafe or malformed pack (links, odd files, bad manifest, forbidden restore path) |
| `E_HASH` | The first 8 hex characters of the pack's sha256 are not the ones typed, i.e. the pack is not the one approved or was edited |
| `E_TICKET` | `pack.json` is for a different ticket |
| `E_STEP_n` / `E_TIMEOUT_n` | Step n failed / timed out; everything declared was restored |
| `E_INTERRUPTED` | Ctrl-C, SIGTERM or SIGHUP; everything declared was restored |
| `E_RESTORE` | The restore itself could not finish; the originals stay in `/var/lib/lifeos-runner/snapshots/` |

The runner copies the pack to a root-only work folder and hashes the COPY, so the bytes that were approved are the bytes that run (a swap after the hash is impossible; a swap before it changes the hash).

## Result file
`/var/lib/lifeos-runner/results/LIFE-NNN.json` (latest) and `LIFE-NNN-<sha8>.json`: ticket, sha8, full sha256, outcome, code, steps run and total, per-step exit code, seconds, output-line count and redaction count, the names restored (`restored`, `restore_failed`), the `READBACK` lines, the finish time in Central Time. `runner.log` has one line per attempt, refusals included (epoch, ticket, sha8, outcome, code). No secret value is written or printed anywhere: secret-shaped text (GitHub, Slack, AWS and `sk-` tokens, bearer headers, `password=`/`token=` values, private keys, any 40+ character token) is replaced before anything is kept.

## Install and uninstall (a protected step: Jim's `run LIFE-310 <sha8>` first)
`lifeos/edge/runner/install-runner.sh` pins the sha256 of `runner.py`, so the one printed hash of the install script covers both. It starts nothing and touches only `/opt/lifeos-runner`, `/usr/local/sbin/lifeos-run` and `/var/lib/lifeos-runner`.
```
scp lifeos/edge/runner/install-runner.sh lifeos/edge/runner/runner.py opc@lifeos-edge-01:/tmp/
ssh -t opc@lifeos-edge-01 'sudo bash /tmp/install-runner.sh'            # dry run: writes nothing
ssh -t opc@lifeos-edge-01 'sudo bash /tmp/install-runner.sh install'    # installs, reads the hash back, prints the rollback
ssh -t opc@lifeos-edge-01 'sudo bash /tmp/install-runner.sh verify'
ssh -t opc@lifeos-edge-01 'sudo bash /tmp/install-runner.sh uninstall'  # the rollback; add --purge to remove the results folder too
```
The uninstall is tested before the install (`tests/edge/runner/test_runner.py`: install, verify, tamper, uninstall, purge under a temp prefix).

## Not proven until installed
All tests run on temp folders with a fake `systemctl`. On the real host, still UNVERIFIED: the launcher at `/usr/local/sbin/lifeos-run` (the commands use the full path so `sudo`'s `secure_path` does not matter), real service restore through `systemctl`, and the SELinux label on the installed files. The first real pack should be a harmless one (it declares one scratch file) before any pack that changes a service.
