"""Fixed error codes only (public repo): never a name, a company, an address, a link or a response body."""


class NetworkError(RuntimeError):
    """Message is always a fixed NETWORK_* code."""
