"""LIFE-121: a private report that is safe to leave in a PUBLIC workflow log. The text (company, title, link) is encrypted on the runner with a random AES-256 key, that key is sealed to
Jim's session public key (RSA-OAEP), and only the ciphertext is printed. Without the private key (never in this repository) the log shows nothing readable. Counts stay in the clear.

The key below is a PUBLIC key; publishing it is safe. What `seal` returns is base64 and carries no plaintext."""
import base64
import os
import subprocess
import tempfile

PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEA1eRYIhdlOTmjl8jknnKZ
AZw2C0WBMiE+5VnLx+FfiFcXzIsTwqJqD8Brc17D0k9ZA1P9Mfi0wJEBseB6MSug
MIpKRMJHtxicPl1XyrA2bQkkoGcllpnIF0TnOwCG0WnQnUhmmtOBGAgYsCHlS4QB
6xrGTR5mtLdiShM3PctX9UxhwqWlizOMqRZ9iVTtol/qWASImA3pb/Q8VD5k6UaL
Zq+0cEpjGNc88BQeKs+cgW8Y0SpqLJWpe3Kl4XE2C8HGU9ZCASTnnhdtb/dbjtCh
YiqQCmyrV+Wr5OASne720fcdsTA4yFjd5n4S4KAGm33gLOdvGeEmDLRT/kxDdG1x
LI8CUgOlulOlznTRe+FcqmP/tk8NxkoWyjVv+/7s8pjoSC7u6pke8CkNxDA3A/Gt
W5mVzdMidUQuXMEZPyIo4phTo1zJAft5VwnFjGbiddY7fGNM4cz2H3EdB0bNTIqt
jWP4G+RI5WXKIHSyO0r1vKNKHIWAAFGAhz3n5dTjjrrvAgMBAAE=
-----END PUBLIC KEY-----"""
MARK_BEGIN, MARK_END = "-----BEGIN LIFEOS SEALED REPORT-----", "-----END LIFEOS SEALED REPORT-----"
LINE = 76


def _run(args, data, env=None):
    done = subprocess.run(args, input=data, capture_output=True, check=False, timeout=60, env=env)
    if done.returncode != 0:
        raise RuntimeError("sealed_report_failed")            # a fixed word: openssl's own text is never copied
    return done.stdout


def seal(text, public_key=None):
    """-> the sealed block: [2-byte length][RSA-OAEP-wrapped key][AES-256-CBC (PBKDF2) ciphertext of `text`], as base64 between the two markers."""
    key = base64.b64encode(os.urandom(32))
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "public.pem")
        with open(path, "w") as handle:
            handle.write(public_key or PUBLIC_KEY)
        wrapped = _run(["openssl", "pkeyutl", "-encrypt", "-pubin", "-inkey", path, "-pkeyopt", "rsa_padding_mode:oaep", "-pkeyopt", "rsa_oaep_md:sha256"], key)
        body = _run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "100000", "-salt", "-pass", "env:SEAL_KEY"], text.encode(), env={**os.environ, "SEAL_KEY": key.decode()})
    blob = base64.b64encode(len(wrapped).to_bytes(2, "big") + wrapped + body).decode()
    return "\n".join([MARK_BEGIN, *[blob[i:i + LINE] for i in range(0, len(blob), LINE)], MARK_END])
