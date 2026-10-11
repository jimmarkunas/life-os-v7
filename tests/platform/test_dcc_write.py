import unittest

from lifeos.platform import dcc_write, router
from lifeos.platform.router import DEGRADED, NO_ACTION, PASS

T = "\t"
DCC_OLD = ["**Current focus:** old focus", "**Attention needed:** old (3:07 PM CT)", "**Evidence freshness:** snapshots through 3:13 PM CT", "**Next useful action:** open the report"]
DCC_NEW = ["**Current focus:** new focus", "**Attention needed:** old (3:07 PM CT)", "**Evidence freshness:** snapshots through 4:13 PM CT", "**Next useful action:** open the report"]
JIRA = ["#### Overdue / Blocked — 6", "- [**Epic · JFM-9 — Career**](url)", T + "- [child](url) · due Sep 22 · To Do"]


def page(cal="4:14", jira="4:14", hiring="4:22", exc="4:14", dcc=DCC_OLD, delta="- **Overall:** ok", jira_body=JIRA, mail=True, dcc_copies=1, close=True, amazon="⁦Kid⁩ · \\$21.80"):
    """The Daily Report as the Notion connector serializes it: tabs for nesting, but table rows at column 0."""
    box = lambda n: "<callout icon=\"/icons/target_gray.svg\" color=\"gray_bg\">\n" + "\n".join(T + line for line in ["### ChatGPT · Daily Command Center"] + dcc) + "\n</callout>"
    out = ["Here is the result of \"fetch\" as of 2026-10-11T21:22:29.913Z:", "<page url=\"url\">", "<properties>", "{\"title\":\"Daily LIFE OS Report (Hourly)\"}", "</properties>", "<content>",
           "# September 15, 2026", "Last refreshed 1:27 PM CT · RECOVERY REQUIRED", "---", "<database url=\"url\" inline=\"true\" data-source-url=\"collection://ID\"></database>", "---",
           "<columns>", T + "<column ratio=\"50\">",
           T * 2 + "<callout icon=\"/icons/calendar_red.svg\" color=\"pink_bg\">", T * 3 + f"### Calendar (Updated {cal} PM CT)", T * 3 + "### Today", T * 3 + "- event — [text](url)", T * 2 + "</callout>",
           T * 2 + "<callout icon=\"/icons/briefcase_gray.svg\" color=\"gray_bg\">", T * 3 + "### Daily Jobs List", T * 3 + "<columns>", T * 4 + "<column>", T * 5 + "- [job](url)", T * 4 + "</column>",
           T * 4 + "<column>", T * 5 + "- [job](url)", T * 4 + "</column>", T * 3 + "</columns>", T * 2 + "</callout>", T * 2 + "---",
           T * 2 + "<callout>", T * 3 + f"Updated {exc} PM CT · 23 active exceptions", T * 3 + "<database url=\"url\" inline=\"true\" data-source-url=\"collection://ID\"></database>", T * 2 + "</callout>"]
    if mail:
        out += [T * 2 + "<callout icon=\"/icons/mail_gray.svg\">", T * 3 + "### Mail Alerts", T * 3 + "Updated 4:20 PM CT · 13 waiting", T * 2 + "</callout>"]
    out += [T * 2 + "<callout icon=\"/icons/package_gray.svg\">", T * 3 + "### Amazon Orders", T * 3 + "Updated 4:17 PM CT · 161 in transit", T * 3 + f"- {amazon}", T * 2 + "</callout>",
            T * 2 + "<callout icon=\"/icons/book-closed_gray.svg\">", T * 3 + "-", T * 2 + "</callout>", T + "</column>", T + "<column ratio=\"50\">",
            T * 2 + "### 📈 Hiring Pipeline", T * 2 + f"Updated {hiring} PM CT · 5 active", T * 2 + "<table fit-page-width=\"true\" header-row=\"true\">", "<tr>", "<td>role</td>", "<td>[text](url)</td>", "</tr>", T * 2 + "</table>",
            T * 2 + "<callout icon=\"/icons/checklist_gray.svg\">", T * 3 + "### Clients & Projects", T * 3 + "<empty-block/>", T * 2 + "</callout>",
            T * 2 + "<callout icon=\"/icons/target_gray.svg\" color=\"gray_bg\">", T * 3 + f"#### JIRA Execution (Updated {jira} PM CT)"]
    out += [T * 3 + line for line in jira_body]
    out += [T * 3 + "<details>", T * 3 + "<summary>Done — 2</summary>", T * 4 + "- [done](url)", T * 3 + "</details>", T * 2 + "</callout>", T + "</column>", "</columns>", "---", "## 9 AM Delta", delta, "---",
            "<details color=\"gray\">", "<summary>Run history</summary>", T + "## Sep 13 · 12 AM Delta", T + "- **Overall:** ok", "</details>"]
    out += [box(i) for i in range(dcc_copies)]
    out += ["</content>", "</page>"] if close else []
    return "\n".join(out)


