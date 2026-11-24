"""LIFE-876: the Agent Inbox writer moved to lifeos.platform.agent_inbox (the governed MCP shares it); this module name keeps working."""
import sys

from lifeos.platform import agent_inbox

sys.modules[__name__] = agent_inbox
