"""LIFE-1013: the credential lists a workflow names with `set: <name>` on a `bws-load` step live in `.github/actions/bws-load/sets.yml`, one list per job; the lines are the same `<id> > NAME` lines that used to be inline. Code that reads what a workflow loads (the workflow gate, the secret-usage report, the contract tests) reads it through here.

expand_text(text)   workflow text with every `set: name` line replaced by the inline `secrets: |` block it stands for"""

import pathlib
import re

import yaml

import wfsrc

ROOT = pathlib.Path(__file__).resolve().parents[1]
SETS = ROOT / ".github/actions/bws-load/sets.yml"
REF = re.compile(r"(?m)^( +)set: ([A-Za-z0-9_-]+)\s*$")


def load(path=None):
    """-> {set name: the list text, one `<id> > NAME` line per line}."""
    return yaml.safe_load((path or SETS).read_text()) or {}


def expand_text(text, sets=None):
    """Plain text of a workflow: the YAML aliases the generator wrote (LIFE-1013) and the `set:` names are both written out."""
    sets = load() if sets is None else sets
    text = wfsrc.expand_aliases(text)

    def inline(found):
        indent = found.group(1)
        return f"{indent}secrets: |\n" + "".join(f"{indent}  {line}\n" for line in sets[found.group(2)].splitlines())

    return REF.sub(inline, text)
