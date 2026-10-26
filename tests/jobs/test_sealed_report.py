import base64
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from lifeos.jobs import sealed_report as sr


@unittest.skipUnless(shutil.which("openssl"), "openssl is needed")
class Sealed(unittest.TestCase):
    def keypair(self, folder):
        private = os.path.join(folder, "private.pem")
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", private], check=True, capture_output=True)
        public = subprocess.run(["openssl", "pkey", "-in", private, "-pubout"], check=True, capture_output=True).stdout.decode()
        return private, public

    def open(self, block, private):
        blob = base64.b64decode("".join(block.splitlines()[1:-1]))
        size = int.from_bytes(blob[:2], "big")
        key = subprocess.run(["openssl", "pkeyutl", "-decrypt", "-inkey", private, "-pkeyopt", "rsa_padding_mode:oaep", "-pkeyopt", "rsa_oaep_md:sha256"], input=blob[2:2 + size], check=True, capture_output=True).stdout
        return subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "100000", "-pass", "env:SEAL_KEY"], input=blob[2 + size:], check=True, capture_output=True,
                              env={**os.environ, "SEAL_KEY": key.decode()}).stdout.decode()

    def test_what_is_sealed_opens_with_the_private_key_and_shows_no_plaintext_in_the_block(self):
        with tempfile.TemporaryDirectory() as folder:
            private, public = self.keypair(folder)
            text = "ExampleCo\tSenior Program Manager\thttps://example.com/jobs/1"
            block = sr.seal(text, public_key=public)
            self.assertTrue(block.startswith(sr.MARK_BEGIN) and block.endswith(sr.MARK_END))
            self.assertNotIn("ExampleCo", block)
            self.assertTrue(all(re.fullmatch(r"[A-Za-z0-9+/=]*", line) for line in block.splitlines()[1:-1]))
            self.assertEqual(self.open(block, private), text)
            self.assertNotEqual(sr.seal(text, public_key=public), block)                       # a fresh key and salt every time

    def test_a_bad_public_key_is_a_fixed_word_not_openssls_text(self):
        with self.assertRaises(RuntimeError) as caught:
            sr.seal("x", public_key="not a key")
        self.assertEqual(str(caught.exception), "sealed_report_failed")


if __name__ == "__main__":
    unittest.main()
