"""The one error of the Agent Inbox writer, shared by the inbox package and the governed MCP."""


class InboxError(ValueError):
    """`reason` is a fixed word such as `workspace_not_released`; the message is the reason and nothing else."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason
