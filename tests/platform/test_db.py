import unittest

from lifeos.platform import db


class ConnectionConfigTests(unittest.TestCase):
    def test_config_error_names_settings_not_values(self):
        import os
        for field in db.FIELDS:
            os.environ.pop("LIFEOS_ACQ_" + field, None)
        with self.assertRaises(db.StoreError) as ctx:
            db._cfg()
        self.assertTrue(str(ctx.exception).startswith("STORE_CONFIG_MISSING:"))


if __name__ == "__main__":
    unittest.main()
