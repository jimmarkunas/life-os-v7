"""LIFE-310: the script-pack runner. One root-owned program, one `sudo` command, run on lifeos-edge-01.

    sudo /usr/local/sbin/lifeos-run LIFE-310 1a2b3c4d      Jim's whole approval step: `run LIFE-310 1a2b3c4d` in chat, then this one command.
    lifeos-run hash DIR                    the pack's full sha256 (the agent prints it in the ticket receipt)
    lifeos-run check DIR                   validate a pack without running it
    lifeos-run help                        usage, and the sha256 of every pack staged on this host

A pack is a folder: pack.json (ticket, steps, what to restore), steps/*.sh and optional files/. Jim copies it to /tmp/lifeos-pack-<KEY>/. The runner COPIES it to a root-only work
folder first and hashes the COPY, so the bytes that were approved are the bytes that run. It executes only when the first 8 hex characters of that sha256 equal the ones Jim typed
and pack.json is for the same ticket. Anything else is refused with one line and nothing runs.

The terminal shows exactly one line: `RESULT ok` or `RESULT fail <code>`. Step output never reaches the terminal. It is redacted for secret-shaped text and reduced to names and counts in
/var/lib/lifeos-runner/results/<KEY>.json (plus a line in runner.log), which the agent reads instead of pasted output. A step may print `READBACK name=value` lines (names and counts only).

Before the first step the runner saves every file and service state the pack declares under "restore". If a step fails, times out, or the run is interrupted (Ctrl-C, SIGTERM, SIGHUP), it puts
them back and says what it restored. It starts no timer, no watcher and no second scheduler, keeps no sudoers rule and holds no credential. Python standard library only (run with python3 -I).
"""
import fcntl
import glob
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time

VERSION = 1
STATE = "/var/lib/lifeos-runner"
INBOX = "/tmp/lifeos-pack-{key}"
KEY_RX = re.compile(r"^LIFE-[0-9]{1,6}$")
SHA8_RX = re.compile(r"^[0-9a-f]{8}$")
NAME_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
UNIT_RX = re.compile(r"^[A-Za-z0-9@._-]+\.(service|timer|socket|path|mount)$")
MAX_FILES, MAX_BYTES, MAX_SNAPSHOT_FILE, MAX_SNAPSHOT_FILES = 200, 5 * 1024 * 1024, 1024 * 1024, 50
STEP_TIMEOUT, STEP_TIMEOUT_MAX = 600, 1800
DENY = ("/etc/shadow", "/etc/gshadow", "/etc/sudoers", "/etc/ssh/", "/root/.ssh", "/etc/credstore", "/proc", "/sys", "/dev", "/boot", "/opt/lifeos-runner", "/usr/local/sbin/lifeos-run", STATE)
STEP_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


class Refuse(Exception):
    """A run that must stop with one fixed code (and, when steps had started, after the restore)."""
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class Interrupted(Exception):
    pass


# ---- redaction: secret-shaped text never leaves a step's output --------------------------------------------------------------------------------------------------------------------

def _long(match):
    text = match.group(0)
    return text if text.startswith("/") and text.count("/") >= 2 else "[REDACTED:long]"


_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S), "[REDACTED:key]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{10,}"), "[REDACTED:token]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"), "[REDACTED:token]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"), "[REDACTED:token]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{8,}"), "[REDACTED:token]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED:token]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)\b([A-Za-z0-9_.-]*(?:password|passwd|secret|token|api[_-]?key|credential)[A-Za-z0-9_.-]*)(\s*[=:]\s*)\S+"), r"\1\2[REDACTED]"),
    (re.compile(r"[A-Za-z0-9+/_=-]{40,}"), _long),
]


def redact(text):
    """-> (text with secret-shaped parts replaced, how many replacements)."""
    total = 0
    for rx, repl in _PATTERNS:
        hits = [0]

        def swap(match, repl=repl, hits=hits):
            new = repl(match) if callable(repl) else match.expand(repl)
            hits[0] += new != match.group(0)
            return new
        text = rx.sub(swap, text)
        total += hits[0]
    return text, total


