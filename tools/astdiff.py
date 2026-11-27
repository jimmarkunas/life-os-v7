"""LIFE-1016 / LIFE-1017: the proof that a prose or formatting change changed no behaviour.

    python tools/astdiff.py BASE [HEAD]      every .py file that differs between the two commits (HEAD defaults to the working tree)
    python tools/astdiff.py --each BASE HEAD  the same for every commit on BASE..HEAD, one line per commit

Each changed file is parsed before and after, docstrings are dropped (the first statement of a module, class or function when it is a string), and the two `ast.dump` texts are compared. Comments and layout are not in an AST, so a comment, docstring or formatting change shows no difference; anything that changes a statement, name, literal or default does. Exit 0 and `empty AST diff for N files` when nothing differs; exit 1 and the file names otherwise."""
import ast
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
AREAS = ("lifeos/", "tools/", "tests/")


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def bare(source):
    """-> the AST dump of `source` with every docstring removed."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    return ast.dump(tree)


def changed(base, head):
    names = git("diff", "--name-only", "--diff-filter=AMD", base, *([head] if head else [])).stdout.split()
    return [n for n in names if n.endswith(".py") and n.startswith(AREAS)]


def source(ref, path):
    if ref is None:
        file = ROOT / path
        return file.read_text() if file.exists() else None
    done = git("show", f"{ref}:{path}")
    return done.stdout if done.returncode == 0 else None


def differing(base, head):
    """-> the changed .py paths whose docstring-free AST differs between `base` and `head` (None head = the working tree)."""
    out = []
    for path in changed(base, head):
        before, after = source(base, path), source(head, path)
        if before is None or after is None or bare(before) != bare(after):
            out.append(path)
    return out


def main(argv):
    if argv[:1] == ["--each"]:
        base, head = argv[1], argv[2]
        bad = 0
        for commit in git("rev-list", "--reverse", f"{base}..{head}").stdout.split():
            diff = differing(f"{commit}^", commit)
            print(f"{commit[:8]}: " + ("empty AST diff" if not diff else "DIFFERS " + ", ".join(diff[:5])))
            bad += bool(diff)
        return 1 if bad else 0
    base, head = argv[0], (argv[1] if len(argv) > 1 else None)
    diff = differing(base, head)
    if diff:
        print("AST differs: " + ", ".join(diff))
        return 1
    print(f"empty AST diff for {len(changed(base, head))} files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
