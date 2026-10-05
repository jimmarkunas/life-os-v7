"""Fixed error codes only (public repo): never a name, a company, an address, a link or a response body."""


class NetworkError(RuntimeError):
    """The message is always one of: NETWORK_CONFIG_MISSING, NETWORK_NO_FILE, NETWORK_FILE_URL_REFUSED, NETWORK_FILE_TOO_LARGE,
    NETWORK_FILE_NETWORK, NETWORK_FILE_HTTP_<code>, NETWORK_NO_HEADER, NETWORK_PAGE_INCOMPLETE."""
