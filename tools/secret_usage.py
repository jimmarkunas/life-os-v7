"""LIFE-861: for each workflow job, the Bitwarden names it loads that none of the code it runs reads (an excess load), by reading the repository only.

    python tools/secret_usage.py            prints one line per excess load and a count; exit 1 when there is any
    python tools/secret_usage.py --all      also prints every job's loaded names with the stage modules checked

A step that maps a loaded name into its environment (`NAME: ${{ steps.<bws id>.outputs.NAME }}`) is judged by the code it runs: `python -m lifeos.run <stage>` reaches the stage's module (lifeos.run STAGES) and every `lifeos.*` module that module
imports, directly or through the modules it imports; `python -m lifeos.<module>` reaches that module the same way. The name is USED when any reached module reads the environment name the step gives it (the AST scan of tests/config_scan.py:
os.environ[...], .get, getenv, `in environ`) or writes the name out as a string (a tuple of names read in a loop). A name a step passes anywhere else (a `with:` input, a shell line, an action) is USED, because only Python code can be judged here. A loaded name that no step mentions is excess. A module that
reads environment names by a built name is listed under `dynamic` and never widens the result. Names only; no value is read. Needs PyYAML."""
import ast
import fnmatch
import functools
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests import config_scan  # noqa: E402

OUTPUT = re.compile(r"steps\.(\w+)\.outputs\.(\w+)")
COMMAND = re.compile(r"python3?\s+-m\s+(lifeos(?:\.\w+)+)(?:\s+[\"']?([a-z][\w-]*)(\$\(?)?)?")      # a stage name built in the shell (`amazon-$(...)`) keeps its literal prefix and the `$` marker


def _module_path(name, root=ROOT):
    base = root.joinpath(*name.split("."))
    if base.with_suffix(".py").exists():
        return base.with_suffix(".py")
    init = base / "__init__.py"
    return init if init.exists() else None


