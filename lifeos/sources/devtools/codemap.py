"""The code map: one generated, machine-readable index of V7 so an agent can find things without reading the repo.  python -m lifeos.sources.devtools.codemap  (writes docs/CODEMAP.md and docs/codemap.json)

Everything in it is derived from the code itself (module docstrings, the stage registry, the workflows, the CREATE TABLE statements, the decision index), so it cannot drift:
a contract test regenerates it and fails when the committed files differ. Names of things only; no data, no secrets, no values."""
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WIDTH = 170


def first_line(path):
    try:
        text = ast.get_docstring(ast.parse(path.read_text())) or ""
    except SyntaxError:
        return ""
    return " ".join(text.split("\n\n")[0].split())[:WIDTH]


def packages():
    out = {}
    for pkg in sorted(p for p in (ROOT / "lifeos").iterdir() if p.is_dir() and (p / "__init__.py").exists()):
        out[pkg.name] = {str(f.relative_to(ROOT)): first_line(f) for f in sorted(pkg.rglob("*.py")) if f.name != "__init__.py" and "__pycache__" not in f.parts}
    return out


def stages():
    sys.path.insert(0, str(ROOT))
    from lifeos import run                                              # noqa: PLC0415 - lazy stages import nothing until called
    out = {}
    for name, fn in sorted(run.STAGES.items()):
        target = getattr(fn, "target", None)
        out[name] = f"{target[0]}:{target[1]}" if target else f"lifeos.run:{getattr(fn, '__name__', name)}"
    return out


def workflows():
    out = {}
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        text = path.read_text()
        jobs = re.findall(r"^  ([a-z][\w-]*):\s*$", text.split("\njobs:\n", 1)[-1], re.M)
        out[path.name] = {"secrets": sorted(set(re.findall(r"secrets\.([A-Z0-9_]+)", text))), "jobs": jobs, "manual": "workflow_dispatch" in text}
    return out


def tables():
    out = {}
    for path in sorted((ROOT / "lifeos").rglob("*.py")):
        for name in re.findall(r"CREATE TABLE IF NOT EXISTS (v7_\w+)", path.read_text()):
            out.setdefault(name, str(path.relative_to(ROOT)))
    return out


def decisions():
    index = (ROOT / "docs" / "DECISIONS_INDEX.md").read_text()
    return {m.group(1): m.group(2) for m in re.finditer(r"^- (D\d+) — (.+)$", index, re.M)}


def build():
    return {"stages": stages(), "packages": packages(), "workflows": workflows(), "tables": tables(), "decisions": decisions()}


ROUTING_LIMIT = 15 * 1024                                              # LIFE-592: the routing page stays under this many bytes (a test fails above it)


def package_line(name, files):
    """One line per package for the routing page: the package docstring's first sentence (or the first module's), and the module count."""
    init = ROOT / "lifeos" / name / "__init__.py"
    summary = first_line(init) if init.exists() else ""
    summary = summary or next((d for d in files.values() if d), "")
    return f"- `lifeos/{name}/` ({len(files)} modules): {summary[:110]}"


def markdown(m):
    """LIFE-592: a SHORT routing page. The full map (every module with its description, every workflow secret, every decision title) is docs/codemap.json; ask it for one thing with
    `python -m lifeos.sources.devtools.codemap find <name>`."""
    lines = ["# V7 code map (generated: do not edit; run `python -m lifeos.sources.devtools.codemap`)", "",
             "A routing page. Find a stage here, then its module. For anything else ask one question instead of reading the whole map: "
             "`python -m lifeos.sources.devtools.codemap find <text>` (a package, module, stage, workflow, table or decision number/word; prints only the matching entries). "
             "Full machine-readable map: `docs/codemap.json`. Decisions: `docs/DECISIONS_INDEX.md` (find), then `grep -n \"^## D<n>\" docs/DECISIONS.md`.", "",
             "## Stages (`python -m lifeos.run <stage>`)"]
    lines += [f"- `{k}` -> `{v}`" for k, v in m["stages"].items()]
    lines += ["", "## Packages (`find <package>` lists its modules)"]
    lines += [package_line(pkg, files) for pkg, files in m["packages"].items()]
    lines += ["", "## Workflows (`find <name>` lists jobs and secret names)"]
    lines += [f"- `{k}`: {len(v['jobs'])} jobs; manual {'yes' if v['manual'] else 'no'}" for k, v in m["workflows"].items()]
    lines += ["", "## Database tables (Hostinger) and the file that owns each"]
    lines += [f"- `{k}`: `{v}`" for k, v in m["tables"].items()]
    lines += ["", f"## Decisions: {len(m['decisions'])} in `docs/DECISIONS_INDEX.md` (`find D<n>` or `find <word>` prints matching titles)"]
    return "\n".join(lines) + "\n"


def find(m, text, limit=40):
    """-> the lines of every map entry that matches `text` (case-insensitive substring; a bare D<number> matches that decision exactly). A package name lists all its modules."""
    needle = text.strip().lower()
    out = []
    if not needle:
        return out
    for name, target in m["stages"].items():
        if needle in name.lower() or needle in target.lower():
            out.append(f"stage `{name}` -> `{target}`")
    for pkg, files in m["packages"].items():
        if needle == pkg.lower():
            out.append(f"package `lifeos/{pkg}/` ({len(files)} modules):")
            out += [f"  `{f}`: {d}" for f, d in files.items()]
            continue
        for f, d in files.items():
            if needle in f.lower() or needle in d.lower():
                out.append(f"module `{f}`: {d}")
    for name, w in m["workflows"].items():
        if needle in name.lower() or needle in " ".join(w["jobs"]).lower() or needle in " ".join(w["secrets"]).lower():
            out.append(f"workflow `{name}`: jobs {', '.join(w['jobs']) or '-'}; manual {'yes' if w['manual'] else 'no'}; secrets {', '.join(w['secrets']) or '-'}")
    for name, owner in m["tables"].items():
        if needle in name.lower() or needle in owner.lower():
            out.append(f"table `{name}` -> `{owner}`")
    exact = re.fullmatch(r"d(\d+)", needle)
    for number, title in m["decisions"].items():
        if (exact and number.lower() == needle) or (not exact and (needle in number.lower() or needle in title.lower())):
            out.append(f"decision {number}: {title}")
    return out[:limit] + ([f"... {len(out) - limit} more; narrow the text"] if len(out) > limit else [])


def render():
    m = build()
    return markdown(m), json.dumps(m, indent=1, sort_keys=True) + "\n"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "find":
        found = find(json.loads((ROOT / "docs" / "codemap.json").read_text()), " ".join(argv[1:]))
        print("\n".join(found) if found else "nothing matches; try a shorter word")
        return 0 if found else 1
    md, js = render()
    (ROOT / "docs" / "CODEMAP.md").write_text(md)
    (ROOT / "docs" / "codemap.json").write_text(js)
    print(f"codemap: {len(md.splitlines())} lines, {len(md.encode())} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
