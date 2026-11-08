import json
import pathlib
import re
import shutil
import subprocess
import unittest

DIR = pathlib.Path(__file__).resolve().parents[3] / "lifeos" / "edge" / "buzzplugin" / "lifeos-probe"
HARNESS = {"buzz.claude-code/claude-code", "buzz.codex/codex", "buzz.pi/pi"}
DRIVER = """
import { apply, toolName, record } from %(plugin)r;
const hooks = [], tools = [];
const ctx = { agents: { registerHook: (h) => hooks.push(h), addTool: (t) => tools.push(t) } };
apply(ctx);
const out = { hooks: hooks.map((h) => [h.harness, h.event, h.id]), tools: tools.map((t) => t.name) };
out.returns = hooks.map((h) => h.run({ tool_name: "mcp__x__y", tool_input: { secret: "S" } }, { agent: "a".repeat(64), session: "sess-1234567890abcdef" }));
out.pi = hooks[2].run({ call: { name: "bash" } }, { agent: "b".repeat(64), session: "s" }) ?? null;
out.bad = hooks[0].run(null, null) ?? null;
const report = JSON.parse(await tools[0].call({ pubkey: "p".repeat(64), key: "k" }, { channel: "c", topLevel: true }, {}));
out.report = report;
console.log(JSON.stringify(out));
"""


class Manifest(unittest.TestCase):
    def test_the_manifest_declares_only_the_three_hook_grants(self):
        m = json.loads((DIR / "manifest.json").read_text())
        self.assertEqual((m["id"], m["apiVersion"]), ("lifeos.probe", 1))
        self.assertEqual(set(m["host"]["agentHooks"]), HARNESS)
        self.assertEqual(set(m["host"]), {"agentHooks"})                      # no network origins, commands, processes or files

    def test_the_plugin_has_no_network_shell_file_or_secret_access(self):
        text = (DIR / "plugin.js").read_text()
        for word in ("fetch(", "XMLHttpRequest", "ctx.host", "runCommand", "spawn", "require(", "import(", "localStorage", "process.env", "eval("):
            self.assertNotIn(word, text, word)
        self.assertEqual(re.findall(r"^import ", text, re.M), [])


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class Behaviour(unittest.TestCase):
    def test_hooks_observe_only_and_the_tool_reports_names_never_inputs(self):
        run = subprocess.run(["node", "--input-type=module", "-e", DRIVER % {"plugin": (DIR / "plugin.js").as_uri()}], capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        out = json.loads(run.stdout)
        self.assertEqual([h[:2] for h in out["hooks"]], [["buzz.claude-code/claude-code", "PreToolUse"], ["buzz.codex/codex", "PreToolUse"], ["buzz.pi/pi", "beforeTool"]])
        self.assertEqual(out["tools"], ["lifeos_probe_report"])
        self.assertEqual(out["returns"], [None, None, None])                # JSON turns undefined into null: every hook returned undefined, so nothing is decided
        self.assertIsNone(out["pi"])
        self.assertIsNone(out["bad"])                                       # a null input or context never throws
        report = out["report"]
        self.assertEqual(report["you"], {"pubkey": "p" * 64, "key": "k"})
        self.assertEqual(report["errors"], [])
        tools = [s["tool"] for s in report["seen"]]
        self.assertEqual(tools, ["mcp__x__y", "mcp__x__y", "mcp__x__y", "bash", None])
        self.assertNotIn("secret", json.dumps(report).lower().replace("secret_free", ""))
        self.assertEqual(report["seen"][0]["session"], "sess-1234567890a")  # trimmed to 16


if __name__ == "__main__":
    unittest.main()
