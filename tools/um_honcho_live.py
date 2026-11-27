"""LIFE-1003 (REQ-85 T2): the live entry point, behind a fail-closed gate. Importing this file does nothing; running it without every condition below prints one fixed word and exits non-zero BEFORE any object is built, any token is
used or any call is made. The gate is not a release: the release is the ticket (Agent Go on LIFE-1003 and Jim's two items); it only makes sure a stray run cannot spend.
Gate (all must hold): `--live`; UM_T2_RELEASE equals the exact string `RELEASED-LIFE-1003:um-t2-fixture-v1`; the host is lifeos-edge-01; the Honcho URL is loopback; UM_T2_TOKEN is a workspace-scoped token for exactly
`um-t2-fixture-v1` (claim `w`), not admin (`ad`), with no narrower claim, valid for at least five more minutes and no more than two hours; no admin token is present in the environment; and UM_T2_SCOPE names the meter's experiment scope (the guard checks it against the meter's own health document)."""

import argparse
import base64
import importlib.util
import json
import os
import pathlib
import platform
import sys
import time
import urllib.request


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pilot, client, guard = _load("um_honcho_pilot"), _load("um_honcho_client"), _load("um_budget_guard")
RELEASE = "RELEASED-LIFE-1003:" + pilot.WORKSPACE
HOST = "lifeos-edge-01"
HONCHO_URL = "http://127.0.0.1:8000"
METER_URL = "http://172.28.0.1:8081/health"
MAX_LIFE_S, MIN_LIFE_S = 2 * 3600 + 60, 300


def claims(token):
    """The payload of a JWT, read without verifying (the server verifies); None when it is not a JWT."""
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError, AttributeError):
        return None


def gate(live, env, host, now, url=HONCHO_URL):
    """-> (True, "open") or (False, a fixed reason word). Every check fails closed."""
    if not live:
        return False, "not_live"
    if env.get("UM_T2_RELEASE") != RELEASE:
        return False, "not_released"
    if host != HOST:
        return False, "wrong_host"
    if not url.startswith("http://127.0.0.1:") and not url.startswith("http://localhost:"):
        return False, "url_not_loopback"
    if env.get("HONCHO_ADMIN_JWT") or env.get("HONCHO_ADMIN_TOKEN"):
        return False, "admin_token_present"
    if not env.get("UM_T2_SCOPE"):
        return False, "scope_id_missing"
    token = env.get("UM_T2_TOKEN") or ""
    c = claims(token)
    if not token or c is None:
        return False, "token_missing_or_not_jwt"
    if c.get("ad") or c.get("w") != pilot.WORKSPACE or any(c.get(k) for k in ("p", "s", "sc")):
        return False, "token_not_workspace_scoped"
    life = (c.get("exp") or 0) - now
    if not (MIN_LIFE_S <= life <= MAX_LIFE_S):
        return False, "token_lifetime"
    return True, "open"


class MeterHttp:
    def __init__(self, url=METER_URL, opener=urllib.request.urlopen):
        self.url, self.opener = url, opener

    def reading(self):
        """The meter's whole /health document (monthly spend, blocked, scope); the guard validates it."""
        with self.opener(self.url, timeout=3) as answer:
            return json.loads(answer.read())


class RealClock:
    def now(self):
        return time.time()

    def sleep(self, dt):
        time.sleep(dt)


def build(env):
    """The real objects (only called after the gate opens). Aura is read by name from Bitwarden by the host user; nothing is printed."""
    aura = _load("um_aura_pilot")
    secrets = aura.load_secrets()
    driver = aura.connect(secrets)
    sc = aura.Scoped(driver, secrets["uri"].split("://", 1)[1].split(".")[0])
    honcho = client.Honcho(client.Http(HONCHO_URL, env["UM_T2_TOKEN"]), pilot.WORKSPACE)
    return honcho, MeterHttp(), pilot.OracleDb(), aura.AuraGraph(sc), RealClock(), driver


def main(argv=None, env=None, host=None, now=None, builder=build):
    ap = argparse.ArgumentParser(prog="um_honcho_live")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--seed", choices=("messages", "conclusions"), default="messages")
    ap.add_argument("--evidence", default="/var/tmp/um-t2/evidence.json")
    args = ap.parse_args(argv)
    env = os.environ if env is None else env
    ok, reason = gate(args.live, env, platform.node() if host is None else host, time.time() if now is None else now)
    if not ok:
        print(reason)
        return 2
    honcho, meter, db, graph, clock, driver = builder(env)
    try:
        doc = json.loads(pilot.um.CORPUS.read_text())
        ev = pilot.run_pilot(honcho, meter, db, doc, graph, clock, seed=args.seed, scope_id=env.get("UM_T2_SCOPE"))
    finally:
        getattr(driver, "close", lambda: None)()
    pathlib.Path(args.evidence).write_text(json.dumps(ev, indent=1, default=str))
    print(json.dumps({"stopped": ev.get("stopped"), "steps": [s["step"] for s in ev["steps"]]}))
    return 0 if not ev.get("stopped") else 3


if __name__ == "__main__":
    sys.exit(main())
