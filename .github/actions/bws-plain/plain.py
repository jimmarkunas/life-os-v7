"""LIFE-704: read the plain switches from Bitwarden without masking them.

The pinned helper prints ::add-mask:: for every value it loads. Run as its own step that would mask 'true', 'on' or '22' in every later log and drop every job output that contains
it (the LIFE-703 outage). So this runs the same helper as a child process with its stdout discarded and its file commands pointed at private temp files: the runner never sees a
mask line. Only names on ALLOWED are read, every value is validated, and only the validated value is exported to the job environment (GITHUB_ENV). The log carries names and fixed
codes, never a value. Fail modes: V7_LIVE -> dry run, V7_CLOUD_FIT* -> off, ALERTS_* -> alerts ON with defaults, LIFEOS_ACQ_SSH_PORT -> the job fails with a fixed code.
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import time

IDS = {  # Bitwarden item ids, project "LIFE OS GitHub Actions", lifeos/github-actions/<NAME> (Jira LIFE-704 comments 13319 and 13320)
    "V7_LIVE": "27bf9699-1f27-4176-beba-b503015f89b1",
    "ALERTS_ENABLED": "efc8fc25-0228-4aa4-a7ee-b503015f8a8c",
    "ALERTS_MUTED_UNTIL": "141b6f81-341b-4277-b2b3-b503015f8bd0",
    "ALERT_SETTINGS": "dc0b1ad9-45a8-4e1f-8868-b503015f8c97",
    "V7_CLOUD_FIT": "ca47c0f1-5a25-4dbf-9b36-b503015f8f15",
    "V7_CLOUD_FIT_SCHEDULED": "039e50d1-2793-4809-9ef2-b503015f8fc0",
    "V7_CLOUD_FIT_BACKLOG_BUDGET": "b533a10f-69b5-4ccf-8f47-b503015f90a4",
    "V7_CLOUD_FIT_BACKLOG_MAX": "875eafd7-b718-4bcb-bf1c-b503015f916f",
    "V7_CLOUD_FIT_PARALLEL": "d58fac7a-9c4e-4678-8327-b503015f922b",
    "LIFEOS_ACQ_SSH_PORT": "53f91adc-5bb5-4a33-98a8-b5030155ac02",
}
ALLOWED = frozenset(IDS) | {"LIVE"}


def _whole(low, high, default):
    def check(raw):
        return raw if re.fullmatch(r"[0-9]{1,6}", raw) and low <= int(raw) <= high else default
    return check


def _enum(choices, default):
    return lambda raw: raw if raw in choices else default


def _muted(raw):
    if not raw:
        return ""
    try:
        dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return raw


def _settings(raw):
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return json.dumps(data, separators=(",", ":"), ensure_ascii=True) if isinstance(data, dict) else None


# name -> (check, value used when Bitwarden cannot be read or the value is refused). check returns the value to export or None (refused).
RULES = {
    "V7_LIVE": (_enum({"true", "false"}, None), "false"),                       # fail closed: dry run
    "V7_CLOUD_FIT": (_enum({"off", "shadow", "canary"}, None), "off"),          # fail closed: no model spend (on is refused in code as well)
    "V7_CLOUD_FIT_SCHEDULED": (_whole(0, 1000, None), ""),                      # fail closed: unset = no scheduled batch
    "V7_CLOUD_FIT_BACKLOG_BUDGET": (_whole(0, 100000, None), ""),
    "V7_CLOUD_FIT_BACKLOG_MAX": (_whole(0, 10000, None), "0"),                  # fail closed: a ceiling of 0 asks nothing
    "V7_CLOUD_FIT_PARALLEL": (_whole(1, 3, None), "1"),
    "ALERTS_ENABLED": (_enum({"on", "off"}, None), "on"),                       # fail OPEN: the alarm must not depend on the system it alarms about
    "ALERTS_MUTED_UNTIL": (_muted, ""),                                         # fail open: not muted
    "ALERT_SETTINGS": (_settings, ""),                                          # fail open: the registry defaults
    "LIFEOS_ACQ_SSH_PORT": (_whole(1, 65535, None), None),                      # no default: the job fails with a fixed code
}


def parse_commands(text):
    """GITHUB_OUTPUT format: NAME=value lines, or NAME<<DELIMITER ... DELIMITER blocks."""
    out, lines, i = {}, text.split("\n"), 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if "<<" in line and "=" not in line.split("<<", 1)[0]:
            name, delim = line.split("<<", 1)
            body = []
            while i < len(lines) and lines[i] != delim:
                body.append(lines[i])
                i += 1
            i += 1
            out[name] = "\n".join(body)
        elif "=" in line:
            name, value = line.split("=", 1)
            out[name] = value
    return out


def read_bitwarden(wanted, env, helper, sleep=time.sleep):
    """Return {name: raw value} for the names the helper returned, or None when it could not be read. Its output is never shown."""
    if env.get("PLAIN_TEST_UNREADABLE") == "fail" or not wanted or not os.path.exists(helper):
        return None
    for attempt in (1, 2):
        with tempfile.TemporaryDirectory() as tmp:
            files = {key: os.path.join(tmp, key.lower()) for key in ("GITHUB_OUTPUT", "GITHUB_ENV", "GITHUB_PATH", "GITHUB_STATE", "GITHUB_STEP_SUMMARY")}
            for path in files.values():
                open(path, "w").close()
            child = dict(env, **files, INPUT_SET_ENV="false", INPUT_CLOUD_REGION="", INPUT_BASE_URL="", INPUT_IDENTITY_URL="", INPUT_API_URL="",
                         INPUT_SECRETS="\n".join(f"{IDS[n]} > {n}" for n in wanted))
            try:
                os.chmod(helper, 0o755)              # the vendored index.js does the same before it runs the program
            except OSError:
                pass
            try:
                run = subprocess.run([helper], env=child, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, check=False)
            except (OSError, subprocess.SubprocessError):
                run = None
            if run is not None and run.returncode == 0:
                with open(files["GITHUB_OUTPUT"]) as handle:
                    return {k: v for k, v in parse_commands(handle.read()).items() if k in wanted}
        if attempt == 1:
            sleep(5)
    return None


def decide(names, live_mode, bitwarden):
    """Pure decision. Returns (exports, report, failures): exports NAME -> validated value, report NAME -> fixed code, failures = names that must fail the job."""
    exports, report, failures = {}, {}, []
    for name in names:
        if name == "LIVE":
            continue
        check, fallback = RULES[name]
        raw = None if bitwarden is None else bitwarden.get(name)
        value = None if raw is None else check(raw.strip())
        if value is not None:
            exports[name] = value
            report[name] = "ok"
        elif fallback is None:
            report[name] = "UNREADABLE" if raw is None else "REFUSED"
            failures.append(name)
        else:
            exports[name] = fallback
            report[name] = ("BWS_UNREADABLE" if bitwarden is None else "NOT_RETURNED") if raw is None else "REFUSED_USED_DEFAULT"
    if "LIVE" in names:
        if live_mode in ("true", "false"):
            exports["LIVE"], report["LIVE"] = live_mode, "input"
        else:
            exports["LIVE"] = "true" if exports.get("V7_LIVE") == "true" and report.get("V7_LIVE") == "ok" else "false"
            report["LIVE"] = "follow" if report.get("V7_LIVE") == "ok" else "dry_run_default"
    return exports, report, failures


def requested(names_text, live_mode):
    names = [n.strip() for n in names_text.split(",") if n.strip()]
    unknown = [n for n in names if n not in ALLOWED]
    if unknown:
        raise ValueError("PLAIN_NAME_NOT_ALLOWED")
    names = list(dict.fromkeys(names))
    if "LIVE" in names and live_mode not in ("true", "false") and "V7_LIVE" not in names:
        names.append("V7_LIVE")
    return names


def main(env=os.environ):
    try:
        names = requested(env.get("PLAIN_NAMES", ""), env.get("PLAIN_LIVE_MODE", "false"))
    except ValueError as error:
        print(f"::error::{error}")
        return 1
    wanted = [n for n in names if n != "LIVE"]
    exports, report, failures = decide(names, env.get("PLAIN_LIVE_MODE", "false"), read_bitwarden(wanted, env, env.get("PLAIN_HELPER", "")))
    with open(env["GITHUB_ENV"], "a") as handle:
        for name, value in exports.items():
            if "\n" in value or "\r" in value:
                continue
            handle.write(f"{name}={value}\n")
    print("bws-plain: " + " ".join(f"{n}={c}" for n, c in report.items()))
    if failures:
        print(f"::error::BWS_PLAIN_{'_'.join(sorted(failures))}_{report[failures[0]]}: the value could not be read or was refused; no other source is used")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
