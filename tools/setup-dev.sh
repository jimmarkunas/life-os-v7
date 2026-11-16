#!/usr/bin/env bash
# LIFE-743: one command that makes local checks work: Python 3.12 with the locked runtime and test packages (PyYAML, ruff, coverage), in ONE environment shared by every worktree.
#   sh tools/setup-dev.sh            build or repair the environment, then print its path (a second run changes nothing)
#   LIFEOS_VENV=DIR sh tools/setup-dev.sh   use another folder (default ~/.venvs/lifeos-tests, which tools/test.sh already searches)
# It installs only what requirements.lock and requirements-test.lock name, by version (their sha256 pins are for the Linux CI wheels, so a Mac installs the same versions without the hash check).
# No global install and no sudo. On the Oracle edge host it refuses: install nothing there without Jim's typed yes.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${LIFEOS_VENV:-$HOME/.venvs/lifeos-tests}"

case "$(hostname 2>/dev/null)" in
  *lifeos-edge*) echo "setup-dev.sh: refusing on the Oracle edge host; name this to Jim instead" >&2; exit 3 ;;
esac

PY=""
for candidate in "${LIFEOS_PYTHON:-}" "$(command -v python3.12 2>/dev/null || true)" /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12; do
  if [ -n "$candidate" ] && [ -x "$candidate" ] && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)' >/dev/null 2>&1; then PY="$candidate"; break; fi
done
[ -n "$PY" ] || { echo "setup-dev.sh: Python 3.12 is not installed (on the Mac, install the Homebrew formula named python 3.12), then run this again" >&2; exit 3; }

if [ -x "$VENV/bin/python" ] && ! "$VENV/bin/python" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)' >/dev/null 2>&1; then
  echo "setup-dev.sh: $VENV is not Python 3.12; rebuilding it"; rm -rf "$VENV"
fi
[ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV"

if command -v shasum >/dev/null 2>&1; then sum() { shasum -a 256; }; else sum() { sha256sum; }; fi
want="$(cat "$ROOT/requirements.lock" "$ROOT/requirements-test.lock" | sum | cut -d' ' -f1)"
have="$(cat "$VENV/.lifeos-locks" 2>/dev/null || true)"
if [ "$want" != "$have" ] || ! "$VENV/bin/python" -c 'import yaml, coverage' >/dev/null 2>&1 || [ ! -x "$VENV/bin/ruff" ]; then
  echo "setup-dev.sh: installing the locked packages into $VENV"
  grep -hE '^[A-Za-z0-9_.-]+==' "$ROOT/requirements.lock" "$ROOT/requirements-test.lock" | sed 's/ *\\$//' | "$VENV/bin/pip" install -q --disable-pip-version-check -r /dev/stdin
  printf '%s\n' "$want" > "$VENV/.lifeos-locks"
fi

"$VENV/bin/python" -c 'import yaml, coverage' && "$VENV/bin/ruff" --version >/dev/null
echo "setup-dev.sh: ready: $("$VENV/bin/python" -V) at $VENV/bin/python (tools/test.sh uses it by itself)"
