import copy
import json
import unittest
from unittest.mock import patch
from lifeos.interview import create, notion, prep, stage
from lifeos.interview.models import Child, ParentQuery, PrepEvidence, RoundQuery, Scan
from lifeos.platform.notion_client import NotionError
from lifeos.platform.runtime import RunContext
from tests.interview.test_create import WritableFake, machine_parent, PARENT, ROUND
from tests.interview.test_notion import ENV, block


def machine_round(client, ordinal=1, day=None):
    parent_id = machine_parent(client)
    query = RoundQuery(True, day or "2026-10-01", ordinal=ordinal)
    result = create.apply(client, ENV, PARENT, query, True, RunContext.start(60), set())
    return parent_id, "created-2", query


class PrepFake(WritableFake):
    def call(self, method, path, body=None):
        if method == "PATCH":
            self.calls.append((method, path))
            page_id = path.split("/")[2]
            index = next((i for i, block_value in enumerate(self.blocks[page_id]) if block_value.get("id") == body.get("after")), len(self.blocks[page_id]) - 1)
            inserted = copy.deepcopy(body["children"])
            for i, value in enumerate(inserted):
                value["id"] = f"derived-{i}"
            self.blocks[page_id][index + 1:index + 1] = inserted
            return {}
        return super().call(method, path, body)


class InputTests(unittest.TestCase):
    def test_validation_normalization_hash_order_and_render(self):
        for value in (PrepEvidence(), PrepEvidence(focus=("  ",)), PrepEvidence(focus=("x" * 501,)),
                      PrepEvidence(focus=tuple(str(i) for i in range(6)))):
            with self.assertRaises((ValueError, OverflowError)):
                prep.normalize(value)
        normalized = prep.normalize(PrepEvidence(focus=(" First ", "First", "Second"), questions=("Q2", "Q1")))
        self.assertEqual(normalized["focus"], ("First", "Second"))
        self.assertEqual(normalized["questions"], ("Q2", "Q1"))
        self.assertEqual(prep.digest(normalized), prep.digest(normalized))
        rendered = prep.render(normalized)
        self.assertEqual([notion.text(b) for b in rendered[1:]], ["Focus", "First", "Second", "Questions", "Q2", "Q1"])
        self.assertEqual([b["type"] for b in rendered].count("heading_3"), 2)


