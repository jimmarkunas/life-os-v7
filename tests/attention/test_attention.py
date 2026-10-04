import unittest
from datetime import date, datetime, timezone

from lifeos.attention import policy, reconcile, stage
from lifeos.platform.gmail import GmailError

TODAY = date(2026, 10, 5)                     # a Monday: the week ends Sunday 2026-10-11
WEEK, PRIOR = "2026-10-11", "2026-10-04"
NOW = datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc)


class Policy(unittest.TestCase):
    def verdict(self, sender, subject):
        return policy.decide(sender, subject)

    def test_account_anomaly_with_no_other_owner_is_admitted(self):
        self.assertEqual(self.verdict("no-reply@venmo.com", "A debit card ending 4328 was added to your account"), ("ADMIT", "Account"))

    def test_security_anomaly_is_admitted(self):
        self.assertEqual(self.verdict("alerts@bank.example", "Security alert: new sign-in from an unrecognized device"), ("ADMIT", "Security"))

    def test_deadline_and_admin_are_admitted(self):
        self.assertEqual(self.verdict("a@x.example", "Final notice: respond by Friday"), ("ADMIT", "Deadline"))
        self.assertEqual(self.verdict("a@x.example", "Vehicle licence renewal notice"), ("ADMIT", "Admin"))

    def test_ordinary_jira_work_is_not_admitted(self):
        self.assertEqual(self.verdict("jira@acme.atlassian.net", "Action required: review LIFE-123"), ("OWNED", "jira"))
        self.assertEqual(self.verdict("boss@x.example", "Deadline for LIFE-45 moved"), ("OWNED", "jira"))

    def test_calendar_hiring_bills_amazon_and_mail_belong_elsewhere(self):
        self.assertEqual(self.verdict("a@x.example", "Invitation: Peter sync @ Tue 10am")[0], "OWNED")
        self.assertEqual(self.verdict("r@agency.example", "Interview request - action required"), ("OWNED", "hiring"))
        self.assertEqual(self.verdict("b@x.example", "Payment due: action required")[0], "OWNED")
        self.assertEqual(self.verdict("ship-confirm@amazon.com", "Security alert on your order"), ("OWNED", "amazon"))
        self.assertEqual(self.verdict("noreply@anytimemailbox.com", "You have new mail: action required"), ("OWNED", "mail_alerts"))

    def test_generic_fyi_is_not_admitted(self):
        self.assertEqual(self.verdict("news@x.example", "Our monthly update"), ("SKIP", "no_risk_signal"))


def row(rid, item, category="Account", done=False, active=True, week=WEEK, medium="Gmail:1"):
    return {"id": rid, "item": item, "category": category, "done": done, "active": active, "medium": medium, "url": "u", "week": week}


def cand(item, category="Account", medium="Gmail:1"):
    return {"category": category, "item": item, "url": "u", "medium": medium}


class Reconcile(unittest.TestCase):
    def test_week_ending_is_the_sunday(self):
        self.assertEqual(str(reconcile.week_ending(date(2026, 10, 4))), "2026-10-04")
        self.assertEqual(str(reconcile.week_ending(date(2026, 10, 5))), "2026-10-11")
        self.assertEqual(str(reconcile.week_ending(date(2026, 10, 10))), "2026-10-11")

    def test_new_item_is_created_once_and_replay_or_natural_key_equivalent_is_not(self):
        p = reconcile.plan([cand("Card added"), cand("card  ADDED!", medium="Gmail:2")], [], TODAY, {})
        self.assertEqual(len(p["create"]), 1)
        again = reconcile.plan([cand("Card added")], [row("r1", "Card added")], TODAY, {})
        self.assertEqual((again["create"], again["reused"]), ([], 1))
        equivalent = reconcile.plan([cand("card added")], [row("r1", "Card Added")], TODAY, {})
        self.assertEqual(equivalent["create"], [])

    def test_ambiguous_duplicates_write_nothing(self):
        p = reconcile.plan([cand("Card added")], [row("r1", "Card added"), row("r2", "Card added")], TODAY, {})
        self.assertEqual((p["create"], p["ambiguous"]), ([], 1))

    def test_done_is_never_unticked_recreated_or_reactivated(self):
        p = reconcile.plan([cand("Card added")], [row("r1", "Card added", done=True, active=False)], TODAY, {"Gmail:1": "present"})
        self.assertEqual((p["create"], p["reactivate"], p["skipped_done"]), ([], [], 1))

    def test_resolved_source_deactivates_but_ambiguous_evidence_does_not(self):
        rows = [row("r1", "Card added", medium="Gmail:1"), row("r2", "Other thing", medium="Gmail:2")]
        p = reconcile.plan([], rows, TODAY, {"Gmail:1": "absent", "Gmail:2": "unknown"})
        self.assertEqual(p["deactivate"], ["r1"])
        self.assertEqual(reconcile.plan([], rows, TODAY, {})["deactivate"], [])

    def test_monday_carries_unresolved_exactly_once_and_never_done(self):
        rows = [row("a", "Open item", week=PRIOR), row("b", "Handled item", week=PRIOR, done=True), row("c", "Inactive", week=PRIOR, active=False),
                row("d", "Mail", category="Physical Mail", week=PRIOR)]
        p = reconcile.plan([], rows, TODAY, {})
        self.assertEqual([c["item"] for c in p["create"]], ["Open item"])
        self.assertEqual(p["create"][0]["week"], WEEK)
        after = reconcile.plan([], rows + [row("n", "Open item", week=WEEK)], TODAY, {})
        self.assertEqual(after["create"], [])

    def test_older_weeks_do_not_carry(self):
        self.assertEqual(reconcile.plan([], [row("a", "Old", week="2026-09-13")], TODAY, {})["create"], [])


