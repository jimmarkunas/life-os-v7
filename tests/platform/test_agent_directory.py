import json
import re
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from lifeos.platform import access_policy as A
from lifeos.platform import agent_directory as D
from tests.platform import agent_directory_fixture as F

ROOT = Path(__file__).resolve().parents[2]
READER, STRANGER = "a1" * 32, "ee" * 32
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=D.CENTRAL)
POLICY = A.load(json.dumps({"version": 1, "default": {}, "agents": {READER: {"name": "reader", "notion": "read"}}}))


def build(rows=None, now=NOW):
    return D.build(F.rows() if rows is None else rows, "fixture", now, READER)


def row(agent_id, **patch):
    base = dict(next(r for r in F.rows() if r["Agent ID"] == agent_id))
    base.update(patch)
    return base


def resolve(directory=None, **kw):
    args = dict(now=NOW, policy=POLICY, principal=READER, workspace="Personal")
    args.update(kw)
    return D.resolve(directory or build(), **args)


def proof(agent_id, host, at=NOW - timedelta(minutes=5), **kw):
    return D.Proof(agent_id, host, kw.get("session", "sess-1"), at, kw.get("receipt", "receipt-123"))


def claimed(agent_id, **extra):
    return row(agent_id, **{"Discovery State": "Routable Verified", "Reachability": "Verified", **extra})


class Catalog(unittest.TestCase):
    def test_the_24_row_directory_parses_by_platform_and_kind(self):
        d = build()
        self.assertEqual(len(d.entries), 24)
        by = {}
        for e in d.entries:
            by[e.platform] = by.get(e.platform, 0) + 1
        self.assertEqual(by, {"Claude": 5, "ChatGPT": 3, "Buzz": 8, "Orca": 2, "Hermes": 6})
        kinds = {e.agent_id: e.kind for e in d.entries}
        self.assertEqual(kinds["bot_hermes_main"], "agent_identity")
        self.assertEqual(kinds["buzz_lifeos_codex"], "agent_identity")
        self.assertEqual(kinds["surface_claude_code_web"], "client_surface")
        self.assertEqual(kinds["orca_agent_codex"], "agent_session")
        self.assertEqual(kinds["buzz_persona_council-auditor"], "persona")

    def test_no_row_is_routable_and_each_status_is_honest(self):
        snap = D.snapshot(build(), NOW)
        status = {e["agent_id"]: e["status"] for e in snap["entries"]}
        self.assertNotIn("routable-verified", status.values())
        self.assertTrue(set(status.values()) <= set(D.STATUSES))
        self.assertEqual({s for a, s in status.items() if a.startswith("surface_")}, {"manual-only"})
        self.assertEqual({s for a, s in status.items() if a.startswith("bot_hermes_") or a.startswith("orca_")}, {"configured-untested"})
        self.assertEqual({s for a, s in status.items() if a.startswith("buzz_lifeos_")}, {"not-reachable"})
        self.assertEqual(status["buzz_persona_council-auditor"], "manual-only")      # a persona is never an addressable agent

    def test_one_hermes_runtime_six_profiles_match_the_repo_table(self):
        text = (ROOT / "hermes" / "connector-allowlists.txt").read_text()
        self.assertEqual(sorted(D.hermes_profiles(text)), ["claude-hermes", "developer", "librarian", "main", "research", "writer"])
        self.assertEqual(D.check_hermes(build(), text), [])
        self.assertEqual({e.host for e in build().entries if e.platform == "Hermes"}, {"Oracle"})            # a Mac Hermes Desktop is a client, never a second server row
        rows = [r for r in F.rows() if r["Agent ID"] != "bot_hermes_writer"] + [row("bot_hermes_main", **{"Agent ID": "bot_hermes_ghost", "Identity Alias": "ghost"})]
        self.assertEqual(D.check_hermes(build(rows), text), ["bot_hermes_ghost", ("missing", "writer")])

    def test_a_display_name_never_merges_identities(self):
        d = build()
        self.assertEqual(resolve(d, agent_id="buzz_lifeos_hermes").entry.platform, "Buzz")
        self.assertEqual(resolve(d, agent_id="bot_hermes_main").entry.platform, "Hermes")
        self.assertEqual(resolve(d, alias="Hermes").reason, "unknown_agent")                                  # a display word is not an alias of either identity