# ---- the pack: files, hash, manifest ----------------------------------------------------------------------------------------------------------------------------------------------

def pack_files(root):
    """Sorted relative paths of every regular file in the pack. Symlinks, devices, odd names, extra top-level entries and oversize packs are refused (E_PACK)."""
    found, total = [], 0
    try:
        top = os.lstat(root)
    except OSError:
        raise Refuse("E_UNKNOWN_TICKET") from None
    if not stat.S_ISDIR(top.st_mode):
        raise Refuse("E_PACK")

    def walk(directory, rel):
        nonlocal total
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda e: e.name):
                path = rel + entry.name
                if not NAME_RX.match(path) or ".." in path.split("/"):
                    raise Refuse("E_PACK")
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    if not rel and entry.name not in ("steps", "files"):
                        raise Refuse("E_PACK")
                    walk(entry.path, path + "/")
                elif stat.S_ISREG(info.st_mode):
                    if not rel and entry.name != "pack.json":
                        raise Refuse("E_PACK")
                    total += info.st_size
                    found.append(path)
                    if len(found) > MAX_FILES or total > MAX_BYTES:
                        raise Refuse("E_PACK")
                else:
                    raise Refuse("E_PACK")
    walk(root, "")
    return found


def pack_hash(root):
    """sha256 over the sorted (path, sha256 of content) pairs: names and bytes, not permissions or timestamps. This is the one number the receipt prints."""
    digest = hashlib.sha256()
    for rel in pack_files(root):
        with open(os.path.join(root, rel), "rb") as handle:
            body = hashlib.sha256(handle.read()).hexdigest()
        digest.update(rel.encode() + b"\0" + body.encode() + b"\0")
    return digest.hexdigest()