class FakeNotion:
    def __init__(self, pages=(), schema_ok=True):
        self.pages = {p["id"]: p for p in pages}
        self.schema_ok, self.paths, self.n = schema_ok, [], 0

    def call(self, method, path, body=None):
        self.paths.append((method, path.split("/")[1]))
        types = dict(stage.SCHEMA) if self.schema_ok else {**stage.SCHEMA, "Done": "text"}
        return {"properties": {k: ({"type": v, "select": {"options": [{"name": n} for n in ("Security", "Account", "Deadline", "Admin")]}} if v == "select" else {"type": v}) for k, v in types.items()}}

    def query_data_source(self, source, body):
        self.paths.append(("POST", "data_sources"))
        week = body["filter"]["date"]["equals"]
        return {"results": [p for p in self.pages.values() if p["properties"]["Week Ending"]["date"]["start"] == week], "has_more": False}

    def call_once(self, method, path, body=None):
        self.paths.append((method, path.split("/")[1]))
        self.n += 1
        props = body["properties"]
        page = {"id": f"p{self.n}", "properties": {
            "Item": {"type": "title", "title": props["Item"]["title"]}, "Category": {"type": "select", "select": props["Category"]["select"]},
            "Done": {"type": "checkbox", "checkbox": False}, "Active": {"type": "checkbox", "checkbox": True},
            "Medium": {"type": "rich_text", "rich_text": props["Medium"]["rich_text"]}, "Source URL": {"type": "url", "url": (props.get("Source URL") or {}).get("url")},
            "Week Ending": {"type": "date", "date": props["Week Ending"]["date"]}}}
        for k in ("Item", "Medium"):
            for part in page["properties"][k][page["properties"][k]["type"]]:
                part["plain_text"] = part["text"]["content"]
        self.pages[page["id"]] = page
        return page

    def update_page_properties(self, page_id, properties):
        self.paths.append(("PATCH", "pages"))
        for k, v in properties.items():
            self.pages[page_id]["properties"][k]["checkbox"] = v["checkbox"]


def page(pid, item, category="Account", done=False, active=True, week=WEEK, medium="Gmail:1"):
    t = lambda kind, text: {"type": kind, kind: [{"plain_text": text, "text": {"content": text}}]}
    return {"id": pid, "properties": {"Item": t("title", item), "Category": {"type": "select", "select": {"name": category}}, "Done": {"type": "checkbox", "checkbox": done},
                                      "Active": {"type": "checkbox", "checkbox": active}, "Medium": t("rich_text", medium), "Source URL": {"type": "url", "url": "u"},
                                      "Week Ending": {"type": "date", "date": {"start": week}}}}