class PrepTests(unittest.TestCase):
    def apply(self, client, query, data, live=True):
        return prep.apply(client, ENV, PARENT, query, data, live, RunContext.start(60))

    def test_dry_live_exact_replay_and_conflict_are_append_only(self):
        client = PrepFake()
        _, round_id, query = machine_round(client)
        evidence = PrepEvidence(focus=("Focus A",), pressure_points=("Risk A",), questions=("Ask A",))
        before = notion.protected_snapshot(client, round_id)
        dry = self.apply(client, query, evidence, False)
        self.assertEqual((dry["writes_planned"], dry["writes"], dry["code"]), (1, 0, "derived_allowed"))
        self.assertEqual(self.apply(client, query, evidence)["code"], "derived_created")
        replay = self.apply(client, query, evidence)
        self.assertEqual((replay["code"], replay["writes"]), ("derived_match", 0))
        self.assertEqual(len([c for c in client.calls if c[0] == "PATCH"]), 1)
        blocks = client.blocks[round_id]
        heading = next(i for i, b in enumerate(blocks) if notion.text(b) == "Derived")
        self.assertTrue(notion.text(blocks[heading + 1]).startswith(prep.DERIVED_PREFIX))
        self.assertEqual(notion.protected_snapshot(client, round_id), before)
        self.assertEqual(self.apply(client, query, PrepEvidence(focus=("Different",)))["code"], "derived_conflict")
        self.assertEqual(len([c for c in client.calls if c[0] == "PATCH"]), 1)
        heading, payload = notion.derived_blocks(client, round_id)
        current_hash, full_hash = notion.text(payload[0]).removeprefix(prep.DERIVED_PREFIX).split(";")
        self.assertRegex(current_hash, r"^[0-9a-f]{64}$")
        self.assertRegex(full_hash, r"^[0-9a-f]{64}$")
        self.assertEqual(current_hash, prep.digest(prep.normalize(evidence)))
        self.assertEqual(full_hash, prep.digest(prep.normalize(evidence)))

    def test_protected_snapshot_failure_and_append_failure_do_not_retry(self):
        client = PrepFake()
        _, round_id, query = machine_round(client)
        with patch("lifeos.interview.notion.protected_snapshot", return_value=None):
            result = self.apply(client, query, PrepEvidence(focus=("A",)))
        self.assertEqual(result["code"], "readback_protected_missing")
        self.assertFalse(any(c[0] == "PATCH" for c in client.calls))
        original = notion.append_children
        with patch("lifeos.interview.notion.append_children", side_effect=NotionError("private")) as append:
            result = self.apply(client, query, PrepEvidence(focus=("A",)))
        self.assertEqual(result["code"], "derived_readback_failed")
        append.assert_called_once()
        with patch("lifeos.interview.notion.append_children", side_effect=original):
            self.assertEqual(self.apply(client, query, PrepEvidence(focus=("A",)))["code"], "derived_created")

    def test_unexpected_derived_content_conflicts_and_notes_are_not_inputs(self):
        client = PrepFake()
        _, round_id, query = machine_round(client)
        client.blocks[round_id].append(block("existing payload"))
        protected = notion.protected_snapshot(client, round_id)
        calls = []
        original = client.call
        def record(method, path, body=None):
            calls.append(path)
            return original(method, path, body)
        client.call = record
        result = self.apply(client, query, PrepEvidence(focus=("Input",)))
        self.assertEqual(result["code"], "derived_conflict")
        self.assertEqual(notion.protected_snapshot(client, round_id), protected)
        self.assertFalse(any("Live Notes" in path or "Raw Notes" in path for path in calls))
        serialized = json.dumps(result)
        self.assertNotIn("Input", serialized)

    def test_ordinal_carry_forward_merges_only_pressure_and_questions(self):
        client = PrepFake()
        parent_id, previous_id, previous_query = machine_round(client, ordinal=1)
        old = PrepEvidence(focus=("Old focus",), strongest_evidence=("Old proof",),
                           pressure_points=("Old risk", "Shared risk"), questions=("Old question",))
        self.assertEqual(prep.apply(client, ENV, PARENT, previous_query, old, True, RunContext.start(60))["code"], "derived_created")
        current_query = RoundQuery(True, "2026-10-02", ordinal=3)
        create.apply(client, ENV, PARENT, current_query, True, RunContext.start(60), set())
        current = PrepEvidence(focus=("Current focus",), strongest_evidence=("Current proof",),
                               pressure_points=("Current risk", "Shared risk"), questions=("Current question",))
        result = self.apply(client, current_query, current)
        self.assertEqual(result["code"], "derived_created")
        heading, payload = notion.derived_blocks(client, "created-3")
        rendered = [notion.text(b) for b in payload]
        self.assertIn("Current risk", rendered)
        self.assertIn("Old risk", rendered)
        self.assertEqual(rendered.count("Shared risk"), 1)
        self.assertIn("Old question", rendered)
        self.assertNotIn("Old focus", rendered)
        self.assertNotIn("Old proof", rendered)
        self.assertLess(rendered.index("Current risk"), rendered.index("Old risk"))
        self.assertLess(rendered.index("Current question"), rendered.index("Old question"))

    def _date_round(self, client, parent_id, day, interviewer):
        query = RoundQuery(True, day, interviewer=interviewer)
        result = create.apply(client, ENV, PARENT, query, True, RunContext.start(60), set())
        self.assertEqual(result["code"], "round_created")
        return f"created-{len(client.posts)}", query

    def test_date_fallback_selects_latest_earlier_and_ignores_same_or_later(self):
        client = PrepFake()
        parent_id = machine_parent(client)
        prior_id, prior_query = self._date_round(client, parent_id, "2026-10-01", "Prior One")
        latest_id, latest_query = self._date_round(client, parent_id, "2026-10-03", "Prior Latest")
        later_id, _ = self._date_round(client, parent_id, "2026-10-05", "Later Date")
        prep.apply(client, ENV, PARENT, prior_query, PrepEvidence(pressure_points=("Older",)), True, RunContext.start(60))
        prep.apply(client, ENV, PARENT, latest_query, PrepEvidence(pressure_points=("Latest Earlier",)), True, RunContext.start(60))
        same_id, _ = self._date_round(client, parent_id, "2026-10-02", "Same Date")
        client.blocks[same_id][1] = block(create.identity_text(RoundQuery(True, "2026-10-04", interviewer="Same Date")))
        prep.apply(client, ENV, PARENT, RoundQuery(True, "2026-10-04", interviewer="Same Date"), PrepEvidence(pressure_points=("Same",)), True, RunContext.start(60))
        prep.apply(client, ENV, PARENT, RoundQuery(True, "2026-10-05", interviewer="Later Date"), PrepEvidence(pressure_points=("Later",)), True, RunContext.start(60))
        current_id, _ = self._date_round(client, parent_id, "2026-10-06", "Current")
        client.blocks[current_id][1] = block(create.identity_text(RoundQuery(True, "2026-10-04", interviewer="Current")))
        current_query = RoundQuery(True, "2026-10-04", interviewer="Current", explicit_child_page_id=current_id)
        result = prep.apply(client, ENV, PARENT, current_query, PrepEvidence(focus=("Current",)), True, RunContext.start(60))
        self.assertEqual(result["code"], "derived_created")
        _, payload = notion.derived_blocks(client, current_id)
        values = [notion.text(b) for b in payload]
        self.assertIn("Latest Earlier", values)
        self.assertNotIn("Same", values)
        self.assertNotIn("Later", values)
        self.assertLess(values.index("Latest Earlier"), values.index("Older"))

    def test_ambiguous_latest_date_means_empty_carry_but_current_succeeds(self):
        client = PrepFake()
        parent_id = machine_parent(client)
        first_id, first_query = self._date_round(client, parent_id, "2026-10-02", "Prior A")
        second_id, second_query = self._date_round(client, parent_id, "2026-10-03", "Prior B")
        client.blocks[first_id][1] = block(create.identity_text(RoundQuery(True, "2026-10-03", interviewer="Prior A")))
        prep.apply(client, ENV, PARENT, first_query, PrepEvidence(pressure_points=("A",)), True, RunContext.start(60))
        prep.apply(client, ENV, PARENT, second_query, PrepEvidence(pressure_points=("B",)), True, RunContext.start(60))
        current_id, _ = self._date_round(client, parent_id, "2026-10-05", "Current")
        client.blocks[current_id][1] = block(create.identity_text(RoundQuery(True, "2026-10-04", interviewer="Current")))
        current_query = RoundQuery(True, "2026-10-04", interviewer="Current", explicit_child_page_id=current_id)
        result = prep.apply(client, ENV, PARENT, current_query, PrepEvidence(focus=("Current",)), True, RunContext.start(60))
        self.assertEqual(result["code"], "derived_created")
        _, payload = notion.derived_blocks(client, current_id)
        values = [notion.text(b) for b in payload]
        self.assertNotIn("A", values)
        self.assertNotIn("B", values)

    def test_rejects_oversized_caps_and_returns_counts_only(self):
        client = PrepFake()
        _, _, query = machine_round(client)
        for evidence, code in ((PrepEvidence(focus=("x" * 501,)), "prep_too_large"),
                               (PrepEvidence(questions=("q",) * 6), "prep_too_large"),
                               (PrepEvidence(questions=(" ",)), "prep_invalid")):
            result = self.apply(client, query, evidence)
            self.assertEqual(result["code"], code)
            self.assertEqual(result["writes"], 0)
            self.assertNotIn("x" * 20, json.dumps(result))

    def test_return_and_logs_exclude_private_evidence(self):
        client = PrepFake()
        _, _, query = machine_round(client)
        private = "Confidential synthetic evidence"
        result = self.apply(client, query, PrepEvidence(focus=(private,)))
        self.assertEqual(set(result), {"writes_planned", "writes", "code"})
        self.assertNotIn(private, json.dumps(result))
        self.assertFalse(any("/jobs/" in path or "Live Notes" in path or "Raw Notes" in path for _, path in client.calls))

    def test_marker_requires_canonical_paragraph_and_valid_hash(self):
        client = PrepFake()
        _, page_id, query = machine_round(client)
        normalized = prep.normalize(PrepEvidence(focus=("A",)))
        marker = prep.render(normalized)[0]
        canonical = notion.text(marker)
        for kind, text, expected in (("paragraph", canonical, "marker"),
                                     ("heading_3", canonical, "conflict"),
                                     ("bulleted_list_item", canonical, "conflict"),
                                     ("paragraph", prep.DERIVED_PREFIX + "z" * 64 + ";" + prep.digest(normalized), "conflict"),
                                     ("paragraph", prep.DERIVED_PREFIX + prep.digest(normalized) + ";" + "z" * 64, "conflict")):
            with self.subTest(kind=kind, text=text[-8:]):
                marker_block = copy.deepcopy(marker)
                marker_block["type"] = kind
                marker_block[kind] = marker_block.pop("paragraph")
                marker_block[kind]["rich_text"][0]["text"]["content"] = text
                client.blocks[page_id] = client.blocks[page_id][:5] + [marker_block] + prep.render(normalized)[1:]
                state, _, _ = prep._read_derived(client, page_id)
                self.assertEqual(state, expected)

    def test_current_priority_truncates_only_inherited_items(self):
        current = prep.normalize(PrepEvidence(pressure_points=("A", "B", "C", "D"), questions=("Q1", "Q2", "Q3", "Q4")))
        inherited = {"pressure_points": ("A", "E", "F", "G"), "questions": ("Q5", "Q6")}
        merged = prep._merge(current, inherited)
        self.assertEqual(merged["pressure_points"], ("A", "B", "C", "D", "E"))
        self.assertEqual(merged["questions"], ("Q1", "Q2", "Q3", "Q4", "Q5"))
        with self.assertRaises(OverflowError):
            prep.normalize(PrepEvidence(pressure_points=tuple("ABCDEF")))

    def test_carry_uses_existing_scan_and_fetches_only_winning_owner(self):
        client = PrepFake()
        parent_id, prior_id, prior_query = machine_round(client, ordinal=1)
        prep.apply(client, ENV, PARENT, prior_query, PrepEvidence(pressure_points=("Prior",)), True, RunContext.start(60))
        current = Child("current", parent_id, interview_date="2026-10-02", ordinal=2, identity_valid=True)
        prior = Child(prior_id, parent_id, interview_date="2026-10-01", ordinal=1, identity_valid=True)
        children = Scan((prior, current), True)
        with patch("lifeos.interview.notion.child_scan", side_effect=AssertionError("rescan")), \
             patch("lifeos.interview.notion.explicit_child", side_effect=AssertionError("identity refetch")), \
             patch("lifeos.interview.notion.ownership", wraps=notion.ownership) as ownership:
            inherited = prep._carry(client, children, current, RunContext.start(60))
        self.assertEqual(inherited["pressure_points"], ["Prior"])
        ownership.assert_called_once_with(client, prior_id, "round")

    def test_b3_stage_failures_block_and_uncertain_write_stops(self):
        client = PrepFake()
        machine_parent(client)
        cases = [
            ({"writes_planned": 0, "writes": 0, "code": "derived_conflict"}, 1, 1),
            ({"writes_planned": 1, "writes": 1, "code": "derived_readback_failed"}, 1, 2),
            ({"writes_planned": 0, "writes": 0, "code": "derived_match"}, 2, 2),
        ]
        for outcome, expected_calls, evidence_count in cases:
            with self.subTest(code=outcome["code"]), patch("lifeos.interview.prep.apply", return_value=outcome) as apply_prep:
                result = stage.run(5, True, environ=ENV, client=client,
                    prep_evidence=[(PARENT, ROUND, PrepEvidence(focus=("A",)))] * evidence_count)
                self.assertEqual(apply_prep.call_count, expected_calls)
                if outcome["code"] == "derived_conflict":
                    self.assertEqual((result["blocked"], result["why"]["derived_conflict"], result["writes"]), (1, 1, 0))
                elif outcome["code"] == "derived_readback_failed":
                    self.assertEqual((result["blocked"], result["why"]["derived_readback_failed"]), (1, 1))
                else:
                    self.assertEqual((result["blocked"], result["derived_match"], result["writes"]), (0, 2, 0))

    def test_b3_stage_limit_exhaustion_blocks_and_does_not_attempt_extra_item(self):
        client = PrepFake()
        machine_parent(client)
        outcome = {"writes_planned": 0, "writes": 0, "code": "derived_allowed"}
        with patch("lifeos.interview.prep.apply", return_value=outcome) as apply_prep:
            result = stage.run(1, False, environ=ENV, client=client,
                prep_evidence=[(PARENT, ROUND, PrepEvidence(focus=("A",)))] * 2)
        self.assertEqual(apply_prep.call_count, 1)
        self.assertEqual((result["why"]["pipeline_incomplete"], result["blocked"]), (1, 1))

    def test_protected_snapshot_only_wraps_live_append(self):
        evidence = PrepEvidence(focus=("A",))
        client = PrepFake()
        _, round_id, query = machine_round(client)
        with patch("lifeos.interview.notion.protected_snapshot", side_effect=AssertionError("dry snapshot")):
            self.assertEqual(self.apply(client, query, evidence, False)["code"], "derived_allowed")
        original = notion.protected_snapshot
        events = []
        def snapshot(*args):
            events.append("snapshot")
            return original(*args)
        append = notion.append_children
        def write(*args):
            events.append("append")
            return append(*args)
        with patch("lifeos.interview.notion.protected_snapshot", side_effect=snapshot):
            with patch("lifeos.interview.notion.append_children", side_effect=write):
                self.assertEqual(self.apply(client, query, evidence)["code"], "derived_created")
        self.assertEqual(events, ["snapshot", "append", "snapshot"])
        with patch("lifeos.interview.notion.protected_snapshot", side_effect=AssertionError("replay snapshot")):
            self.assertEqual(self.apply(client, query, evidence)["code"], "derived_match")
            self.assertEqual(self.apply(client, query, PrepEvidence(focus=("Different",)))["code"], "derived_conflict")

    def test_missing_live_protected_snapshot_prevents_append(self):
        client = PrepFake()
        _, _, query = machine_round(client)
        with patch("lifeos.interview.notion.protected_snapshot", return_value=None), \
             patch("lifeos.interview.notion.append_children") as append:
            result = self.apply(client, query, PrepEvidence(focus=("A",)))
        self.assertEqual((result["code"], result["writes"]), ("readback_protected_missing", 0))
        append.assert_not_called()

    def test_replay_stays_a_noop_when_earlier_rounds_change_afterwards(self):
        client = PrepFake()
        _, previous_id, previous_query = machine_round(client, ordinal=1)
        current_query = RoundQuery(True, "2026-10-02", ordinal=3)
        create.apply(client, ENV, PARENT, current_query, True, RunContext.start(60), set())
        current = PrepEvidence(pressure_points=("Current risk",))
        self.assertEqual(self.apply(client, current_query, current)["code"], "derived_created")       # nothing to inherit yet
        old = PrepEvidence(pressure_points=("Old risk",), questions=("Old question",))
        prep.apply(client, ENV, PARENT, previous_query, old, True, RunContext.start(60))              # earlier round prepared later
        calls = len(client.calls)
        self.assertEqual(self.apply(client, current_query, current)["code"], "derived_match")
        self.assertNotIn(("GET", f"/blocks/{previous_id}/children?page_size=100"), client.calls[calls:])   # never reads its Derived
        self.assertEqual(self.apply(client, current_query, PrepEvidence(pressure_points=("Other",)))["code"], "derived_conflict")

    def test_replay_requires_exact_current_evidence_hash(self):
        variants = (
            (PrepEvidence(pressure_points=("A", "B")), PrepEvidence(pressure_points=("A",))),
            (PrepEvidence(pressure_points=("A",)), PrepEvidence(pressure_points=("A", "B"))),
            (PrepEvidence(focus=("A", "B")), PrepEvidence(focus=("B", "A"))),
            (PrepEvidence(focus=("A",)), PrepEvidence(focus=("Changed",))),
            (PrepEvidence(focus=("F",)), PrepEvidence(focus=("F",), strongest_evidence=("S",))),
            (PrepEvidence(pressure_points=("P",)), PrepEvidence(pressure_points=("Changed",))),
            (PrepEvidence(questions=("Q",)), PrepEvidence(questions=("Changed",))),
        )
        for original, replay in variants:
            with self.subTest(original=original, replay=replay):
                client = PrepFake()
                _, prior_id, prior_query = machine_round(client, ordinal=1)
                prep.apply(client, ENV, PARENT, prior_query,
                           PrepEvidence(pressure_points=("B", "Inherited")), True, RunContext.start(60))
                current_query = RoundQuery(True, "2026-10-02", ordinal=3)
                create.apply(client, ENV, PARENT, current_query, True, RunContext.start(60), set())
                self.assertEqual(self.apply(client, current_query, original)["code"], "derived_created")
                result = self.apply(client, current_query, replay)
                self.assertEqual((result["code"], result["writes"]), ("derived_conflict", 0))

    def test_marker_full_hash_commits_to_merged_payload_and_v1_is_never_rewritten(self):
        client = PrepFake()
        _, prior_id, prior_query = machine_round(client, ordinal=1)
        prior = PrepEvidence(pressure_points=("Inherited",))
        prep.apply(client, ENV, PARENT, prior_query, prior, True, RunContext.start(60))
        current_query = RoundQuery(True, "2026-10-02", ordinal=3)
        create.apply(client, ENV, PARENT, current_query, True, RunContext.start(60), set())
        current = PrepEvidence(pressure_points=("Current",))
        self.assertEqual(self.apply(client, current_query, current)["code"], "derived_created")
        _, payload = notion.derived_blocks(client, "created-3")
        current_hash, full_hash = notion.text(payload[0]).removeprefix(prep.DERIVED_PREFIX).split(";")
        self.assertEqual(current_hash, prep.digest(prep.normalize(current)))
        merged = prep.normalize(PrepEvidence(pressure_points=("Current", "Inherited")))
        self.assertEqual(full_hash, prep.digest(merged))
        before = copy.deepcopy(client.blocks["created-3"])
        client.blocks["created-3"][5] = block("v7-interview-derived:1;" + full_hash)
        result = self.apply(client, current_query, current)
        self.assertEqual((result["code"], result["writes"]), ("derived_conflict", 0))
        self.assertEqual(client.blocks["created-3"][5], block("v7-interview-derived:1;" + full_hash))

    def test_replay_never_carries_or_reads_previous_round_and_ignores_later_changes(self):
        client = PrepFake()
        _, previous_id, previous_query = machine_round(client, ordinal=1)
        current_query = RoundQuery(True, "2026-10-02", ordinal=3)
        create.apply(client, ENV, PARENT, current_query, True, RunContext.start(60), set())
        current = PrepEvidence(pressure_points=("Current",))
        self.assertEqual(self.apply(client, current_query, current)["code"], "derived_created")
        client.blocks[previous_id].append(block("changed previous derived"))
        calls = len(client.calls)
        with patch("lifeos.interview.prep._carry", side_effect=AssertionError("carry on replay")), \
             patch("lifeos.interview.notion.protected_snapshot", side_effect=AssertionError("snapshot on replay")):
            self.assertEqual(self.apply(client, current_query, current)["code"], "derived_match")
            self.assertEqual(self.apply(client, current_query, PrepEvidence(pressure_points=("Other",)))["code"], "derived_conflict")
        self.assertFalse(any(method == "GET" and path == f"/blocks/{previous_id}/children?page_size=100"
                             for method, path in client.calls[calls:]))

    def test_payload_tampering_fails_full_hash_commitment(self):
        client = PrepFake()
        _, round_id, query = machine_round(client)
        evidence = PrepEvidence(focus=("Original",))
        self.assertEqual(self.apply(client, query, evidence)["code"], "derived_created")
        heading, payload = notion.derived_blocks(client, round_id)
        item_index = next(i for i, block_value in enumerate(client.blocks[round_id])
                          if block_value.get("type") == "bulleted_list_item")
        client.blocks[round_id][item_index] = block("Tampered", "bulleted_list_item")
        result = self.apply(client, query, evidence)
        self.assertEqual((result["code"], result["writes"]), ("derived_conflict", 0))

    def test_any_error_after_the_append_is_a_readback_failure_and_stops(self):
        for error in (ValueError("x"), TypeError("x"), KeyError("x"), AttributeError("x")):
            client = PrepFake()
            _, _, query = machine_round(client)
            real = notion.explicit_child
            def fail_after_append(c, page_id, _client=client, _error=error):
                if any(method == "PATCH" for method, _ in _client.calls):
                    raise _error
                return real(c, page_id)
            with patch("lifeos.interview.notion.explicit_child", side_effect=fail_after_append):
                result = self.apply(client, query, PrepEvidence(focus=("A",)))
            self.assertEqual((result["code"], result["writes"]), ("derived_readback_failed", 1))
        before_write = PrepFake()
        _, _, query = machine_round(before_write)
        with patch("lifeos.interview.prep.normalize", side_effect=ValueError("x")):
            self.assertEqual(self.apply(before_write, query, PrepEvidence(focus=("A",)))["code"], "prep_invalid")


