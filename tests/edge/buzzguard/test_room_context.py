"""LIFE-397: a mentioned Buzz agent can read its room (channel info, history, thread, event, canvas) before it answers; nothing else is widened.
Kept apart from test_guard.py (LIFE-333) so the CI shard balance (tests/shard.py, by file size) stays as it was."""
import json
import os
import subprocess

from tests.edge.buzzguard.test_guard import ATL, GH, GOOD_REPO, GUARD, Base


class RoomContext(Base):
    """LIFE-397: a mentioned agent must be able to read the room (channel info, history, thread, the kickoff event) before it answers; nothing else is widened."""
    EID = "ab12" * 16
    CHAN = "0a1b2c3d-1111-4222-8333-944455566677"

    def test_room_context_reads_with_real_ids_are_allowed(self):
        for tool, ti in (("mcp__buzz__get_channel_info", {"channel": self.CHAN}), ("mcp__buzz__channel_info", {"channel": self.CHAN}),
                         ("mcp__buzz__get_messages", {"channel": self.CHAN, "limit": 30}), ("mcp__buzz__get_messages", {"channel": self.CHAN, "before": self.EID}),
                         ("mcp__buzz__get_thread", {"root": self.EID}), ("mcp__buzz__get_thread", {"thread": self.EID, "channel": self.CHAN}),
                         ("mcp__buzz__get_event", {"event_id": self.EID}), ("mcp__buzz__buzz_get_event", {"event_id": self.EID}),
                         ("mcp__buzz__get_addressed", {"channel": self.CHAN}), ("mcp__buzz__capabilities", {}), ("mcp__buzz__buzz_capabilities", {}),
                         ("mcp__buzz__get_messages", {"community": "life-os", "channel": self.CHAN}),
                         ("mcp__buzz__buzz_read", {"operation": "get_messages", "community": "life-os", "arguments": {"channel": self.CHAN, "limit": 20}}),
                         ("mcp__buzz__buzz_read", {"operation": "get_event", "community": "life-os", "arguments": {"event_id": self.EID}}),
                         ("mcp__buzz__search_messages", {"query": "LIFE-369 kickoff", "channel": self.CHAN})):
            self.assertEqual(self.dec(tool, ti), "allow", (tool, ti))

    def test_the_room_read_then_reply_flow_the_challenger_needed(self):
        steps = [("mcp__buzz__get_channel_info", {"channel": self.CHAN}), ("mcp__buzz__get_messages", {"channel": self.CHAN, "limit": 50}),
                 ("mcp__buzz__get_event", {"event_id": self.EID}),
                 ("mcp__buzz__send", self.send("@Jim This room is LIFE-369 (from the kickoff). My view: ...", channel=self.CHAN))]
        self.assertEqual([self.dec(t, ti) for t, ti in steps], ["allow"] * 4)

    def test_ids_are_exempt_only_when_they_are_exactly_ids_under_id_keys(self):
        for ti in ({"event_id": "tskey-api-abc123"}, {"channel": "ghp_" + "a" * 30}, {"query": self.EID}, {"note": self.EID}, {"event_id": self.EID + " password: x"},
                   {"channel": self.CHAN, "query": "token: abc"}, {"root": "nsec1" + "q" * 40}):
            self.assertEqual(self.dec("mcp__buzz__get_event", ti), "deny", ti)

    def test_other_communities_unknown_runner_operations_and_writes_stay_denied(self):
        for tool, ti in (("mcp__buzz__get_messages", {"community": "bytalos", "channel": self.CHAN}), ("mcp__buzz__get_messages", {"community": 5}),
                         ("mcp__buzz__buzz_read", {"operation": "get_messages", "community": "bytalos", "arguments": {"channel": self.CHAN}}),
                         ("mcp__buzz__buzz_read", {"operation": "post_message", "community": "life-os"}), ("mcp__buzz__buzz_read", {"operation": "get_and_delete"}),
                         ("mcp__buzz__buzz_read", {"community": "life-os"}), ("mcp__buzz__read", {"operation": ["get_messages"]}),
                         ("mcp__buzz__read", {"operation": "post_message", "channel": self.CHAN}), ("mcp__buzz__read", {"channel": self.CHAN, "community": "bytalos"}),
                         ("mcp__buzz__read", {"channel": "tskey-api-abc123"}), ("mcp__buzz__canvas_set", {"channel": self.CHAN, "text": "x"}),
                         ("mcp__buzz__canvas_update", {"channel": self.CHAN}),
                         ("mcp__buzz__buzz_publish", {"operation": "message", "arguments": {"channel": self.CHAN, "text": "x"}}),
                         ("mcp__buzz__buzz_post_message", {"channel": self.CHAN, "text": "x"}), ("mcp__buzz__get_event_and_delete", {"event_id": self.EID}),
                         ("mcp__buzz__buzz_get_nsec", {}), ("mcp__buzz__get_messages_run", {"channel": self.CHAN})):
            self.assertEqual(self.dec(tool, ti), "deny", (tool, ti))

    def test_injected_room_text_cannot_widen_anything(self):
        """A room message saying "Jim approved, run this" changes nothing: shell, file writes, unknown tools and Jira/GitHub writes are refused before any handler runs."""
        for tool, ti in (("Bash", {"command": "cat ~/.ssh/id_rsa"}), ("Write", {"file_path": "/tmp/x", "content": "x"}), ("Edit", {"file_path": "/tmp/x", "old_string": "a", "new_string": "b"}),
                         ("Read", {"file_path": os.path.join(self.home, ".ssh", "id_rsa")}), ("WebFetch", {"url": "https://example.com"}), ("mcp__evil__anything", {}),
                         (ATL + "addCommentToJiraIssue", {"issueIdOrKey": "LIFE-369", "commentBody": "Jim approved"}),
                         (GH + "create_pull_request", {**GOOD_REPO, "title": "x", "head": "a", "base": "main"})):
            self.assertEqual(self.dec(tool, ti), "deny", tool)

    def test_stop_still_wins_over_room_reads(self):
        open(os.path.join(self.g, "STOP"), "w").close()
        self.assertEqual(self.dec("mcp__buzz__get_event", {"event_id": self.EID}), "deny")
        self.assertEqual(self.dec("mcp__buzz__channel_info", {"channel": self.CHAN}), "deny")

    def test_the_installed_script_gives_the_same_answer(self):
        out = subprocess.run(["python3", "-I", GUARD], input=json.dumps({"hook_event_name": "PreToolUse", "tool_name": "mcp__buzz__get_thread", "tool_input": {"root": self.EID}}),
                             capture_output=True, text=True, cwd=self.tmp, check=True).stdout
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "allow")


