"""Reproducible Buzz MCP release from one merged commit: the frozen manifest, its import-closure check, the build and the
hash verification. Stdlib only.

  python3 tools/buzz_release.py closure                 # the files lifeos.buzz.mcp needs at the working tree
  python3 tools/buzz_release.py build <commit> <out.tgz> # every manifest file at that commit, bin/ tools placed
  python3 tools/buzz_release.py verify <commit> <dir>    # sha256 of every file in an installed release dir vs the commit

The release directory holds exactly the manifest: lifeos/... as in the repo and bin/<name> from deploy/buzz-mcp/<name>.
A file that is not in the manifest, or a manifest file whose hash differs, fails verify."""

import argparse
import ast
import hashlib
import io
import pathlib
import subprocess
import sys
import tarfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "deploy" / "buzz-mcp" / "release-manifest.txt"
ENTRY = "lifeos.buzz.mcp"
DATA = ("lifeos/platform/mcp_access.json", "lifeos/platform/capability_catalog.json")
BIN = ("honcho-alert", "place-credentials", "selfhost-probe", "store-credential")


def read_manifest(path=MANIFEST):
    return [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")]


def _imports(source):
    out = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
            out.update(f"{node.module}.{a.name}" for a in node.names)
    return {m for m in out if m == "lifeos" or m.startswith("lifeos.")}


def _path(module, root):
    base = root.joinpath(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.exists():
            return candidate
    return None


def closure(root=ROOT):
    """-> sorted release paths: every lifeos module the service imports (packages' __init__ included), the two access
    files it reads, and the bin/ tools."""
    seen, todo, files = set(), [ENTRY], set()
    while todo:
        module = todo.pop()
        if module in seen:
            continue
        seen.add(module)
        path = _path(module, root)
        if path is None:
            continue
        files.add(path.relative_to(root).as_posix())
        parts = module.split(".")
        todo += [".".join(parts[:i]) for i in range(1, len(parts))]
        todo += sorted(_imports(path.read_text()))
    return sorted(files | set(DATA)) + [f"bin/{name}" for name in BIN]


def source_of(release_path):
    return f"deploy/buzz-mcp/{release_path[4:]}" if release_path.startswith("bin/") else release_path


def _show(commit, path):
    return subprocess.run(["git", "-C", str(ROOT), "show", f"{commit}:{path}"], check=True, capture_output=True).stdout


def build(commit, out):
    entries = read_manifest()
    with tarfile.open(out, "w:gz") as tar:
        for rel in entries:
            data = _show(commit, source_of(rel))
            info = tarfile.TarInfo(rel)
            info.size, info.mode, info.mtime = len(data), 0o755 if rel.startswith("bin/") else 0o644, 0
            tar.addfile(info, io.BytesIO(data))
            print(f"{hashlib.sha256(data).hexdigest()}  {rel}")


def verify(commit, directory):
    """-> 0 when the directory holds exactly the manifest at that commit, else 1 (each difference printed)."""
    directory = pathlib.Path(directory)
    want = {rel: hashlib.sha256(_show(commit, source_of(rel))).hexdigest() for rel in read_manifest()}
    have = {
        p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in directory.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    bad = [f"missing {r}" for r in sorted(set(want) - set(have))] + [
        f"extra {r}" for r in sorted(set(have) - set(want))
    ]
    bad += [f"hash {r}" for r in sorted(set(want) & set(have)) if want[r] != have[r]]
    for line in bad:
        print(line)
    print(f"verify: {len(want)} manifest files, {len(bad)} differences")
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("closure")
    b = sub.add_parser("build")
    b.add_argument("commit")
    b.add_argument("out")
    v = sub.add_parser("verify")
    v.add_argument("commit")
    v.add_argument("directory")
    args = ap.parse_args(argv)
    if args.cmd == "closure":
        print("\n".join(closure()))
        return 0
    if args.cmd == "build":
        build(args.commit, args.out)
        return 0
    return verify(args.commit, args.directory)


if __name__ == "__main__":
    sys.exit(main())
