# Config registry (LIFE-560)

One checked list of every environment-variable **name** LIFE OS reads or sets. It lists names only: never a value, a secret id or a path. No read site was changed and there is no central getter.

**Where it is.** The `CONFIG` constant in `lifeos/platform/credential_list.py`, the same module as the credential-expiry list (`CREDENTIALS`, LIFE-528). There is one registry file with two lists of different shapes; they are not merged. (LIFE-560's ticket text names `config_registry.json`; Jim's LIFE-522 comment 12565 says to extend the existing credential list and not add a second registry, and the Close service ships only `.py` files, so it is a Python constant.)

**Kinds.**
* `input`: read by code, set by a workflow or a person (ids, addresses, counts, dates).
* `flag`: an on/off or mode switch.
* `secret`: a credential or private detail. Listed by identifier only; the kind marks it.
* `workflow_only`: set in a workflow and used by a shell step or an action, not read by Python.

GitHub- and host-provided names (`GITHUB_*`, `RUNNER_*`, `CI`, `PATH`, `SUDO_USER`, `CREDENTIALS_DIRECTORY`, `HOME`) are one group under `provided`, not listed one by one.

**Names built at run time** (for example `"LIFEOS_ACQ_" + field`) cannot be read from the code, so the file that builds them is declared under `patterns` with the name patterns it may read. A new run-time read in an undeclared file fails the test.

**How to add a variable.** Add one row to `CONFIG["names"]` (keep the list sorted): `name`, `kind`, `owner` (the package that reads it, or `workflow`), and a one-line `purpose` in words. Then run `python -m unittest tests.contracts.test_config_registry`.

**What the test checks** (`tests/contracts/test_config_registry.py`, scanner `tests/config_scan.py`): every name read in `lifeos/` (AST: `os.environ[...]`, `.get`, `os.getenv`, `in os.environ`, and the same on any `environ` or `env` parameter) and every name in a workflow `env:` block is listed; no listed name is stale; every run-time read is declared; and each of those rules has a negative control that plants a fault and shows it fails.

**Measured at creation (Nov 10 2026).** 125 distinct names read in 94 files; 147 set in the workflow `env:` blocks of 22 workflows; 35 reads built at run time in 19 files (3 files are not environment reads and are listed under `not_environment`); 158 rows after the provided group is taken out (72 input, 46 secret, 30 flag, 10 workflow_only).
