#!/usr/bin/python3
"""LIFE-889: the synthetic Honcho proof. FABRICATED DATA ONLY: three invented people, 40 invented messages in two test workspaces, 20 scored recall questions.

Runs on lifeos-edge-01 against the private Honcho API (127.0.0.1:8000). Every model call goes through the metering proxy (cap $0.90); this program also reads the proxy's /health
before each question and stops asking at $0.80 so the cap is never the thing that ends a run. Prints one JSON result (no secrets exist in it) and writes it to the path in argv[1].

Measures: recall accuracy /20 at two depths (reasoning_level minimal, then medium), planted-fact extraction (conclusions that carry each planted fact), top-3 retrieval
(workspace search with the question as the query; hit when a top-3 message holds the first expected token), embeddings latency p50/p95 through the proxy, and the proxy's spend."""
import json
import sys
import time
import urllib.request

HONCHO = "http://127.0.0.1:8000"
METER = "http://172.28.0.1:8081"
STOP_AT = 0.80

ALPHA = "proof-alpha"
BETA = "proof-beta"

MESSAGES = {
    ALPHA: [
        ("maple", "My dog Pickle chews every shoe in the hall."),
        ("maple", "I am allergic to cashews, so I read every label twice."),
        ("maple", "I moved from Tallinn to Dunmore in 2019."),
        ("maple", "I play the cello and my birthday is March 3."),
        ("maple", "Next May I fly to Lisbon for a cello festival."),
        ("maple", "My green bicycle is called Fennel."),
        ("maple", "I eat marmalade toast every morning with Earl Grey."),
        ("maple", "I hate Mondays because the Dunmore library is closed."),
        ("maple", "My neighbor Walt waters my plants when I travel."),
        ("maple", "I keep a jar of 212 buttons on my desk."),
        ("tobias", "I inspect lighthouses for a living, mostly along the Harrow coast."),
        ("tobias", "I am afraid of ferries since a storm in 2015."),
        ("tobias", "My sister Odette sends me a brass key every birthday."),
        ("tobias", "My favorite tea is lapsang souchong."),
        ("tobias", "I live in a rented flat above a bakery in Harrow."),
        ("tobias", "I am learning chess and my rating is 1100."),
        ("tobias", "I drive a blue van with the plate HRW-481."),
        ("tobias", "I never eat mushrooms."),
        ("tobias", "My brother-in-law Cass is a diver."),
        ("tobias", "I once visited Tallinn and found a lucky coin there."),
    ],
    BETA: [
        ("ines", "I run a sourdough bakery called Crumb and Co."),
        ("ines", "My sourdough starter is named Gerald."),
        ("ines", "I run five kilometres every Thursday at dawn."),
        ("ines", "My partner is Rafael Okonkwo-Lund."),
        ("ines", "I am allergic to penicillin."),
        ("ines", "I was born in Gdansk and my birthday is November 19."),
        ("ines", "I ride an orange scooter to the market."),
        ("ines", "I am saving for a trip to Kyoto in October."),
        ("ines", "I am teaching myself the oboe."),
        ("ines", "I donate unsold loaves to the shelter on Fridays."),
        ("tobias", "I work at the quarry office counting trucks."),
        ("tobias", "I sing bass in the Larch Hill choir."),
        ("tobias", "I am training for a half marathon in April."),
        ("tobias", "My cat is named Biscuit."),
        ("tobias", "I take the 7:40 bus every morning."),
        ("tobias", "I grow tomatoes on my balcony."),
        ("tobias", "I am saving for a new accordion."),
        ("tobias", "My favorite colour is teal."),
        ("tobias", "I have a scar on my left thumb from a kitchen knife."),
        ("tobias", "I speak a little Finnish."),
    ],
}

# (workspace, peer to ask, question, expected tokens (all must appear), forbidden tokens (none may appear), kind)
QUESTIONS = [
    (ALPHA, "maple", "What is the name of Maple's dog?", ["pickle"], [], "direct"),
    (ALPHA, "maple", "What is Maple allergic to?", ["cashew"], [], "direct"),
    (ALPHA, "maple", "Which city did Maple move from?", ["tallinn"], [], "direct"),
    (ALPHA, "maple", "In which month does Maple travel to Lisbon?", ["may"], [], "direct"),
    (ALPHA, "maple", "What is Maple's bicycle called?", ["fennel"], [], "direct"),
    (ALPHA, "tobias", "What is Tobias afraid of?", ["ferr"], [], "direct"),
    (ALPHA, "tobias", "What is the name of Tobias's sister?", ["odette"], [], "direct"),
    (ALPHA, "tobias", "What is the plate number of Tobias's van?", ["hrw-481"], [], "direct"),
    (BETA, "ines", "What is the name of Ines's sourdough starter?", ["gerald"], [], "direct"),
    (BETA, "ines", "Who is Ines's partner?", ["rafael"], [], "direct"),
    (BETA, "ines", "What is Ines allergic to?", ["penicillin"], [], "direct"),
    (BETA, "ines", "Where does Ines plan to travel in October?", ["kyoto"], [], "direct"),
    (BETA, "ines", "Which instrument is Ines learning?", ["oboe"], [], "direct"),
    (BETA, "tobias", "Which choir does Tobias sing in?", ["larch"], [], "direct"),
    (ALPHA, "maple", "Which instrument does Maple play and where does she travel to play it?", ["cello", "lisbon"], [], "deep"),
    (ALPHA, "tobias", "What does Tobias do for work and what is he afraid of?", ["lighthouse", "ferr"], [], "deep"),
    (BETA, "ines", "What does Ines run and what is her starter called?", ["crumb", "gerald"], [], "deep"),
    (BETA, "tobias", "What does Tobias do for work and what does he sing in?", ["quarry", "larch"], [], "deep"),
    (BETA, "tobias", "What is Tobias afraid of?", [], ["ferr"], "isolation"),
    (ALPHA, "maple", "What is Ines's sourdough starter called?", [], ["gerald"], "isolation"),
]

