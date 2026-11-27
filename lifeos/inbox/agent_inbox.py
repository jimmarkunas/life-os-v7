"""Compatibility alias: the Agent Inbox writer lives in lifeos.platform.agent_inbox (shared with the governed MCP)."""
import sys

from lifeos.platform import agent_inbox

sys.modules[__name__] = agent_inbox