def _imports(tree):
    """-> every lifeos module name this tree imports anywhere (top level or inside a function)."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.startswith("lifeos"))
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("lifeos"):
            found.add(node.module)
            found.update(f"{node.module}.{a.name}" for a in node.names)
    return found


class Graph:
    def __init__(self, root=ROOT):
        self.root, self.cache, self.reads_cache, self.imports_cache = root, {}, {}, {}      # LIFE-963: every cache is per module, so a module is parsed, walked and scanned once per run

    def parsed(self, name):
        if name not in self.cache:
            path = _module_path(name, self.root)
            self.cache[name] = (ast.parse(path.read_text()), path) if path else None
        return self.cache[name]

    def reach(self, entries):
        seen, todo = set(), list(entries)
        while todo:
            name = todo.pop()
            if name in seen or not self.parsed(name):
                continue
            seen.add(name)
            parent = name.rsplit(".", 1)[0]
            todo.extend([parent] if parent != "lifeos" and parent not in seen else [])
            if name not in self.imports_cache:
                self.imports_cache[name] = _imports(self.parsed(name)[0])
            todo.extend(self.imports_cache[name] - seen)
        return seen

    def module_reads(self, name):
        """-> (names a module reads or writes out, whether it reads a name built at run time); parsed once per module."""
        if name not in self.reads_cache:
            tree, path = self.parsed(name)
            found, dyn = config_scan.reads_in(path.read_text())
            found |= {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and config_scan.NAME.match(n.value)}   # a name written out in code that is reached (a tuple of names read in a loop)
            self.reads_cache[name] = (found, bool(dyn))
        return self.reads_cache[name]

    def reads(self, modules):
        """-> (names read or written out, patterns declared for the modules that read a name built at run time (the config registry))."""
        names, patterns = set(), []
        for name in modules:
            found, dyn = self.module_reads(name)
            names |= found
            if dyn:
                patterns += declared_patterns().get(str(self.parsed(name)[1].relative_to(self.root)), [])
        return names, patterns


_PATTERNS = {}


def declared_patterns():
    """-> {file: [patterns]} from the config registry: the names a module that builds names at run time may read (for example LIFEOS_ACQ_*). Read once per process (LIFE-963: it was read 642 times a run)."""
    if not _PATTERNS:
        from lifeos.platform import credentials
        _PATTERNS.update({entry["file"]: entry["match"] for entry in credentials.config()["patterns"]})
    return _PATTERNS


def stage_entries(stage, graph, dynamic=""):
    """-> the modules a `lifeos.run <stage>` reaches: lifeos.run's own top-level imports plus the stage's target (a lazy stage's module, or the imports inside its function)."""
    import importlib
    run = importlib.import_module("lifeos.run")
    if dynamic or not stage:                                                    # `amazon-$(...)` or `"$STAGE"`: every stage that starts with the literal prefix (all of them when there is none)
        entries = {"lifeos.run"}
        for name in run.STAGES:
            if name.startswith(stage):
                entries |= stage_entries(name, graph)
        return entries
    function = run.STAGES.get(stage)
    entries = {"lifeos.run"}
    target = getattr(function, "target", None)
    if target:
        entries.add(target[0])
    elif function is not None:
        tree = graph.parsed("lifeos.run")[0]
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == getattr(function, "__name__", ""):
                entries |= _imports(node)
    return entries


def _env_mappings(step):
    """-> {ENV NAME: (bws step id, output name)} for the step's env block."""
    out = {}
    for key, value in (step.get("env") or {}).items():
        match = OUTPUT.search(str(value))
        if match:
            out[key] = match.groups()
    return out


_GRAPH = None


def analyse(docs, graph=None):
    """-> (excess, detail). `docs`: {workflow file name: parsed YAML}. excess: sorted [(workflow, job, name)]; detail: {(workflow, job): {name: why}}."""
    global _GRAPH
    graph = graph or _GRAPH or Graph()
    _GRAPH = graph if graph.root == ROOT else _GRAPH                                      # parsed modules are reused between calls
    excess, detail = [], {}
    for wf, doc in sorted(docs.items()):
        for job_name, job in sorted((doc.get("jobs") or {}).items()):
            steps = job.get("steps") or []
            loads = {}
            for step in steps:
                if str(step.get("uses", "")).endswith("bws-load"):
                    lines = str(step["with"]["secrets"]).strip().splitlines()
                    loads[step.get("id")] = [line.split(">")[1].strip() for line in lines if ">" in line]
            if not loads:
                continue
            used, why = {}, {}
            for step in steps:
                mapped = _env_mappings(step)
                commands = COMMAND.findall(str(step.get("run", "")))
                text = str(step)
                if mapped and commands and not str(step.get("uses", "")):
                    entries = set()
                    for module, stage, dyn in commands:
                        entries |= stage_entries(stage, graph, dyn) if module == "lifeos.run" else {module}
                    modules = graph.reach(entries)
                    read, patterns = graph.reads(modules)
                    shell = str(step.get("run", ""))
                    for env_name, (sid, name) in mapped.items():
                        if env_name in read or any(fnmatch.fnmatch(env_name, p) for p in patterns) or re.search(r"\$\{?%s\b" % re.escape(env_name), shell):
                            used[(sid, name)] = f"read by code reached from {', '.join(f'{m} {s}'.strip() for m, s, _ in commands)}"
                        else:
                            why.setdefault((sid, name), f"given to a step as {env_name}, but no code that step runs reads it ({', '.join(f'{m} {s}'.strip() for m, s, _ in commands)})")
                else:
                    for sid, name in OUTPUT.findall(text):
                        used[(sid, name)] = "passed to an action or shell line"
            exported = [s for s in steps if str(s.get("uses", "")).endswith("bws-load") and str((s.get("with") or {}).get("set_env", "true")).lower() != "false"]
            if exported:                                                       # set_env true: every name is in the job environment, so any code any step runs may read it
                entries, shell = set(), ""
                for step in steps:
                    for module, stage, dyn in COMMAND.findall(str(step.get("run", ""))):
                        entries |= stage_entries(stage, graph, dyn) if module == "lifeos.run" else {module}
                    shell += str(step.get("run", "")) + "\n"
                read, patterns = graph.reads(graph.reach(entries))
                for step in exported:
                    for name in loads[step.get("id")]:
                        if name in read or any(fnmatch.fnmatch(name, p) for p in patterns) or re.search(r"\$\{?%s\b|\b%s\b" % (re.escape(name), re.escape(name)), shell):
                            used[(step.get("id"), name)] = "job environment, read by code the job runs"
            report = {}
            for sid, names in loads.items():
                for name in names:
                    key = (sid, name)
                    if key in used:
                        report[name] = used[key]
                    else:
                        report[name] = "EXCESS: " + why.get(key, "loaded, never passed to any step")
                        excess.append((wf, job_name, name))
            detail[(wf, job_name)] = report
    return sorted(excess), detail


def load_docs(root=ROOT):
    import yaml
    import wfsets
    return {p.name: yaml.safe_load(wfsets.expand_text(p.read_text(), wfsets.load(root / wfsets.SETS.relative_to(wfsets.ROOT)))) for p in sorted((root / ".github" / "workflows").glob("*.yml"))}   # LIFE-1013: `set:` is read as the list it names


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    excess, detail = analyse(load_docs())
    if "--all" in argv:
        for (wf, job), report in sorted(detail.items()):
            print(f"{wf}::{job}")
            for name, reason in sorted(report.items()):
                print(f"    {name}: {reason}")
    for wf, job, name in excess:
        print(f"excess: {wf}::{job} loads {name} and no code it runs reads it")
    print(f"secret_usage: {len(excess)} excess load(s) across {len(detail)} job(s)")
    return 1 if excess else 0


if __name__ == "__main__":
    sys.exit(main())
