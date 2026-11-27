"""LIFE-1003 (REQ-85 T2): the isolated Honcho pilot, written so it can be tested end to end with fakes and run live only on purpose.

Order (policy: free proofs before any paid call): fresh Personal baseline and due-dream list; create the workspace (all spending switches off) with its peers and session; prove the token is refused on `personal`; brake test (an UNARMED message
must create no work); then the guard seeds in armed tranches and arms the one manual dream; then isolation, residue and the four-mode comparison (T1's runner, Honcho client, Aura graph arm). Live use needs `--live`, the host user's Bitwarden
access for Aura, and the scoped token in UM_T2_TOKEN (never printed). Without `--live` it only prints the plan and the budget numbers."""

import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


budget, guard, client, um = (
    _load("um_budget"),
    _load("um_budget_guard"),
    _load("um_honcho_client"),
    _load("um_baseline"),
)
WORKSPACE = "um-t2-fixture-v1"
ALPHA_MESSAGES, BETA_MESSAGES = (
    21,
    4,
)  # all 25 corpus sources; the guard stops seeding early if two conclusions each would pass the explicit limit


def corpus_messages(doc):
    """-> the fabricated messages: one per source (sorted by id), each by its tenant's peer, the fixture id in its text and in its metadata."""
    out = []
    for tenant, limit in (("alpha", ALPHA_MESSAGES), ("beta", BETA_MESSAGES)):
        for s in sorted((x for x in doc["sources"] if x["tenant"] == tenant), key=lambda x: x["id"])[:limit]:
            out.append(
                {
                    "peer_id": client.TENANT_PEER[tenant],
                    "tenant": tenant,
                    "content": f'{s["id"]}: {s["title"]}. {s["text"]}'[:300],
                    "source_ref": s["id"],
                }
            )
    return out