class FakeGmail:
    def __init__(self, messages, fail=False):
        self.messages, self.fail = messages, fail

    def label_id(self, name, create=False):
        return "L1"

    def list_ids_complete(self, query, limit):
        if self.fail:
            raise GmailError("GMAIL_LISTING_INCOMPLETE")
        return list(self.messages)

    def message_record(self, mid):
        sender, subject = self.messages[mid]
        return {"sender": sender, "subject": subject, "label_ids": ["L1"]}

    def message_labels(self, mid):
        return ["INBOX"]                                           # label gone


class FakeOutlook:
    def folder_id(self, name, create=False):
        return "F1"

    def messages(self, folder, since, limit):
        return [{"id": "o1", "subject": "Security alert: password was changed", "from": {"emailAddress": {"address": "a@b.example"}}}]


class Stage(unittest.TestCase):
    def go(self, notion, gmail, live=True, outlook=()):
        return stage.run(200, live, environ={}, gmail=gmail, notion=notion, outlook_clients=list(outlook), now=NOW)

    def test_admits_from_gmail_and_outlook_writes_once_and_reads_back(self):
        notion = FakeNotion()
        gm = FakeGmail({"g1": ("no-reply@venmo.com", "Debit card ending 4328 was added"), "g2": ("news@x.example", "Monthly update"),
                        "g3": ("r@a.example", "Interview request - action required")})
        out = self.go(notion, gm, outlook=[("personal", FakeOutlook(), False)])
        self.assertEqual((out["created"], out["admitted"], out["no_risk_signal"], out["owned_elsewhere"], out["verified"]), (2, 2, 1, 1, True))
        again = self.go(notion, gm, outlook=[("personal", FakeOutlook(), False)])
        self.assertEqual(again["created"], 0)
        self.assertEqual(len(notion.pages), 2)

    def test_only_the_attention_source_is_touched(self):
        notion = FakeNotion()
        self.go(notion, FakeGmail({"g1": ("a@b.example", "Security alert")}))
        self.assertTrue({path for _, path in notion.paths} <= {"data_sources", "pages"})

    def test_dry_run_writes_nothing(self):
        notion = FakeNotion()
        out = self.go(notion, FakeGmail({"g1": ("a@b.example", "Security alert")}), live=False)
        self.assertEqual((out["created"], len(notion.pages)), (1, 0))

    def test_failed_source_is_degraded_but_prior_state_and_other_source_are_kept(self):
        notion = FakeNotion([page("old", "Card added", medium="Gmail:g9")])
        with self.assertRaises(stage.AttentionError) as ctx:
            self.go(notion, FakeGmail({}, fail=True), outlook=[("personal", FakeOutlook(), False)])
        self.assertIn("ATTENTION_DEGRADED", str(ctx.exception))
        self.assertTrue(notion.pages["old"]["properties"]["Active"]["checkbox"])        # not deactivated on unknown evidence
        self.assertEqual(len(notion.pages), 2)                                          # the Outlook item was still saved

    def test_schema_mismatch_fails_closed(self):
        with self.assertRaises(stage.AttentionError):
            self.go(FakeNotion(schema_ok=False), FakeGmail({}))

    def test_done_is_never_changed_and_label_removal_deactivates_only_the_open_item(self):
        notion = FakeNotion([page("open", "Card added", medium="Gmail:g9"), page("handled", "Other", done=True, medium="Gmail:g8")])
        out = self.go(notion, FakeGmail({}))
        self.assertEqual(out["deactivated"], 1)
        self.assertFalse(notion.pages["open"]["properties"]["Active"]["checkbox"])
        self.assertTrue(notion.pages["handled"]["properties"]["Done"]["checkbox"])
        self.assertTrue(notion.pages["handled"]["properties"]["Active"]["checkbox"])

    def test_monday_rollover_carries_unresolved_once_and_keeps_history(self):
        notion = FakeNotion([page("a", "Open item", week=PRIOR, medium="Gmail:g1"), page("b", "Done item", week=PRIOR, done=True, medium="Gmail:g2")])
        gm = FakeGmail({"g1": ("x@y.example", "Open item: security alert")})
        self.go(notion, gm)
        self.go(notion, gm)
        weeks = sorted(p["properties"]["Week Ending"]["date"]["start"] for p in notion.pages.values())
        self.assertEqual(weeks.count(WEEK), 2)                                         # the carried item and the freshly admitted one, each exactly once
        self.assertEqual(weeks.count(PRIOR), 2)                                        # prior-week rows preserved unchanged


if __name__ == "__main__":
    unittest.main()
