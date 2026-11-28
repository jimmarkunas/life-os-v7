"""GI-1: fill the derived Aura relationship index from the synthetic fixture. Dry run by default.

  python tools/graph_index_sync.py                     plan only, against an empty in-memory graph (nothing is sent anywhere)
  python tools/graph_index_sync.py --apply             apply to the in-memory graph and checkpoint (--times N repeats it)
  python tools/graph_index_sync.py --apply --live      the ONE additive write to Aura namespace gi1-fixture-v1 and its checkpoint row
  python tools/graph_index_sync.py --readback --live   read-only: the live namespace equals the fixture, and the pilot data is unchanged
  python tools/graph_index_sync.py --rebuild           wipe and rebuild the in-memory graph only (refused with --live: no live delete)

A remote write needs both --apply and --live. Live runs happen on Oracle, as the host user that reads the Bitwarden project, from an
environment that has `neo4j` and `pymysql`. Output is counts, booleans and 16-character checksums; never a credential, host or result row.
Exit codes: 0 done, 2 refused before any write, 1 failed."""

import argparse
from datetime import datetime, timezone
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lifeos.platform import graph_index as gi  # noqa: E402

NAMES = {
    "uri": "lifeos/edge/NEO4J_AURA_FREE_URI",
    "user": "lifeos/edge/NEO4J_AURA_FREE_USERNAME",
    "pw": "lifeos/edge/NEO4J_AURA_FREE_PASSWORD",
    "host": "lifeos/edge/CKPT_DB_HOST",
    "name": "lifeos/edge/CKPT_DB_NAME",
    "user_db": "lifeos/edge/CKPT_DB_USER",
    "password": "lifeos/edge/CKPT_DB_PASSWORD",
}


def load_secrets(env_file="~/.config/bws/openclaw.env"):
    """Oracle only: the machine-account token is read from its file into this process, `bws` lists the project, and only the named values are kept."""
    token = ""
    for line in open(os.path.expanduser(env_file)):
        line = line.strip().removeprefix("export ")
        if line.startswith("BWS_ACCESS_TOKEN="):
            token = line.split("=", 1)[1].strip().strip("'\"")
    done = subprocess.run(
        ["bws", "secret", "list", "-o", "json"],
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "BWS_ACCESS_TOKEN": token},
        capture_output=True,
        text=True,
        timeout=30,
    )
    if done.returncode:
        raise SystemExit("bws_list_failed")
    by = {r["key"]: r.get("value") for r in json.loads(done.stdout)}
    if not all(by.get(n) for n in NAMES.values()):
        raise SystemExit("secret_missing")
    return {k: by[v] for k, v in NAMES.items()}


def live_parts():
    import neo4j

    secrets = load_secrets()
    driver = neo4j.GraphDatabase.driver(
        secrets["uri"], auth=(secrets["user"], secrets["pw"]), warn_notification_severity="OFF", connection_timeout=8
    )
    driver.verify_connectivity()
    config = {
        "host": secrets["host"],
        "name": secrets["name"],
        "user": secrets["user_db"],
        "password": secrets["password"],
    }
    store = gi.CheckpointStore(gi.connect_checkpoint(config))
    store.ensure_table()
    return gi.AuraClient(driver), store


def emit(**kv):
    print(json.dumps(kv, sort_keys=True))


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def main(argv=None, client=None, store=None, now=utc_now):
    ap = argparse.ArgumentParser(prog="graph_index_sync")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--readback", action="store_true")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--times", type=int, default=1)
    args = ap.parse_args(argv)
    out = emit
    if args.live and not (args.apply or args.readback):
        out(refused="LIVE_NEEDS_APPLY_OR_READBACK")
        return 2
    if args.live and args.rebuild:
        out(refused="NO_LIVE_DELETE")
        return 2
    if args.readback and not args.live:
        out(refused="READBACK_IS_LIVE")
        return 2
    nodes, edges = gi.fixture()
    ns = gi.NS_FIXTURE
    try:
        if args.live:
            client, store = (client, store) if client else live_parts()
        else:
            client, store = client or gi.MemoryClient(), store or gi.MemoryStore()
        if args.readback:
            return readback(client, store, ns, nodes, edges, now(), out)
        if args.rebuild:
            client.wipe(ns)
            args.apply = True
        if not args.apply:
            desired_nodes, desired_edges = gi.validate_all(nodes, edges)
            existing_nodes, existing_edges, _ = client.read_namespace(ns)
            node_rows, edge_rows = gi.plan(existing_nodes, existing_edges, desired_nodes, desired_edges)
            out(mode="dry-run", ns=ns, would_write_nodes=len(node_rows), would_write_edges=len(edge_rows))
            return 0
        for _ in range(max(1, args.times)):
            out(
                mode="live" if args.live else "memory",
                **gi.apply(client, store, ns, nodes, edges, now(), live=args.live),
            )
        return 0
    except gi.Refused as error:
        out(refused=str(error))
        return 2
    except gi.Failed as error:
        out(failed=str(error))
        return 1


def readback(client, store, ns, nodes, edges, now, out):
    """No write: the namespace equals the fixture, the checkpoint serves it, and the pilot data matches its baseline."""
    desired_nodes, desired_edges = gi.validate_all(nodes, edges)
    live_nodes, live_edges, foreign = client.read_namespace(ns)
    equal = (live_nodes, live_edges) == (desired_nodes, desired_edges) and not foreign
    try:
        served_nodes, served_edges = gi.serve(client, store, ns, now)
        served = len(served_nodes) == len(desired_nodes) and len(served_edges) == len(desired_edges)
    except gi.Unserved as error:
        served = str(error)
    pilot = client.pilot_state() if hasattr(client, "pilot_state") else None
    pilot_unchanged = pilot == gi.PILOT_BASELINE if pilot is not None else None
    row = store.latest(ns)
    out(
        mode="readback",
        ns=ns,
        equals_fixture=equal,
        foreign=foreign,
        nodes=len(live_nodes),
        edges=len(live_edges),
        checksum16=gi.checksum(live_nodes, live_edges)[:16],
        served=served,
        checkpoint_generation=row["generation"] if row else None,
        checkpoint_status=row["status"] if row else None,
        pilot_unchanged=pilot_unchanged,
        pilot=pilot,
    )
    return 0 if equal and served is True and pilot_unchanged is not False else 1


if __name__ == "__main__":
    sys.exit(main())