def stage_copy(src, dest):
    """Copy the pack to a root-only folder without following links; the hash is taken from THIS copy."""
    names = pack_files(src)
    os.makedirs(dest, mode=0o700)
    for rel in names:
        target = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
        fd = os.open(os.path.join(src, rel), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise Refuse("E_PACK")
            body = handle.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise Refuse("E_PACK")
        with open(target, "wb") as out:
            out.write(body)
        os.chmod(target, 0o600)


def load_manifest(root):
    try:
        with open(os.path.join(root, "pack.json"), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        raise Refuse("E_PACK") from None
    if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("ticket"), str) or not KEY_RX.match(data["ticket"]):
        raise Refuse("E_PACK")
    steps, restore = data.get("steps"), data.get("restore") or {}
    if not isinstance(steps, list) or not 1 <= len(steps) <= 50 or not isinstance(restore, dict):
        raise Refuse("E_PACK")
    names = set(pack_files(root))
    for step in steps:
        if not isinstance(step, str) or not step.startswith("steps/") or not step.endswith(".sh") or step not in names:
            raise Refuse("E_PACK")
    files, units = restore.get("files") or [], restore.get("services") or []
    if not isinstance(files, list) or not isinstance(units, list) or len(files) > MAX_SNAPSHOT_FILES or len(units) > 50 or len(set(files)) != len(files):
        raise Refuse("E_PACK")
    for path in files:
        if not isinstance(path, str) or not path.startswith("/") or ".." in path.split("/") or "//" in path or path.endswith("/") or any(path.startswith(d) for d in DENY):
            raise Refuse("E_PACK")
    for unit in units:
        if not isinstance(unit, str) or not UNIT_RX.match(unit):
            raise Refuse("E_PACK")
    timeout = data.get("step_timeout_seconds", STEP_TIMEOUT)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= STEP_TIMEOUT_MAX:
        raise Refuse("E_PACK")
    return {"ticket": data["ticket"], "steps": steps, "files": files, "services": units, "timeout": timeout}


# ---- the host: real paths and systemctl, swappable in tests ---------------------------------------------------------------------------------------------------------------------

class Host:
    def __init__(self, state=STATE, inbox=INBOX, require_root=True, systemctl=None, clock=time.time):
        self.state, self.inbox, self.require_root, self.clock = state, inbox, require_root, clock
        self._systemctl = systemctl

    def systemctl(self, *args):
        """-> (exit code, first word of stdout)."""
        if self._systemctl:
            return self._systemctl(*args)
        try:
            done = subprocess.run(["/usr/bin/systemctl", *args], capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            return 127, ""
        return done.returncode, (done.stdout.split() or [""])[0]


def _write_atomic(path, data, mode):
    tmp = "%s.tmp%d" % (path, os.getpid())
    with open(tmp, "wb") as handle:
        handle.write(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


# ---- snapshot and restore (GWT-RUN-03) --------------------------------------------------------------------------------------------------------------------------------------------

def snapshot(manifest, host, snapdir):
    """Everything the pack declared, as it is now. Also written root-only under snapdir so a hard kill (SIGKILL, power) still leaves the originals."""
    os.makedirs(snapdir, mode=0o700)
    files, meta = {}, []
    for i, path in enumerate(manifest["files"]):
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            files[path] = None
            meta.append({"path": path, "existed": False})
            continue
        except OSError:
            raise Refuse("E_PACK") from None
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SNAPSHOT_FILE:
            raise Refuse("E_PACK")
        with open(path, "rb") as handle:
            body = handle.read()
        files[path] = (body, stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid)
        _write_atomic(os.path.join(snapdir, "%d.bin" % i), body, 0o600)
        meta.append({"path": path, "existed": True, "mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid, "copy": "%d.bin" % i})
    units = {}
    for unit in manifest["services"]:
        units[unit] = {"enabled": host.systemctl("is-enabled", unit)[1], "active": host.systemctl("is-active", unit)[1]}
    _write_atomic(os.path.join(snapdir, "snapshot.json"), json.dumps({"files": meta, "services": units}, indent=1).encode(), 0o600)
    return {"files": files, "services": units}


def restore(snap, host):
    """-> (names put back, names that could not be). Idempotent: a file or service already as it was is left alone."""
    done, failed, systemd_touched = [], [], False
    for path, saved in snap["files"].items():
        try:
            if saved is None:
                if os.path.lexists(path):
                    os.remove(path)
                    done.append(path)
                    systemd_touched = systemd_touched or path.startswith("/etc/systemd/")
                continue
            body, mode, uid, gid = saved
            try:
                with open(path, "rb") as current:
                    same = current.read() == body and stat.S_IMODE(os.stat(path).st_mode) == mode
            except OSError:
                same = False
            if same:
                continue
            tmp = path + ".lifeos-restore"
            with open(tmp, "wb") as handle:
                handle.write(body)
            os.chmod(tmp, mode)
            try:
                os.chown(tmp, uid, gid)
            except PermissionError:
                pass
            os.replace(tmp, path)
            done.append(path)
            systemd_touched = systemd_touched or path.startswith("/etc/systemd/")
        except OSError:
            failed.append(path)
    if systemd_touched:
        host.systemctl("daemon-reload")
    for unit, was in snap["services"].items():
        try:
            enabled, active = host.systemctl("is-enabled", unit)[1], host.systemctl("is-active", unit)[1]
            actions = []
            if was["enabled"] == "enabled" and enabled != "enabled":
                actions.append("enable")
            elif was["enabled"] == "disabled" and enabled == "enabled":
                actions.append("disable")
            if was["active"] == "active" and active != "active":
                actions.append("start")
            elif was["active"] != "active" and active == "active":
                actions.append("stop")
            ok = all(host.systemctl(action, unit)[0] == 0 for action in actions)
            if actions and ok:
                done.append(unit)
            elif not ok:
                failed.append(unit)
        except Exception:
            failed.append(unit)
    return done, sorted(set(failed))


# ---- running the steps ------------------------------------------------------------------------------------------------------------------------------------------------------------

def _kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass


def run_step(stage, rel, key, timeout):
    """-> (exit code or None on timeout, redacted output, replacements, seconds). The raw output exists only in memory."""
    env = {"PATH": STEP_PATH, "LANG": "C", "LC_ALL": "C", "HOME": "/root", "LIFE_KEY": key, "PACK_DIR": stage}
    began = time.monotonic()
    proc = subprocess.Popen(["/bin/bash", "--noprofile", "--norc", os.path.join(stage, rel)], cwd=stage, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        raw, _ = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        raw, _ = proc.communicate()
        code = None
    except BaseException:
        _kill_group(proc)
        proc.wait()
        raise
    text, count = redact(raw.decode("utf-8", "replace"))
    return code, text, count, round(time.monotonic() - began, 2)


def readback_lines(text):
    lines = []
    for line in text.splitlines():
        if line.startswith("READBACK "):
            clean = "".join(c for c in line[9:] if " " <= c <= "~")[:200]
            lines.append(clean)
    return lines[:100]


def _ct(epoch):
    try:
        from zoneinfo import ZoneInfo
        from datetime import datetime
        return datetime.fromtimestamp(epoch, ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %I:%M:%S %p CT")
    except Exception:
        return None


def record(host, result, given):
    """The result file(s) the agent reads (names and counts only) and one line in runner.log. A failure to write never changes the RESULT line."""
    try:
        out = os.path.join(host.state, "results")
        os.makedirs(out, mode=0o755, exist_ok=True)
        data = json.dumps(result, indent=1, sort_keys=True).encode()
        if KEY_RX.match(result["ticket"] or ""):
            _write_atomic(os.path.join(out, result["ticket"] + ".json"), data, 0o644)
            if result.get("sha8"):
                _write_atomic(os.path.join(out, "%s-%s.json" % (result["ticket"], result["sha8"])), data, 0o644)
        line = "%d %s %s %s %s\n" % (result["finished_epoch"], result["ticket"] or "invalid", given if SHA8_RX.match(given or "") else "invalid", result["outcome"], result["code"] or "-")
        with open(os.path.join(host.state, "runner.log"), "a") as log:
            log.write(line)
        os.chmod(os.path.join(host.state, "runner.log"), 0o644)
    except OSError:
        pass


def execute(key, sha8, host):
    """One approved run. -> the result dict (outcome ok|fail, code). Never raises: every stop is a code."""
    started = host.clock()
    result = {"schema": 1, "runner_version": VERSION, "ticket": key if KEY_RX.match(key or "") else None, "sha8": sha8 if SHA8_RX.match(sha8 or "") else None, "sha256": None,
              "outcome": "fail", "code": None, "steps_total": 0, "steps_run": 0, "step_results": [], "restored": [], "restore_failed": [], "readback": [],
              "redactions_total": 0, "started_epoch": int(started)}
    lock = stage = snapdir = None
    previous = {}
    snap = None
    try:
        if not result["ticket"] or not result["sha8"]:
            raise Refuse("E_USAGE")
        if host.require_root and os.geteuid() != 0:
            raise Refuse("E_NOTROOT")
        try:
            os.makedirs(host.state, mode=0o755, exist_ok=True)
            lock = open(os.path.join(host.state, "lock"), "w")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Refuse("E_BUSY") from None
        work = os.path.join(host.state, "work")
        os.makedirs(work, mode=0o700, exist_ok=True)
        stamp = "%s-%d" % (key, int(started))
        stage, snapdir = os.path.join(work, stamp), os.path.join(host.state, "snapshots", stamp)
        os.makedirs(os.path.dirname(snapdir), mode=0o700, exist_ok=True)
        stage_copy(host.inbox.format(key=key), stage)
        digest = pack_hash(stage)
        result["sha256"] = digest
        if digest[:8] != sha8:
            raise Refuse("E_HASH")
        manifest = load_manifest(stage)
        if manifest["ticket"] != key:
            raise Refuse("E_TICKET")
        result["steps_total"] = len(manifest["steps"])
        snap = snapshot(manifest, host, snapdir)

        def stop(signum, frame):
            raise Interrupted()
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            previous[sig] = signal.signal(sig, stop)
        for number, rel in enumerate(manifest["steps"], 1):
            code, text, count, seconds = run_step(stage, rel, key, manifest["timeout"])
            result["steps_run"] = number
            result["redactions_total"] += count
            result["readback"] = (result["readback"] + readback_lines(text))[:100]
            result["step_results"].append({"step": rel, "exit": code, "timed_out": code is None, "seconds": seconds, "output_lines": len(text.splitlines()), "redactions": count})
            if code is None:
                raise Refuse("E_TIMEOUT_%d" % number)
            if code != 0:
                raise Refuse("E_STEP_%d" % number)
        result["outcome"], result["code"] = "ok", None
    except Interrupted:
        result["code"] = "E_INTERRUPTED"
    except Refuse as stopped:
        result["code"] = stopped.code
    except Exception:                                   # a bug in the runner is a failed run, never a crash with a traceback on the terminal
        result["code"] = "E_RUNNER"
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            if sig in previous:
                signal.signal(sig, signal.SIG_IGN)      # the restore must finish
        if snap is not None and result["outcome"] != "ok":
            try:
                result["restored"], result["restore_failed"] = restore(snap, host)
            except Exception:
                result["restore_failed"] = ["restore"]
            if result["restore_failed"]:
                result["code"] = "E_RESTORE"
        for sig, old in previous.items():
            signal.signal(sig, old)
        if stage and os.path.isdir(stage):
            shutil.rmtree(stage, ignore_errors=True)
        if snapdir and os.path.isdir(snapdir) and not result["restore_failed"]:
            shutil.rmtree(snapdir, ignore_errors=True)  # kept only when something could not be put back
        if lock:
            lock.close()
        finished = host.clock()
        result["finished_epoch"], result["finished_ct"] = int(finished), _ct(finished)
        result["restored_count"], result["redacted_note"] = len(result["restored"]), "output is redacted; only names and counts are recorded"
        record(host, result, sha8)
    return result


# ---- the command line -------------------------------------------------------------------------------------------------------------------------------------------------------------

HELP = """lifeos-run: the LIFE-310 script-pack runner.
  sudo /usr/local/sbin/lifeos-run <LIFE-key> <sha8>    run the pack staged in /tmp/lifeos-pack-<LIFE-key>/ if its sha256 starts with <sha8> and it is for that ticket
  lifeos-run hash DIR                  print the pack's full sha256
  lifeos-run check DIR                 validate a pack without running it
The terminal shows one line, `RESULT ok` or `RESULT fail <code>`; the agent reads /var/lib/lifeos-runner/results/<LIFE-key>.json.
Packs staged on this host (full sha256; the first 8 characters are what you approve):"""


def main(argv, host=None):
    host = host or Host()
    args = list(argv)
    if not args or args[0] in ("help", "-h", "--help"):
        print(HELP)
        staged = sorted(glob.glob(host.inbox.format(key="LIFE-*")))
        for path in staged:
            key = os.path.basename(path).split("lifeos-pack-", 1)[-1]
            try:
                print("  %s  sha256 %s" % (key, pack_hash(path)))
            except Refuse as stopped:
                print("  %s  unreadable (%s)" % (key, stopped.code))
        if not staged:
            print("  (none staged)")
        return 0
    if args[0] in ("hash", "check") and len(args) == 2:
        try:
            digest = pack_hash(args[1])
            if args[0] == "hash":
                print(digest)
            else:
                manifest = load_manifest(args[1])
                print("PACK ok %s steps=%d restore_files=%d restore_services=%d sha256=%s" % (manifest["ticket"], len(manifest["steps"]), len(manifest["files"]), len(manifest["services"]), digest))
            return 0
        except Refuse as stopped:
            print("PACK fail %s" % stopped.code)
            return 1
    if len(args) != 2:
        print("RESULT fail E_USAGE")
        return 2
    result = execute(args[0], args[1], host)
    print("RESULT ok" if result["outcome"] == "ok" else "RESULT fail %s" % result["code"])
    return 0 if result["outcome"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
