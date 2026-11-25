"""LIFE-973, synthetic only: a Hiring Decision set by hand in the Job Ledger gets Decision On, one history line and Freshness Status Closed; Hiring Decision itself is never changed."""
import json
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.hiring import decision as D, decision_sync as S

NOW = datetime(2026, 11, 25, 10, 0, tzinfo=ZoneInfo("America/Chicago"))
T = lambda text: [{"type": "text", "plain_text": text, "text": {"content": text}}]      # noqa: E731


def row(pid, decision="Rejected", on=None, history="", fresh="Fresh"):
    return {"id": pid, "properties": {D.DECISION: {"type": "select", "select": {"name": decision} if decision else None}, D.HISTORY: {"type": "rich_text", "rich_text": T(history)},
                                      S.DECISION_ON: {"type": "date", "date": {"start": on} if on else None}, S.FRESHNESS: {"type": "select", "select": {"name": fresh}}}}


class Ledger:
    source = "ledger-source"

    def __init__(self, *pages, drop=False):
        self.pages, self.writes, self.drop = {p["id"]: p for p in pages}, [], drop

    def query_data_source(self, source_id, body):
        assert body["filter"] == {"property": D.DECISION, "select": {"is_not_empty": True}}
        return {"results": [p for p in self.pages.values() if p["properties"][D.DECISION]["select"]], "has_more": False}

    def update_page_properties(self, pid, props):
        self.writes.append((pid, props))
        if self.drop:
            return
        for name, value in props.items():
            kind = next(iter(value))
            self.pages[pid]["properties"][name] = {"type": kind, **value}
            if kind == "rich_text":
                self.pages[pid]["properties"][name]["rich_text"] = T(value[kind][0]["text"]["content"])

    def get_page(self, pid):
        return self.pages[pid]


class DecisionSync(unittest.TestCase):
    def test_a_hand_set_decision_gets_its_date_one_history_line_and_closed_freshness(self):
        ledger = Ledger(row("p1"))
        counts = S.run(ledger, True, NOW)
        self.assertEqual((counts["decided"], counts["would_sync"], counts["synced"], counts["failed"]), (1, 1, 1, 0))
        props = ledger.pages["p1"]["properties"]
        self.assertEqual((props[D.DECISION]["select"]["name"], props[S.DECISION_ON]["date"]["start"], props[S.FRESHNESS]["select"]["name"]), ("Rejected", "2026-11-25", "Closed"))
        [entry] = D.entries(D._text(props[D.HISTORY], "rich_text"))
        self.assertEqual((entry["outcome"], entry["action"]), ("Rejected", "hand-set"))
        self.assertNotIn(D.DECISION, ledger.writes[0][1])                                                  # the decision itself is never written

    def test_a_row_that_already_has_all_three_is_left_alone_and_a_replay_writes_nothing(self):
        done = json.dumps({"op": "x1", "action": "CONFIRM_REJECTED", "at": "2026-11-20T09:00:00-06:00", "reason": "r", "outcome": "Passed"})
        ledger = Ledger(row("p1", "Passed", on="2026-11-20", history=done, fresh="Closed"), row("p2"))
        S.run(ledger, True, NOW)
        self.assertEqual([w[0] for w in ledger.writes], ["p2"])
        S.run(ledger, True, NOW)
        self.assertEqual(len(ledger.writes), 1)

    def test_only_what_is_missing_is_written(self):
        ledger = Ledger(row("p1", on="2026-11-01", fresh="Closed"))
        S.run(ledger, True, NOW)
        self.assertEqual(list(ledger.writes[0][1]), [D.HISTORY])
        ledger = Ledger(row("p2", on="2026-11-01", history=json.dumps({"op": "o", "action": "A", "at": "t", "reason": "r", "outcome": "Rejected"})))
        S.run(ledger, True, NOW)
        self.assertEqual(list(ledger.writes[0][1]), [S.FRESHNESS])

    def test_a_dry_run_counts_and_writes_nothing_and_an_open_row_is_ignored(self):
        ledger = Ledger(row("p1"), row("p2", decision=None))
        counts = S.run(ledger, False, NOW)
        self.assertEqual((counts["decided"], counts["would_sync"], counts["synced"], ledger.writes), (1, 1, 0, []))

    def test_a_write_that_does_not_read_back_is_counted_failed(self):
        counts = S.run(Ledger(row("p1"), drop=True), True, NOW)
        self.assertEqual((counts["synced"], counts["failed"]), (0, 1))

    def test_an_unreadable_history_is_refused_not_overwritten(self):
        ledger = Ledger(row("p1", history="not json"))
        counts = S.run(ledger, True, NOW)
        self.assertEqual((counts["failed"], ledger.writes), (1, []))


if __name__ == "__main__":
    unittest.main()