class FailClosed(unittest.TestCase):
    def test_duplicate_agent_id_refuses_the_whole_directory(self):
        with self.assertRaises(D.DirectoryError) as c:
            build(F.rows() + [row("bot_hermes_main")])
        self.assertEqual(c.exception.reason, "duplicate_agent_id")

    def test_missing_or_bad_workspace_refuses(self):
        for space, reason in (("[]", "missing_workspace"), ("", "missing_workspace"), ('["Elsewhere"]', "bad_workspace")):
            with self.assertRaises(D.DirectoryError) as c:
                build([row("bot_hermes_main", Space=space)])
            self.assertEqual(c.exception.reason, reason)

    def test_secret_material_is_refused_and_never_echoed(self):
        nsec = "nsec1" + "q" * 58
        for field in ("Notes", "Identity Alias", "Invocation Route"):
            with self.assertRaises(D.DirectoryError) as c:
                build([row("buzz_lifeos_codex", **{field: "key " + nsec})])
            self.assertEqual(c.exception.reason, "secret_material")
            self.assertNotIn(nsec, str(c.exception))
        for text in ("-----" + "BEGIN OPENSSH PRIVATE KEY" + "-----", "ghp_" + "a" * 30, "api_key = abcdefgh12345678"):
            with self.assertRaises(D.DirectoryError):
                build([row("buzz_lifeos_codex", Notes=text)])

    def test_a_full_public_npub_is_redacted_to_a_partial_in_the_directory(self):
        npub = "npub1" + "q" * 58
        d = build([row("buzz_lifeos_codex", **{"Identity Alias": npub})])
        self.assertEqual(d.entries[0].alias, "npub…qqq")
        self.assertNotIn(npub, json.dumps(D.snapshot(d, NOW)))

    def test_bad_rows_and_unknown_record_types_refuse(self):
        for patch in ({"Record Type": "Robot"}, {"Platform": "Gemini"}, {"Agent ID": "Bad ID!"}, {"date:Last Checked:start": "yesterday"}, {"date:Last Checked:start": ""}):
            with self.assertRaises(D.DirectoryError):
                build([row("bot_hermes_main", **patch)])
        for rows in ([], None, "x"):
            with self.assertRaises(D.DirectoryError):
                D.build(rows, "fixture", NOW)

    def test_one_bad_row_is_skipped_with_its_number_and_reason_and_the_rest_still_resolve(self):       # LIFE-985
        bad = [row("bot_hermes_main", **{"Agent ID": ""}), row("buzz_lifeos_codex", Notes="key " + "nsec1" + "q" * 58), row("bot_hermes_writer", **{"date:Last Checked:start": "yesterday"})]
        with self.assertLogs("lifeos.agent_directory", "WARNING") as logged:
            d = build(F.rows() + bad[:1])
        self.assertEqual(d.skipped, ((len(F.rows()) + 1, "bad_agent_id"),))
        self.assertEqual(len(d.entries), len(build().entries))
        self.assertEqual(resolve(d, agent_id="buzz_lifeos_hermes").decision, "allow")
        self.assertEqual(logged.records[0].getMessage(), "agent directory: row %d skipped: bad_agent_id" % (len(F.rows()) + 1))
        with self.assertLogs("lifeos.agent_directory", "WARNING") as logged:
            d = build(F.rows() + bad[1:])
        self.assertEqual([r for _, r in d.skipped], ["secret_material", "bad_last_checked"])
        self.assertNotIn("nsec1", " ".join(r.getMessage() for r in logged.records))                      # the reason word only, never a row's text
        self.assertEqual(build().skipped, ())

    def test_a_duplicate_among_good_rows_and_a_directory_with_no_good_row_are_still_refused(self):     # LIFE-985
        with self.assertRaises(D.DirectoryError) as c:
            build(F.rows() + [row("bot_hermes_main"), row("bot_hermes_main", **{"Agent ID": ""})])
        self.assertEqual(c.exception.reason, "duplicate_agent_id")
        with self.assertRaises(D.DirectoryError) as c:
            build([row("bot_hermes_main", **{"Agent ID": ""}), row("bot_hermes_main", **{"Agent ID": "Bad!"})])
        self.assertEqual(c.exception.reason, "bad_agent_id")

    def test_unavailable_notion_raises_and_nothing_is_cached(self):
        def boom():
            raise RuntimeError("503 token=abcdefghijkl")
        with self.assertRaises(D.DirectoryError) as c:
            D.load(boom, POLICY, READER, NOW)
        self.assertEqual(c.exception.reason, "directory_unavailable")
        self.assertNotIn("503", str(c.exception))
        with self.assertRaises(D.DirectoryError):
            D.load(D.fetch_unavailable, POLICY, READER, NOW)                                              # the default reader: no live read until it is approved

    def test_grant_is_checked_before_any_read_and_again_on_every_resolve(self):
        calls = []
        for policy, principal in ((POLICY, STRANGER), (None, READER), (A.load(json.dumps({"version": 1, "default": {}, "agents": {READER: {"notion": "off"}}})), READER)):
            with self.assertRaises(D.DirectoryError) as c:
                D.load(lambda: calls.append(1) or F.rows(), policy, principal, NOW)
            self.assertEqual(c.exception.reason, "grant_denied")
        self.assertEqual(calls, [])
        d = D.load(F.rows, POLICY, READER, NOW)
        revoked = A.load(json.dumps({"version": 1, "default": {}, "agents": {READER: {"notion": "off"}}}))
        self.assertEqual(D.resolve(d, NOW, revoked, READER, agent_id="bot_hermes_main", workspace="Personal").reason, "grant_denied")
        self.assertEqual(D.resolve(d, NOW, POLICY, STRANGER, agent_id="bot_hermes_main", workspace="Personal").reason, "grant_denied")

    def test_stale_rows_are_not_reachable_and_state_is_not_kept_across_restarts(self):
        later = NOW + timedelta(days=8)
        e = resolve(build(now=later), now=later, agent_id="bot_hermes_main")
        self.assertEqual((e.decision, e.status, e.reason), ("allow", "not-reachable", "stale_data"))
        self.assertEqual(resolve(agent_id="bot_hermes_main").status, "configured-untested")
        future = build([row("bot_hermes_main", **{"date:Last Checked:start": "2026-12-30"})])
        self.assertEqual(resolve(future, agent_id="bot_hermes_main").reason, "stale_data")                # a future check time is not trusted

    def test_retired_is_never_reachable(self):
        d = build([row("bot_hermes_main", **{"Discovery State": "Retired"})])
        self.assertEqual(resolve(d, agent_id="bot_hermes_main").status, "not-reachable")