class OracleDb:
    """Read-only SQL in the Honcho database container (BEGIN READ ONLY every time); aggregates only. Live use."""

    def __init__(self, run=subprocess.run):
        self.run = run

    def _sql(self, text):
        script = "BEGIN READ ONLY;\n" + text + "\nROLLBACK;\n"
        done = self.run(
            [
                "sudo",
                "docker",
                "exec",
                "-i",
                "honcho-database-1",
                "sh",
                "-c",
                'psql -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-postgres}" -At -F"|"',
            ],
            input=script,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if done.returncode:
            raise RuntimeError("sql_failed")
        return [line.split("|") for line in done.stdout.splitlines() if line and line not in ("BEGIN", "ROLLBACK")]

    def personal_state(self):
        row = self._sql(
            "select count(*), coalesce(max(created_at)::text,''), md5(coalesce(string_agg(id||md5(content), ',' order by id), '')) from documents where workspace_name='personal';"
        )[0]
        return {"count": int(row[0]), "newest": row[1], "hash": row[2]}

    DUE = (
        "import asyncio,json\nfrom src.dependencies import tracked_db\nfrom src.dreamer.dream_due import list_due_dreams\n"
        "async def main():\n    async with tracked_db('um_t2_due') as db:\n        print(json.dumps(sorted({r.workspace_name for r in await list_due_dreams(db)})))\nasyncio.run(main())\n"
    )

    def due_workspaces(self):
        """The installed due-dream selector, read-only, run in the API container: -> the workspaces with a dream due."""
        done = self.run(
            [
                "sudo",
                "docker",
                "exec",
                "-i",
                "-e",
                "PYTHONPATH=/app",
                "honcho-api-1",
                "/app/.venv/bin/python",
                "-c",
                self.DUE,
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        if done.returncode:
            raise RuntimeError("due_selector_failed")
        return json.loads(done.stdout.strip().splitlines()[-1])

    def residue(self, workspace, conclusion_id):
        rows = self._sql(
            f"select (select count(*) from documents where id='{conclusion_id}'), (select count(*) from dream_run_changes where conclusion_id='{conclusion_id}'), (select count(*) from dream_run_changes where workspace_name='{workspace}' and peer_card_after is not null);"
        )[0]
        return {"documents": int(rows[0]), "dream_changes": int(rows[1]), "peer_card_changes": int(rows[2])}


def run_pilot(honcho, meter, db, doc, graph, clock, reps=5, seed="messages", scope_id=None):
    """-> the evidence (aggregates, counts and fixture ids only). Stops at the first free proof that fails, before any paid call."""
    ev, calls = {"steps": []}, []

    def step(name, **kw):
        calls.append(name)
        ev["steps"].append({"step": name, **kw})

    before = db.personal_state()
    step("personal_baseline", **before)
    due = db.due_workspaces()
    step("due_before", due=due)
    if due not in ([], None):
        ev["stopped"] = "due_list_not_empty"
        return ev
    honcho.create_workspace()
    honcho.create_peers_and_session()
    honcho.set_flags(False, False)
    flags = tuple(honcho.get_flags())
    step("created_all_off", flags=flags)
    if flags != (False, False):
        ev["stopped"] = "flags_unverified"
        return ev
    refused = honcho.probe_other_workspace("personal")
    step("token_refused_on_personal", refused=refused)
    if not refused:
        ev["stopped"] = "token_not_scoped"
        return ev
    units = honcho.completed_units()
    honcho.post_messages(
        [
            {
                "peer_id": client.TENANT_PEER["alpha"],
                "content": "brake test: an unarmed message",
                "source_ref": "FAKE-BRAKE-000",
            }
        ],
        armed=False,
    )
    clock.sleep(3)
    idle, same = honcho.queue_idle(), honcho.completed_units() == units
    step("brake_test_unarmed_message_creates_no_work", queue_idle=idle, units_unchanged=same)
    if not (idle and same):
        ev["stopped"] = "unarmed_message_created_work"
        return ev
    oversize = honcho.probe_oversize_message()
    step("oversize_message_refused_by_the_api", refused=oversize)
    if not oversize:
        ev["stopped"] = "oversize_message_accepted"
        return ev
    messages = corpus_messages(doc)
    if seed == "conclusions":
        honcho.create_conclusions(messages)  # free seeding: no deriver call at all
        step("conclusions_created_directly", n=len(messages))
    g = guard.Guard(meter, honcho, clock, scope_id=scope_id)
    report = g.run(messages, seeded=(seed == "conclusions"))
    step("guard_run", **{k: v for k, v in report.items() if k != "events"}, events=len(report["events"]))
    ev["guard_events"] = report["events"]
    if report["stopped"]:
        ev["stopped"] = "guard_" + report["stopped"]
    after = db.personal_state()
    ev["personal_unchanged"] = after == before
    ev["due_after"] = db.due_workspaces()
    ids = honcho.conclusion_ids()
    ev["conclusions"] = len(ids)
    if ids:
        honcho.delete_conclusion(ids[0])
        ev["residue_after_delete"] = db.residue(WORKSPACE, ids[0])
    runs = honcho.dreams()
    ev["dream_runs"] = len(runs)
    if runs:
        detail = honcho.dream_detail(runs[0]["id"])
        ev["dream"] = {
            "status": detail.get("status"),
            "created": len(detail.get("created") or []),
            "removed": len(detail.get("removed") or []),
            "peer_card_updated": detail.get("peer_card_updated"),
        }
    try:
        ev["comparison"] = um.run(
            doc, ("direct", "graph", "honcho", "combined"), reps, {"honcho": honcho, "graph": graph}
        )
    except Exception as error:  # noqa: BLE001  the earlier evidence is kept; one fixed word names the failure
        ev["stopped"] = "comparison_" + type(error).__name__
    return ev


def main(argv=None):
    ap = argparse.ArgumentParser(prog="um_honcho_pilot")
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args(argv)
    doc = json.loads(um.CORPUS.read_text())
    plan = {
        "workspace": WORKSPACE,
        "messages": len(corpus_messages(doc)),
        "budget": budget.numbers(len(corpus_messages(doc))),
        "live": args.live,
    }
    if not args.live:
        print(json.dumps(plan))
        return 0
    if not os.environ.get("UM_T2_TOKEN"):
        print("token_missing")
        return 2
    print("live_run_is_released_by_the_ticket_not_by_this_file")
    return 3


if __name__ == "__main__":
    sys.exit(main())
