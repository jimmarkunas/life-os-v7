"""LIFE-560: find every environment-variable NAME the code reads and every name the workflows set. Names only; no value is ever read or kept.

Reads (AST over lifeos/**/*.py): os.environ[...], os.environ.get/pop/setdefault(...), os.getenv(...), `"X" in os.environ`, and the same on any object named like an environment
(a parameter called `environ`, `env`, `environment`, or an attribute ending in `environ`), because most modules take `environ=os.environ` so tests can pass a dict.
A read whose name is not a string literal is reported separately as DYNAMIC (file and line), never guessed.
Sets (YAML over .github/workflows/*.yml): every key of every `env:` mapping at workflow, job and step level."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_OBJECTS = ("environ", "env", "environment")
METHODS = ("get", "pop", "setdefault", "getenv")
NAME = re.compile(r"[A-Z][A-Z0-9_]*\Z")


def _is_env_object(node):
    if isinstance(node, ast.Name):
        return node.id in ENV_OBJECTS or node.id.endswith("environ")
    if isinstance(node, ast.Attribute):
        return node.attr in ENV_OBJECTS or node.attr.endswith("environ")
    return False


def _literal(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def reads_in(source):
    """-> (set of literal names, list of line numbers of reads whose name is not a literal)."""
    names, dynamic = set(), []
    for node in ast.walk(ast.parse(source)):
        key = None
        if isinstance(node, ast.Subscript) and _is_env_object(node.value):
            if not isinstance(node.ctx, ast.Load):
                continue                                  # a write (`env[k] = v`) is not a read
            key = node.slice
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in METHODS and node.args:
            owner = node.func.value
            if node.func.attr == "getenv" and isinstance(owner, ast.Name) and owner.id == "os" or (node.func.attr != "getenv" and _is_env_object(owner)):
                key = node.args[0]
        elif isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], (ast.In, ast.NotIn)) and _is_env_object(node.comparators[0]):
            key = node.left
        if key is None:
            continue
        name = _literal(key)
        if name is None:
            dynamic.append(node.lineno)
        elif NAME.match(name):
            names.add(name)
    return names, dynamic


def code_reads(root=ROOT, skip=()):
    """-> ({name: sorted files that read it}, {file: [lines of non-literal reads]}). `skip`: files whose `env` is not the process environment."""
    found, dynamic = {}, {}
    for path in sorted((root / "lifeos").rglob("*.py")):
        rel = str(path.relative_to(root))
        if rel in skip:
            continue
        names, dyn = reads_in(path.read_text())
        for name in names:
            found.setdefault(name, []).append(rel)
        if dyn:
            dynamic[rel] = dyn
    return found, dynamic


def _env_keys(node, out):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "env" and isinstance(value, dict):
                out.update(str(k) for k in value)
            _env_keys(value, out)
    elif isinstance(node, list):
        for item in node:
            _env_keys(item, out)


def workflow_sets(root=ROOT):
    """-> {name: sorted workflow files that set it in an env: block}."""
    import yaml
    found = {}
    for path in sorted((root / ".github" / "workflows").glob("*.yml")):
        keys = set()
        _env_keys(yaml.safe_load(path.read_text()), keys)
        for name in keys:
            if NAME.match(name):
                found.setdefault(name, []).append(path.name)
    return found


def problems(config, root=ROOT):
    """-> list of plain-words problems between the registry (`config`) and the code and workflows under `root`. Names only."""
    import fnmatch
    out = []
    listed = {row["name"]: row for row in config["names"]}
    prefixes, extra = tuple(config["provided"]["prefixes"]), set(config["provided"]["names"])

    def provided(name):
        return name.startswith(prefixes) or name in extra
    declared = {entry["file"]: entry["match"] for entry in config["patterns"]}
    reads, dynamic = code_reads(root, skip=tuple(config["not_environment"]))
    sets = workflow_sets(root)
    for name, files in sorted(reads.items()):
        if name not in listed and not provided(name):
            out.append(f"code reads {name} ({files[0]}) but the registry does not list it")
    for name, files in sorted(sets.items()):
        if name not in listed and not provided(name):
            out.append(f"workflow {files[0]} sets {name} but the registry does not list it")
    for file in sorted(dynamic):
        if file not in declared:
            out.append(f"{file} reads an environment name built at run time and the registry declares no pattern for it")
    for file in sorted(declared):
        if file not in dynamic:
            out.append(f"the registry declares a run-time pattern for {file}, which has no run-time read any more")
    for file, matches in sorted(declared.items()):
        for pattern in matches:
            if not any(fnmatch.fnmatch(name, pattern) for name in listed):
                out.append(f"the pattern {pattern} ({file}) matches no listed name")
    covered = lambda name: any(fnmatch.fnmatch(name, p) for ps in declared.values() for p in ps)
    for name in sorted(listed):
        if name not in reads and name not in sets and not covered(name):
            out.append(f"the registry lists {name} but no code reads it and no workflow sets it (stale)")
    return out
