# Config registry (LIFE-560)

One checked list of every environment-variable **name** LIFE OS reads or sets. It lists names only: never a value, a secret id or a path. No read site was changed and there is no central getter.

**Where it is.** One row per name, in `lifeos/<owner package>/config.txt` (LIFE-644; it was the `CONFIG` constant in `lifeos/platform/credential_list.py`). A row is a line `NAME kind purpose`; `kind:owner` names an owner that is not a package (for example `workflow`). `credentials.config()` collects the files, sorted by name, together with the groups that stay in `credential_list.py` (`PROVIDED`, `PATTERNS`, `NOT_ENVIRONMENT`).

**Kinds.**
* `input`: read by code, set by a workflow or a person (ids, addresses, counts, dates).
* `flag`: an on/off or mode switch.
* `secret`: a credential or private detail. Listed by identifier only; the kind marks it.
* `workflow_only`: set in a workflow and used by a shell step or an action, not read by Python.

GitHub- and host-provided names (`GITHUB_*`, `RUNNER_*`, `CI`, `PATH`, `SUDO_USER`, `CREDENTIALS_DIRECTORY`, `HOME`) are one group under `provided`, not listed one by one.

**Names built at run time** (for example `"LIFEOS_ACQ_" + field`) cannot be read from the code, so the file that builds them is declared under `patterns` with the name patterns it may read. A new run-time read in an undeclared file fails the test.

**How to add a variable.** Add one line `NAME kind purpose` to the owner package's `config.txt` (create it if the package has none; a name no package owns goes in `lifeos/platform/config.txt` as `NAME kind:workflow purpose`). The collector sorts, so a merge is a one-line fix. Then run `python -m unittest tests.contracts.test_config_registry`.

**What the test checks** (`tests/contracts/test_config_registry.py`, scanner `tests/config_scan.py`): every name read in `lifeos/` (AST: `os.environ[...]`, `.get`, `os.getenv`, `in os.environ`, and the same on any `environ` or `env` parameter) and every name in a workflow `env:` block is listed; no listed name is stale; every run-time read is declared; and each of those rules has a negative control that plants a fault and shows it fails.

**Measured at creation (Nov 10 2026).** 125 distinct names read in 94 files; 147 set in the workflow `env:` blocks of 22 workflows; 35 reads built at run time in 19 files (3 files are not environment reads and are listed under `not_environment`); 158 rows after the provided group is taken out (72 input, 46 secret, 30 flag, 10 workflow_only).