class Routing(unittest.TestCase):
    def test_a_row_claim_alone_is_never_routable(self):
        d = build([claimed("bot_hermes_main")])
        r = resolve(d, agent_id="bot_hermes_main")
        self.assertEqual((r.status, r.reason), ("configured-untested", "row_claim_without_proof"))

    def test_routable_needs_a_fresh_same_host_proof_for_that_agent(self):
        d = build([claimed("bot_hermes_main")])
        ok = resolve(d, agent_id="bot_hermes_main", proofs=[proof("bot_hermes_main", "Oracle")])
        self.assertEqual(ok.status, "routable-verified")
        cases = {"proof_wrong_host": proof("bot_hermes_main", "Mac"), "proof_expired": proof("bot_hermes_main", "Oracle", NOW - timedelta(hours=2)),
                 "proof_incomplete": proof("bot_hermes_main", "Oracle", receipt=""), "row_claim_without_proof": proof("bot_hermes_writer", "Oracle")}
        for reason, p in cases.items():
            r = resolve(d, agent_id="bot_hermes_main", proofs=[p])
            self.assertEqual((r.status, r.reason), ("configured-untested", reason), reason)
        self.assertEqual(resolve(d, agent_id="bot_hermes_main", proofs=[proof("bot_hermes_main", "Oracle", receipt="token=abcdefgh1234")]).status, "configured-untested")

    def test_candidates_surfaces_and_personas_are_never_routable_even_with_a_proof(self):
        for aid, host in (("buzz_lifeos_unknown-jr3", "Cloud"), ("buzz_persona_council-auditor", "Cloud"), ("surface_claude_code_web", "Cloud")):
            d = build([claimed(aid, **{"Space": '["Personal"]'})])
            r = resolve(d, agent_id=aid, proofs=[proof(aid, host)])
            self.assertNotEqual(r.status, "routable-verified", aid)
        d = build([claimed("buzz_lifeos_codex")])                                                           # a truncated npub is not an identity
        self.assertEqual(resolve(d, agent_id="buzz_lifeos_codex", proofs=[proof("buzz_lifeos_codex", "Cloud")]).reason, "identity_partial")

    def test_a_proof_for_an_unlisted_agent_creates_nothing(self):
        r = resolve(agent_id="agent_not_listed", proofs=[proof("agent_not_listed", "Oracle")])
        self.assertEqual((r.decision, r.reason), ("deny", "unknown_agent"))

    def test_no_broadcast_wildcard_or_two_selectors(self):
        for kw in ({"agent_id": "*"}, {"agent_id": "all"}, {"alias": "@everyone"}, {"agent_id": "a,b"}, {"alias": "x|y"}):
            self.assertEqual(resolve(**kw).reason, "broadcast_refused", kw)
        self.assertEqual(resolve().reason, "bad_request")
        self.assertEqual(resolve(agent_id="bot_hermes_main", alias="main").reason, "bad_request")
        self.assertEqual(resolve(agent_id="  ").reason, "bad_request")

    def test_alias_must_be_exact_and_unique(self):
        d = build()
        self.assertEqual(resolve(d, alias="  LIBRARIAN ").entry.agent_id, "bot_hermes_librarian")
        self.assertEqual(resolve(d, alias="lib").reason, "unknown_agent")
        clash = build(F.rows() + [row("bot_hermes_main", **{"Agent ID": "bot_hermes_main2", "Identity Alias": "Librarian"})])
        self.assertEqual(resolve(clash, alias="librarian").reason, "ambiguous_alias")
        self.assertEqual(resolve(clash, agent_id="bot_hermes_librarian").decision, "allow")                 # the stable id still works

    def test_workspaces_cross_space_tgp_and_unknown_are_denied(self):
        self.assertEqual(resolve(agent_id="bot_hermes_main", workspace="Bytalos").reason, "cross_space_denied")
        self.assertEqual(resolve(agent_id="bot_hermes_main", workspace=None).reason, "missing_workspace")
        self.assertEqual(resolve(agent_id="bot_hermes_main", workspace="TGP").reason, "workspace_not_granted")
        self.assertEqual(resolve(agent_id="bot_hermes_main", workspace="Unknown").reason, "workspace_not_granted")
        self.assertEqual(resolve(agent_id="surface_claude_code_web", workspace="Personal").reason, "cross_space_denied")      # a surface whose space is Unknown is nobody's
        tgp = build([row("bot_hermes_main", Space='["TGP"]')])
        self.assertEqual(resolve(tgp, agent_id="bot_hermes_main", workspace="TGP").reason, "workspace_not_granted")      # a TGP label is not a TGP grant
        self.assertEqual(resolve(tgp, agent_id="bot_hermes_main", workspace="Personal").reason, "cross_space_denied")

    def test_resolving_authorizes_nothing(self):
        r = resolve(agent_id="bot_hermes_main")
        self.assertEqual(r.decision, "allow")
        self.assertFalse(hasattr(r, "send") or hasattr(r, "execute"))
        self.assertEqual(D.snapshot(build(), NOW)["authorizes"], [])


