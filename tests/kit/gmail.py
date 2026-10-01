class FakeGmail:
    """Synthetic mailbox. `results` maps a search query to ids (sweep); `messages` maps id -> (sender, html, epoch) (ingest)."""

    def __init__(self, results=None, messages=None):
        self.results, self.messages, self.calls = results or {}, messages or {}, []

    def label_id(self, name, create=False):
        return "LBL"

    def list_ids(self, query, limit=5000):
        return list(self.results.get(query, [])) if self.results else list(self.messages)[:limit]

    def message(self, message_id):
        return self.messages[message_id]

    def relabel(self, ids, add=(), remove=()):
        self.calls.append((list(ids), list(add), list(remove)))
