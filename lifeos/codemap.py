"""The code map: one generated, machine-readable index of V7 so an agent can find things without reading the repo.  python -m lifeos.codemap  (writes docs/CODEMAP.md and docs/codemap.json)

Everything in it is derived from the code itself (module docstrings, the stage registry, the workflows, the CREATE TABLE statements, the decision index), so it cannot drift:
a contract test regenerates it and fails when the committed files differ. Names of things only; no data, no secrets, no values."""
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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


def markdown(m):
    lines = ["# V7 code map (generated: do not edit; run `python -m lifeos.codemap`)", "",
             "Start here. Find a stage, then its module; find the file for a feature in Packages; find why in Decisions (then `grep -n \"^## D<n>\" docs/DECISIONS.md`). Machine-readable twin: `docs/codemap.json`.", "",
             "## Stages (`python -m lifeos.run <stage>`)"]
    lines += [f"- `{k}` -> `{v}`" for k, v in m["stages"].items()]
    lines += ["", "## Packages and modules"]
    for pkg, files in m["packages"].items():
        lines.append(f"### lifeos/{pkg}/")
        lines += [f"- `{f}`: {d}" for f, d in files.items()]
    lines += ["", "## Workflows (jobs, manual trigger, secrets used by name)"]
    lines += [f"- `{k}`: jobs {', '.join(v['jobs']) or '-'}; manual {'yes' if v['manual'] else 'no'}; secrets {', '.join(v['secrets']) or '-'}" for k, v in m["workflows"].items()]
    lines += ["", "## Database tables (Hostinger) and the file that owns each"]
    lines += [f"- `{k}`: `{v}`" for k, v in m["tables"].items()]
    lines += ["", "## Decisions (index)"]
    lines += [f"- {k}: {v}" for k, v in m["decisions"].items()]
    return "\n".join(lines) + "\n"


def render():
    m = build()
    return markdown(m), json.dumps(m, indent=1, sort_keys=True) + "\n"


def main():
    md, js = render()
    (ROOT / "docs" / "CODEMAP.md").write_text(md)
    (ROOT / "docs" / "codemap.json").write_text(js)
    print(f"codemap: {len(md.splitlines())} lines")


if __name__ == "__main__":
    main()