class Snapshot(unittest.TestCase):
    def test_deterministic_redacted_with_provenance(self):
        a, b = D.snapshot(build(), NOW), D.snapshot(build(list(reversed(F.rows()))), NOW)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertEqual([e["agent_id"] for e in a["entries"]], sorted(e["agent_id"] for e in a["entries"]))
        self.assertEqual((a["count"], a["source"], a["principal"]), (24, "fixture", READER[:8]))
        self.assertNotIn(READER, json.dumps(a))
        self.assertTrue(all(e["last_checked"].startswith("2026-11-16") and e["stale"] is False for e in a["entries"]))
        self.assertTrue(all(set(e) == set(a["entries"][0]) for e in a["entries"]))
        text = json.dumps(a)
        self.assertFalse(D.SECRET.search(text) or re.search(r"npub1[a-z0-9]{58}", text))

    def test_no_route_text_or_notes_leave_in_the_snapshot(self):
        text = json.dumps(D.snapshot(build(), NOW))
        self.assertNotIn("Concord Room", text)
        self.assertNotIn("lifeos-edge-01", text)


class Boundaries(unittest.TestCase):
    def test_the_module_reaches_nothing(self):
        source = (ROOT / "lifeos" / "platform" / "agent_directory.py").read_text()
        code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
        for banned in ("requests", "urllib", "subprocess", "socket", "http.client", "sched", "threading", "sqlite3", "open(", "os.environ", "sleep"):
            self.assertNotIn(banned, code, banned)


if __name__ == "__main__":
    unittest.main()