PLANTED = {ALPHA: ["pickle", "cashew", "tallinn", "cello", "lisbon", "fennel", "marmalade", "walt", "212", "lighthouse", "ferr", "odette", "lapsang", "chess", "hrw-481", "mushroom", "cass"],
           BETA: ["crumb", "gerald", "thursday", "rafael", "penicillin", "gdansk", "scooter", "kyoto", "oboe", "shelter", "quarry", "larch", "half marathon", "biscuit", "bus", "tomato", "accordion", "teal", "finnish"]}


def call(base, method, path, body=None, timeout=300):
    request = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as reply:
        raw = reply.read()
    return json.loads(raw) if raw else None


def spent():
    return call(METER, "GET", "/health", timeout=10)["spent_usd"]


def percentile(values, p):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(p * (len(ordered) - 1))))] if ordered else None


def ingest():
    for workspace, messages in MESSAGES.items():
        call(HONCHO, "POST", "/v3/workspaces", {"id": workspace})
        peers = sorted({p for p, _ in messages})
        for peer in peers:
            call(HONCHO, "POST", f"/v3/workspaces/{workspace}/peers", {"id": peer})
        call(HONCHO, "POST", f"/v3/workspaces/{workspace}/sessions", {"id": "chat", "peers": {p: {} for p in peers}})
        for start in range(0, len(messages), 5):
            call(HONCHO, "POST", f"/v3/workspaces/{workspace}/sessions/chat/messages", {"messages": [{"peer_id": p, "content": c} for p, c in messages[start:start + 5]]})


def wait_for_derivation(limit_s=900):
    began = time.time()
    while time.time() - began < limit_s:
        pending = 0
        for workspace in MESSAGES:
            status = call(HONCHO, "GET", f"/v3/workspaces/{workspace}/queue/status")
            pending += int(status.get("pending_work_units") or 0) + int(status.get("in_progress_work_units") or 0)
        if pending == 0:
            return round(time.time() - began, 1)
        time.sleep(5)
    return None


def conclusions_text(workspace):
    texts = []
    try:
        page = call(HONCHO, "POST", f"/v3/workspaces/{workspace}/conclusions/list", {})
    except Exception:                                                                    # noqa: BLE001 - the shape is checked, the proof reports UNVERIFIED instead of failing
        return None
    items = page.get("items") if isinstance(page, dict) else page
    for item in items or []:
        texts.append(str(item.get("content") or item))
    return " ".join(texts).lower()


def ask(depth):
    rows = []
    for workspace, peer, question, expect, forbid, kind in QUESTIONS:
        if spent() >= STOP_AT:
            rows.append({"q": question, "skipped": "spend guard at $%.2f" % STOP_AT})
            continue
        began = time.time()
        reply = call(HONCHO, "POST", f"/v3/workspaces/{workspace}/peers/{peer}/chat", {"query": question, "reasoning_level": depth})
        text = str((reply or {}).get("content") or "").lower()
        ok = all(t in text for t in expect) and not any(t in text for t in forbid)
        rows.append({"q": question, "kind": kind, "ok": ok, "seconds": round(time.time() - began, 1), "answer": text[:160]})
    return rows


def retrieval():
    hits, total = 0, 0
    for workspace, _, question, expect, forbid, kind in QUESTIONS:
        if not expect:
            continue
        total += 1
        found = call(HONCHO, "POST", f"/v3/workspaces/{workspace}/search", {"query": question, "limit": 3})
        if any(expect[0] in str(m.get("content", "")).lower() for m in (found or [])[:3]):
            hits += 1
    return {"hit_at_3": hits, "of": total}


def embeddings():
    times = []
    for n in range(30):
        began = time.time()
        call(METER, "POST", "/v1/embeddings", {"model": "nomic-embed-text", "input": MESSAGES[ALPHA][n % 20][1]}, timeout=60)
        times.append((time.time() - began) * 1000)
    return {"calls": len(times), "p50_ms": round(percentile(times, 0.5), 1), "p95_ms": round(percentile(times, 0.95), 1)}


def main(out_path):
    result = {"spent_start_usd": spent()}
    ingest()
    result["derivation_seconds"] = wait_for_derivation()
    result["spent_after_derivation_usd"] = spent()
    extraction = {}
    for workspace in MESSAGES:
        text = conclusions_text(workspace)
        extraction[workspace] = None if text is None else {"found": sum(1 for t in PLANTED[workspace] if t in text), "of": len(PLANTED[workspace])}
    result["planted_fact_extraction"] = extraction
    result["retrieval_top3"] = retrieval()
    result["embeddings"] = embeddings()
    for depth in ("minimal", "medium"):
        rows = ask(depth)
        scored = [r for r in rows if "ok" in r]
        result["recall_" + depth] = {"correct": sum(1 for r in scored if r["ok"]), "asked": len(scored), "of": len(QUESTIONS), "rows": rows}
        result["spent_after_" + depth + "_usd"] = spent()
    result["spent_end_usd"] = spent()
    with open(out_path, "w") as handle:
        json.dump(result, handle, indent=1)
    brief = {k: v for k, v in result.items() if not k.startswith("recall_")}
    brief.update({k: {kk: vv for kk, vv in v.items() if kk != "rows"} for k, v in result.items() if k.startswith("recall_")})
    print(json.dumps(brief, indent=1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/honcho_proof_result.json")
