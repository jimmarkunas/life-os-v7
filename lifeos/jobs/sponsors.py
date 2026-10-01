"""UK Skilled Worker route evidence: is the ACTUAL employer on the Home Office register of licensed sponsors (Skilled Worker route)?

The register is loaded into `v7_sponsors` by lifeos.sources.sponsor_register. Lookup is by distinctive-token equality of the employer name
(lifeos.jobs.names), never a fuzzy score. States: POSITIVE (the employer is on the register), NEGATIVE (the register is loaded and the
employer is not on it), UNRESOLVED (no register loaded, an empty company, or a recruiter/intermediary whose own licence cannot qualify an
unresolved client employer, so the job goes to Review).
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
    """The register from Hostinger, or an empty one (every state UNRESOLVED) when it has not been loaded yet."""
    own = connection is None
    connection = connection or store.connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT name FROM v7_sponsors")
            return Register(r[0] for r in cursor.fetchall())
    finally:
        if own:
            connection.close()
