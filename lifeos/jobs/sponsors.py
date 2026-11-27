"""UK Skilled Worker route evidence: is the ACTUAL employer on the Home Office register of licensed sponsors?

The register is loaded into `v7_sponsors` by lifeos.sources.discovery.sponsor_register. Lookup is by distinctive-token
equality of the employer name (lifeos.jobs.names), never a fuzzy score. States: POSITIVE (on the register), NEGATIVE
(register loaded, employer not on it), UNRESOLVED (no register, an empty company, or a recruiter/intermediary).
"""
from lifeos.jobs import names, store
from lifeos.jobs.lanes import NEGATIVE, POSITIVE, UNRESOLVED

ROUTE = "Skilled Worker"


class Register:
    def __init__(self, entries):
        self.index = {}
        for name in entries:
            c = names.core(name)
            if c:
                self.index.setdefault(c[0], set()).add(tuple(c))

    def __len__(self):
        return sum(len(v) for v in self.index.values())

    def state(self, company):
        if not len(self) or not company or names.is_intermediary(company):
            return UNRESOLVED
        c = names.core(company)
        if not c or c[0] in names.GENERIC:
            return UNRESOLVED
        return POSITIVE if tuple(c) in self.index.get(c[0], ()) else NEGATIVE


def load(connection=None):
    """The register from the store, or an empty one (every state UNRESOLVED) when it has not been loaded yet.

    store.connect() is a context manager (tunnel and connection together), so always use it with `with`.
    """
    if connection is not None:
        with connection.cursor() as cursor:
            cursor.execute("SELECT name FROM v7_sponsors")
            return Register(r[0] for r in cursor.fetchall())
    with store.connect() as opened, opened.cursor() as cursor:
        cursor.execute("SELECT name FROM v7_sponsors")
        return Register(r[0] for r in cursor.fetchall())