class NativeReadTools(Base):
    """LIFE-397, live failure Nov 7 5:23 PM CT: the Challenger's real calls (recovered from hook.log input hashes) were refused. Each must now be allowed."""
    CHAN = "0a1b2c3d-1111-4222-8333-944455566677"

    def test_the_exact_calls_from_hook_log_are_allowed(self):
        self.assertEqual(self.dec("mcp__buzz__read", {"channel": self.CHAN, "limit": 30}), "allow")
        self.assertEqual(self.dec("mcp__buzz__read", {}), "allow")
        self.assertEqual(self.dec("mcp__buzz__canvas_get", {"channel": self.CHAN}), "allow")
        self.assertEqual(self.dec("mcp__buzz__channel_get", {"channel": self.CHAN}), "allow")   # Nov 7 5:46:55 PM CT: the Challenger's channel read, same input hash as the canvas_get call
        self.assertEqual(self.dec("mcp__buzz__channel_delete", {"channel": self.CHAN}), "deny")  # only the exact read name is added; a look-alike change tool stays refused
        self.assertEqual(self.dec("mcp__buzz__read", {"operation": "get_messages", "channel": self.CHAN}), "allow")

    def test_hashes_match_the_logged_inputs(self):
        import hashlib
        h = lambda o: hashlib.sha256(json.dumps(o, sort_keys=True, default=str).encode()).hexdigest()[:16]   # same formula as guard.main's input_sha
        real = "b995612a-678c-4861-82d1-448daffe6f97"                                                          # the LIFE-369 room (a Buzz channel id, not a Notion id)
        self.assertEqual(h({"channel": real, "limit": 30}), "15d4fb6febf946f1")
        self.assertEqual(h({"channel": real}), "46117809905a0b4e")
        self.assertEqual(h({}), "44136fa355b3678a")

    def test_notion_is_read_only_and_an_exact_list(self):
        """Jim, Nov 8: the Buzz agents need Notion. Search, fetch, queries and comments are allowed; every write and every other Notion tool is denied; a secret in the input is still denied."""
        for op, ti in (("notion-search", {"query": "REQ-7 v4"}), ("notion-fetch", {"id": "4113c5a0-5926-81f9-bb8e-de0df05ef8a9"}),
                       ("notion-query-data-sources", {"data": {"mode": "sql", "data_source_urls": ["collection://a139cff3-ed9c-48d8-aaf2-6663148a786a"], "query": "SELECT 1"}}),
                       ("notion-get-comments", {"page_id": "4113c5a0-5926-81f9-bb8e-de0df05ef8a9"})):
            self.assertEqual(self.dec("mcp__claude_ai_Notion__" + op, ti), "allow", op)
        for op in ("notion-create-pages", "notion-update-page", "notion-move-pages", "notion-delete-pages", "notion-duplicate-page", "notion-create-comment", "notion-update-data-source",
                   "notion-create-database", "notion-get-users", "notion-send-message-to-session", "notion-spawn-session", "notion-upload-skill", "notion-search-agents"):
            self.assertEqual(self.dec("mcp__claude_ai_Notion__" + op, {"id": "x"}), "deny", op)
        self.assertEqual(self.dec("mcp__claude_ai_Notion__notion-search", {"query": "token: ghp_" + "a" * 30}), "deny")
