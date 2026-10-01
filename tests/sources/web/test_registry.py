import unittest

from lifeos.sources.web import registry


class RegistryTests(unittest.TestCase):
    def test_the_v2_universe_is_harvested_whole(self):
        rows = registry.load()
        self.assertEqual(len(rows), 43)
        self.assertEqual([sum(r["tier"] == t for r in rows) for t in registry.TIERS], [30, 10, 3])
        self.assertEqual(len({r["id"] for r in rows}), 43)

    def test_ready_boards_are_the_ats_families_v7_already_reads(self):
        ready = registry.enabled(status="ready")
        self.assertEqual({r["kind"] for r in ready}, {"greenhouse", "ashby", "smartrecruiters"})
        self.assertEqual(len(ready), 26)                      # 20 greenhouse + 4 ashby + ServiceNow + Apex Systems
        self.assertTrue(all(r["slug"] for r in ready))

    def test_aggregators_are_discovery_only(self):
        helpers = registry.enabled(tier="helper")
        self.assertEqual({r["company"] for r in helpers}, {"LinkedIn Jobs", "Built In", "Dice"})
        self.assertTrue(all(r["status"] == "discovery" for r in helpers))

    def test_coverage_counts(self):
        cov = registry.coverage()
        self.assertEqual(cov[("employer", "ready")], 25)       # 20 Greenhouse + 4 Ashby + ServiceNow
        self.assertEqual(cov[("employer", "port")], 3)         # Adobe + Postman (Workday), GitHub (Jibe)
        self.assertEqual(cov[("employer", "bespoke")], 2)      # Shopify, Atlassian (company career pages)
        self.assertEqual((cov[("staffing", "ready")], cov[("staffing", "bespoke")]), (1, 9))   # Apex (SmartRecruiters) is the only ready agency
        self.assertEqual(cov[("helper", "discovery")], 3)

    def test_bad_rows_are_rejected(self):
        import json, tempfile, pathlib
        bad = {"sources": [{"id": "x", "company": "X", "tier": "employer", "kind": "greenhouse", "status": "ready", "enabled": True}]}
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "r.json"
            p.write_text(json.dumps(bad))
            with self.assertRaises(registry.RegistryError):
                registry.load(p)


if __name__ == "__main__":
    unittest.main()