class Connector:
    """A stand-in for the Notion connector: the page as markdown, and update_content as an exact, unique search-and-replace. Hooks stand in for other writers and connector faults."""
    def __init__(self, md, after=None, fail=False, bug=None, truncate=False):
        self.md, self.after, self.fail, self.bug, self.truncate = md, after, fail, bug, truncate
        self.reads, self.writes = 0, []

    def fetch(self):
        self.reads += 1
        return self.md[:self.md.index("</content>")] if self.truncate and self.writes else self.md

    def update(self, old, new):
        self.writes.append((old, new))
        if self.fail:
            raise RuntimeError("400 detail that must not travel")
        assert self.md.count(old) == 1, "the connector refuses a non-unique match"
        self.md = self.md.replace(old, new, 1)
        if self.bug:
            self.md = self.bug(self.md)
        if self.after:
            self.md = self.after(self.md)

    def go(self, intended=DCC_NEW):
        return dcc_write.run(self.fetch, self.update, "\n".join(intended))


def outside_dcc(md):
    start = md.index("<callout icon=\"/icons/target_gray.svg\" color=\"gray_bg\">\n" + T + "### ChatGPT")
    return md[:start]


class DccWriteTests(unittest.TestCase):
    def test_1_a_dcc_only_update_passes_and_changes_nothing_else(self):
        c = Connector(page())
        before = c.md
        result = c.go()
        self.assertEqual((result["outcome"], result["code"], result["written"], result["external"]), (PASS, "DCC_UPDATED", True, []))
        self.assertEqual(len(c.writes), 1)
        self.assertEqual(outside_dcc(c.md), outside_dcc(before))                     # every byte above the DCC callout, nested columns, table rows and databases included
        self.assertTrue(c.md.endswith("</callout>\n</content>\n</page>"))
        self.assertIn("snapshots through 4:13 PM CT", c.md)

    def test_the_write_replaces_only_the_lines_that_differ_and_stays_inside_the_callout(self):
        c = Connector(page())
        c.go()
        old, new = c.writes[0]
        self.assertEqual(old.count("\n"), 2)                                          # focus ... freshness: the unchanged attention line between them rides along, nothing else
        self.assertTrue(all(line.startswith(T) for line in old.split("\n")))
        self.assertNotIn("Next useful action", old)
        self.assertNotIn("JIRA", old + new)

    def test_a_changed_line_that_also_appears_elsewhere_is_widened_inside_the_callout(self):
        md = page().replace(T + "- **Overall:** ok\n</details>", T + "**Next useful action:** open the report\n</details>")      # the same line, same indent, outside the DCC callout
        self.assertEqual(md.count(T + "**Next useful action:** open the report"), 2)
        step = dcc_write.plan(md, "\n".join(DCC_OLD[:3] + ["**Next useful action:** changed"]))
        self.assertEqual(md.count(step["old"]), 1)
        self.assertIn("Evidence freshness", step["old"])                              # widened upward, still inside the callout
        self.assertIn(step["old"], md[md.index("### ChatGPT"):])

    def test_2_identical_content_writes_nothing(self):
        c = Connector(page())
        result = c.go(DCC_OLD)
        self.assertEqual((result["outcome"], result["code"], result["written"]), (NO_ACTION, "DCC_UNCHANGED", False))
        self.assertEqual((c.writes, c.reads), ([], 1))

    def test_serialization_differences_are_not_content(self):
        c = Connector(page(dcc=["**Paid:** \\$21.80  ", "**Who:** ⁦Kid⁩"]))
        self.assertEqual(c.go(["**Paid:** $21.80", "**Who:** Kid"])["outcome"], NO_ACTION)

    def test_3_a_missing_dcc_is_degraded_and_never_created(self):
        c = Connector(page(dcc_copies=0))
        result = c.go()
        self.assertEqual((result["outcome"], result["code"], result["written"]), (DEGRADED, "ROUTER_TARGET_MISSING", False))
        self.assertEqual(c.writes, [])

    def test_4_a_duplicate_dcc_is_degraded_and_nothing_is_guessed(self):
        c = Connector(page(dcc_copies=2))
        result = c.go()
        self.assertEqual((result["outcome"], result["code"]), (DEGRADED, "ROUTER_TARGET_AMBIGUOUS"))
        self.assertEqual(c.writes, [])

    def test_5_a_write_failure_is_degraded_without_a_retry_or_detail(self):
        c = Connector(page(), fail=True)
        result = c.go()
        self.assertEqual((result["outcome"], result["code"]), (DEGRADED, "DCC_WRITE_BLOCKED"))
        self.assertEqual((len(c.writes), c.reads), (1, 1))
        self.assertNotIn("detail", str(result))

    def test_6_an_incomplete_read_back_is_degraded_even_though_the_write_happened(self):
        c = Connector(page(), truncate=True)
        result = c.go()
        self.assertEqual((result["outcome"], result["code"], result["written"]), (DEGRADED, "READBACK_INCOMPLETE", True))
        self.assertEqual(len(c.writes), 1)
        self.assertEqual(dcc_write.run(lambda: page(close=False), lambda *a: None, "x")["code"], "READBACK_INCOMPLETE")

    def test_7_a_changed_jira_region_is_degraded(self):
        bug = lambda md: md.replace("due Sep 22", "due Sep 23")                        # content moved, stamp did not: not the owner refreshing it
        c = Connector(page(), bug=bug)
        self.assertEqual(c.go()["code"], "PROTECTED_REGION_VERIFICATION_FAILED")
        gone = Connector(page(), bug=lambda md: md.replace("Done — 2", "Done — 3"))
        self.assertEqual(gone.go()["outcome"], DEGRADED)

    def test_8_any_other_region_changing_is_degraded(self):
        for name, bug in (("unrelated text", lambda md: md.replace("- **Overall:** ok\n---\n<details", "- **Overall:** bad\n---\n<details")),
                          ("headless callout", lambda md: md.replace(T * 3 + "-\n", T * 3 + "- edited\n")),
                          ("a region deleted", lambda md: md.replace(T * 2 + "<callout icon=\"/icons/mail_gray.svg\">\n" + T * 3 + "### Mail Alerts\n" + T * 3 + "Updated 4:20 PM CT · 13 waiting\n" + T * 2 + "</callout>\n", "")),
                          ("the page title block", lambda md: md.replace("# September 15, 2026", "# October 11, 2026"))):
            with self.subTest(name):
                result = Connector(page(), bug=bug).go()
                self.assertEqual((result["outcome"], result["code"]), (DEGRADED, "PROTECTED_REGION_VERIFICATION_FAILED"))

    def test_9_an_owner_refreshing_its_region_during_the_write_is_noted_not_blamed(self):
        refresh = lambda md: md.replace("Calendar (Updated 4:14 PM CT)", "Calendar (Updated 4:31 PM CT)").replace("- event —", "- new event —")
        result = Connector(page(), after=refresh).go()
        self.assertEqual((result["outcome"], result["code"], result["external"]), (PASS, "DCC_UPDATED", ["Calendar"]))
        hiring = lambda md: md.replace("Updated 4:22 PM CT · 5 active", "Updated 4:40 PM CT · 6 active").replace("<td>role</td>", "<td>role</td>\n<td>new</td>")
        self.assertEqual(Connector(page(), after=hiring).go()["external"], ["📈 Hiring Pipeline"])
        attention = lambda md: md.replace("Updated 4:14 PM CT · 23 active", "Updated 4:44 PM CT · 24 active")
        self.assertEqual(Connector(page(), after=attention).go()["external"], ["(unmarked callout 1)"])

    def test_a_change_with_no_owner_stamp_move_is_never_attributed_to_someone_else(self):
        silent = lambda md: md.replace("- event —", "- new event —")                  # Calendar changed but its stamp did not
        self.assertEqual(Connector(page(), after=silent).go()["code"], "PROTECTED_REGION_VERIFICATION_FAILED")
        unowned = lambda md: md.replace("Updated 4:14 PM CT · 23 active exceptions", "Updated 4:14 PM CT · 23 active exceptions").replace(T * 3 + "-\n", T * 3 + "- x\n")
        self.assertEqual(Connector(page(), after=unowned).go()["outcome"], DEGRADED)

    def test_a_stamp_move_on_an_unowned_region_is_not_an_owner_refresh(self):
        md = page().replace("### Daily Jobs List", "### Daily Jobs List (Updated 4:01 PM CT)")
        later = lambda m: m.replace("Updated 4:01 PM CT", "Updated 4:09 PM CT")
        self.assertEqual(Connector(md, after=later).go()["code"], "PROTECTED_REGION_VERIFICATION_FAILED")

    def test_serialization_noise_in_a_protected_region_is_not_a_mutation(self):
        renderer = lambda md: md.replace("⁦Kid⁩", "Kid").replace("\\$21.80", "$21.80")
        result = Connector(page(), after=renderer).go()
        self.assertEqual((result["outcome"], result["external"]), (PASS, []))

    def test_10_nesting_formatting_and_the_unindented_table_are_read_by_tags_not_indentation(self):
        lines = dcc_write._content(page())
        names = [name for _, _, name, _ in dcc_write._regions(lines)]
        self.assertEqual(names, ["Calendar", "Daily Jobs List", "(unmarked callout 1)", "Mail Alerts", "Amazon Orders", "(unmarked callout 2)", "📈 Hiring Pipeline", "Clients & Projects",
                                 "JIRA Execution", router.DCC_REGION])
        owners = {name: owner for _, _, name, owner in dcc_write._regions(lines)}
        self.assertEqual((owners["JIRA Execution"], owners["📈 Hiring Pipeline"], owners["(unmarked callout 1)"], owners["(unmarked callout 2)"], owners["Daily Jobs List"]),
                         ("v7-jira", "v7-hiring", "v7-attention", None, None))
        snap = dcc_write.snapshot(page())
        self.assertNotIn(router.DCC_REGION, snap["regions"])
        self.assertIn("<td>role</td>", snap["regions"]["📈 Hiring Pipeline"][1])
        self.assertIn("<columns>", snap["skeleton"])

    def test_the_dcc_text_may_be_longer_shorter_or_start_empty(self):
        for want in (DCC_OLD + ["**One more:** line"], DCC_OLD[:2], ["**Only:** this"]):
            c = Connector(page())
            self.assertEqual(c.go(want)["outcome"], PASS)
        empty = Connector(page(dcc=[]))
        self.assertEqual(empty.go(["**Fresh:** text"])["outcome"], PASS)
        self.assertEqual(empty.writes[0][0], T + "### ChatGPT · Daily Command Center")

    def test_results_carry_fixed_codes_and_region_headings_only(self):
        result = Connector(page(), fail=True).go()
        self.assertEqual(set(result), {"outcome", "code", "external", "written"})
        self.assertTrue(result["code"].isupper() or "_" in result["code"])

    def test_the_helper_imports_nothing_outside_re_and_the_router_contract(self):
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(dcc_write))
        names = sorted({a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)})
        self.assertEqual(names, ["lifeos.platform", "lifeos.platform.router", "re"])


if __name__ == "__main__":
    unittest.main()