class DerivedScanTests(unittest.TestCase):
    class Pages:
        def __init__(self, pages):
            self.pages, self.calls = pages, 0

        def call(self, method, path, body=None):
            index = 0 if "start_cursor" not in path else int(path.split("start_cursor=")[1])
            self.calls += 1
            more = index + 1 < len(self.pages)
            return {"results": self.pages[index], "has_more": more, "next_cursor": str(index + 1) if more else None}

    def heading(self, label, kind="heading_2"):
        return {"type": kind, kind: {"rich_text": [{"plain_text": label, "text": {"content": label}}]}}

    def bullet(self, label):
        return {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"plain_text": label, "text": {"content": label}}]}}

    def test_large_notes_do_not_exhaust_the_scan_and_are_dropped_unread(self):
        notes = [[self.bullet("private note") for _ in range(100)] for _ in range(60)]    # 60 pages, beyond the 40-call scan budget
        client = self.Pages([[self.heading("Live Notes")]] + notes + [[self.heading("Derived"), self.bullet("x")]])
        heading, payload = notion.derived_blocks(client, "p")
        self.assertEqual((notion.text(heading), [notion.text(b) for b in payload]), ("Derived", ["x"]))

    def test_scan_still_fails_closed_past_its_own_budget_and_on_duplicate_headings(self):
        endless = self.Pages([[self.bullet("n")]] * (notion.DERIVED_MAX_CALLS + 5) + [[self.heading("Derived")]])
        with self.assertRaises(NotionError):
            notion.derived_blocks(endless, "p")
        twice = self.Pages([[self.heading("Derived"), self.heading("Raw Notes"), self.heading("Derived")]])
        with self.assertRaises(NotionError):
            notion.derived_blocks(twice, "p")
        none = self.Pages([[self.heading("Live Notes")]])
        with self.assertRaises(NotionError):
            notion.derived_blocks(none, "p")


if __name__ == "__main__":
    unittest.main()
