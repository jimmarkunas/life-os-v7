"""the Atlassian MCP connector (v2) is the only Jira connector an agent may use"""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENT_FOLDERS = (".agents", ".claude", ".codex", ".cursor", ".gemini", ".github")


def tracked_agent_files():
    for folder in AGENT_FOLDERS:
        for path in (ROOT / folder).rglob("*"):
            if (
                path.is_file()
                and "worktrees" not in path.parts
                and path.suffix in (".json", ".md", ".mdc", ".toml", ".yml", ".yaml")
            ):
                yield path
    for name in (".mcp.json", "AGENTS.md", "CLAUDE.md"):
        if (ROOT / name).is_file():
            yield ROOT / name


class OnlyTheSupportedAtlassianConnector(unittest.TestCase):
    def test_no_permission_or_settings_file_allows_a_rovo_tool(self):
        allowed = []
        for path in tracked_agent_files():
            if path.suffix == ".json" and "rovo" in path.read_text().lower():
                allowed.append(str(path.relative_to(ROOT)))
        self.assertEqual(
            allowed,
            [],
            "a settings or allowlist file names a Rovo connector tool: use mcp__claude_ai_Atlassian_MCP__* instead",
        )

    def test_no_agent_instruction_tells_an_agent_to_call_a_rovo_tool(self):
        for path in tracked_agent_files():
            if path.suffix == ".json":
                continue
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if "Atlassian_Rovo" in line:
                    self.fail(f"{path.relative_to(ROOT)}:{number} names a Rovo connector tool")

    def test_agents_md_names_the_one_supported_connector(self):
        text = (ROOT / "AGENTS.md").read_text()
        self.assertIn("mcp__claude_ai_Atlassian_MCP__", text)
        self.assertIn("Rovo connectors are retired", text)
        for path in (ROOT / ".claude/settings.json", ROOT / ".claude/settings.local.json"):
            if path.is_file():
                json.loads(path.read_text())  # still valid JSON


if __name__ == "__main__":
    unittest.main()
