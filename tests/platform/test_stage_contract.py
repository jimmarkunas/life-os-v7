"""LIFE-589: the stage contract type (lifeos/platform/stage_contract.py)."""
import unittest

from lifeos.platform import codes, stage_contract as sc


def contract(run, **over):
    fields = dict(name="demo", run=run, inputs=("a page",), outputs=("status", "n"), dry_run="reads and counts", write="writes one row")
    return sc.Contract(**{**fields, **over})


class ContractTests(unittest.TestCase):
    def test_calling_a_contract_runs_the_stage_with_its_arguments_and_returns_its_counts(self):
        seen = []

        def run(limit, live, fetcher=None):
            seen.append((limit, live, fetcher))
            return {"status": "OK", "n": 3, "extra": 1}

        counts = contract(run)(5, True, fetcher="f")
        self.assertEqual(counts, {"status": "OK", "n": 3, "extra": 1})
        self.assertEqual(seen, [(5, True, "f")])

    def test_a_stage_that_leaves_out_a_declared_output_or_returns_a_non_dict_fails_with_the_fixed_code(self):
        for bad in ({"status": "OK"}, None, ["status", "n"], "OK"):
            with self.assertRaises(sc.ContractError) as caught:
                contract(lambda limit, live, bad=bad: bad)(1, False)
            self.assertEqual(str(caught.exception), "STAGE_CONTRACT_OUTPUT_MISSING")

    def test_an_empty_field_is_refused_when_the_contract_is_made(self):
        for field, value in (("name", ""), ("inputs", ()), ("outputs", ()), ("dry_run", "  "), ("write", "")):
            with self.assertRaises(sc.ContractError) as caught:
                contract(lambda limit, live: {}, **{field: value})
            self.assertEqual(str(caught.exception), "STAGE_CONTRACT_INCOMPLETE", field)

    def test_describe_lists_the_declaration(self):
        d = contract(lambda limit, live: {}).describe()
        self.assertEqual((d["name"], d["inputs"], d["outputs"], d["dry_run"], d["write"]), ("demo", ["a page"], ["status", "n"], "reads and counts", "writes one row"))

    def test_the_two_codes_are_registered_and_the_module_imports_nothing_from_lifeos(self):
        self.assertIn("STAGE_CONTRACT_OUTPUT_MISSING", codes.CODES)
        self.assertIn("STAGE_CONTRACT_INCOMPLETE", codes.CODES)
        import ast
        import pathlib
        tree = ast.parse(pathlib.Path(sc.__file__).read_text())
        self.assertEqual([n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) and "lifeos" in (getattr(n, "module", "") or "")], [])


if __name__ == "__main__":
    unittest.main()
