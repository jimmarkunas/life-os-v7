"""LIFE-1003 (REQ-85 T2): the Honcho v3 client for the isolated experiment. Standard library only. The workspace-scoped token is held in memory by the caller and is never logged, printed or stored; every method is one HTTP
call to the loopback API, and a 401 or 403 is raised as Unauthorized so the guard stops. Tenant answers come from the observed peer, so one tenant's question can only be answered from that tenant's conclusions."""
import importlib.util
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


guard = _load("um_budget_guard")
FIXTURE_ID = re.compile(r"FAKE-[A-Z]+-\d{3}")
TENANT_PEER = {"alpha": "t-alpha", "beta": "t-beta"}
AGENTS = ("agent-a", "agent-b")


class Http:
    def __init__(self, base, token, opener=None, timeout=30):
        self.base, self._token, self.opener, self.timeout = base.rstrip("/"), token, opener or urllib.request.urlopen, timeout

    def __repr__(self):
        return "Http(<token hidden>)"

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={"Authorization": "Bearer " + self._token, "Content-Type": "application/json"})
        try:
            with self.opener(req, timeout=self.timeout) as answer:
                raw = answer.read()
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise guard.Unauthorized(str(error.code)) from None
            raise guard.HonchoError("http_%d" % error.code) from None
        except (urllib.error.URLError, OSError, TimeoutError) as error:
            raise guard.HonchoError(type(error).__name__) from None
        return json.loads(raw) if raw else {}


class Honcho:
    """The surface the guard and the pilot need. `ws` is the one workspace; `session` the one session."""

    def __init__(self, http, ws, session="s1"):
        self.http, self.ws, self.session = http, ws, session
        self.dream_scheduled_at_runs = 0

    def _p(self, tail=""):
        return f"/v3/workspaces/{self.ws}{tail}"

    # ---- the free setup calls
    def create_workspace(self):
        return self.http.call("POST", "/v3/workspaces", {"id": self.ws, "configuration": {"reasoning": {"enabled": False}, "dream": {"enabled": False}, "summary": {"enabled": False}}})

    def create_peers_and_session(self):
        for peer in (*TENANT_PEER.values(), *AGENTS):
            self.http.call("POST", self._p("/peers"), {"id": peer})
        return self.http.call("POST", self._p("/sessions"), {"id": self.session, "peers": {p: {} for p in (*TENANT_PEER.values(), *AGENTS)}})

    def set_flags(self, reasoning, dream):
        self.http.call("PUT", self._p(), {"configuration": {"reasoning": {"enabled": bool(reasoning)}, "dream": {"enabled": bool(dream)}, "summary": {"enabled": False}}})

    def get_flags(self):
        conf = (self.http.call("POST", "/v3/workspaces", {"id": self.ws}) or {}).get("configuration") or {}
        return bool((conf.get("reasoning") or {}).get("enabled")), bool((conf.get("dream") or {}).get("enabled"))

    # ---- messages, queue, conclusions
    def post_messages(self, batch, armed):
        rows = []
        for m in batch:
            row = {"peer_id": m["peer_id"], "content": m["content"], "metadata": {"source_ref": m["source_ref"]}}
            if armed:
                row["configuration"] = {"reasoning": {"enabled": True}}                       # spending is switched on for these messages only; the workspace stays off
            rows.append(row)
        self.http.call("POST", self._p(f"/sessions/{self.session}/messages"), {"messages": rows})

    def queue(self):
        return self.http.call("GET", self._p("/queue/status"))

    def queue_idle(self):
        q = self.queue()
        return int(q.get("pending_work_units") or 0) == 0 and int(q.get("in_progress_work_units") or 0) == 0

    def completed_units(self):
        return int(self.queue().get("completed_work_units") or 0)

    def explicit_count(self):
        return int(self.http.call("POST", self._p("/conclusions/list?page=1&size=1"), {}).get("total") or 0)

    def conclusions(self, tenant, query, top_k):
        peer = TENANT_PEER[tenant]
        out = self.http.call("POST", self._p("/conclusions/query"), {"query": query, "top_k": top_k, "filters": {"observed_id": peer}})
        return out if isinstance(out, list) else out.get("items", [])

    def message_ref(self, message_id):
        msg = self.http.call("GET", self._p(f"/sessions/{self.session}/messages/{message_id}"))
        return ((msg or {}).get("metadata") or {}).get("source_ref")

    def recall(self, query, tenant, top_k):
        """-> [{"source_ref", "text"}]: each conclusion mapped to its fixture record, first through the message it came from, then through an id quoted in its own text; unmapped ones keep source_ref None (the runner drops and counts them)."""
        items = []
        for c in self.conclusions(tenant, query, top_k):
            ref = None
            for mid in c.get("source_message_ids") or []:
                ref = self.message_ref(mid)
                if ref:
                    break
            if not ref:
                found = FIXTURE_ID.search(c.get("content") or "")
                ref = found.group(0) if found else None
            items.append({"source_ref": ref, "text": c.get("content") or ""})
        return items

    # ---- the dream
    def schedule_dream(self):
        self.dream_scheduled_at_runs = len(self.dreams())
        self.http.call("POST", self._p("/schedule_dream"), {"observer": TENANT_PEER["alpha"], "observed": TENANT_PEER["alpha"], "dream_type": "omni", "session_id": self.session})

    def dreams(self):
        out = self.http.call("POST", self._p("/dreams/list"), {})
        return out if isinstance(out, list) else out.get("items", [])

    def dream_finished(self):
        runs = self.dreams()
        return self.queue_idle() and len(runs) > self.dream_scheduled_at_runs and runs[0].get("status") in ("success", "failed", "completed")

    def delete_conclusion(self, conclusion_id):
        self.http.call("DELETE", self._p(f"/conclusions/{conclusion_id}"))

    def conclusion_ids(self):
        out = self.http.call("POST", self._p("/conclusions/list?page=1&size=100"), {})
        return [c["id"] for c in out.get("items", [])]

    def probe_other_workspace(self, name="personal"):
        """-> True when this token is refused on another workspace (a free call)."""
        try:
            self.http.call("GET", f"/v3/workspaces/{name}/queue/status")
        except guard.Unauthorized:
            return True
        return False

    def dream_detail(self, run_id):
        return self.http.call("GET", self._p(f"/dreams/{run_id}"))
