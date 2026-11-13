"""LIFE-647 group 1: load one installed program the way its service does and prove it is the trimmed one. Usage: check.py NAME PATH. Prints READBACK counts; exit 1 on any miss."""
import importlib.util
import sys

name, path = sys.argv[1:3]
spec = importlib.util.spec_from_file_location("chk_" + name[:-3], path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
if name == "offsite_backup.py":
    ok = tuple(len(getattr(mod, n.upper())) for n in ("sqlite", "files", "trees", "secret_trees")) == (16, 19, 13, 1) and len(mod.PROFILES) == 5
elif name == "ticker.py":
    ok = callable(mod.watch) and callable(mod.gate.watch) and callable(mod.stall_incident)
elif name == "close_release.py":
    ok = callable(mod.latest) and not hasattr(mod, "hourly_report")
else:
    ok = callable(mod._grans) and sorted(mod.OPS) == ["list", "purge", "show", "sync"]
print("READBACK check_%s=%s" % (name[:-3], "ok" if ok else "bad"))
sys.exit(0 if ok else 1)
