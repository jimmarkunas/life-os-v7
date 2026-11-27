"""A fake source reader opener for the inbox tests: opener(rows, error) returns open_reader(credential_ref), whose reader returns rows or raises error."""


def opener(rows, error=None):
    def open_reader(credential_ref):
        def read(*args):
            if error:
                raise error
            return rows

        return read

    return open_reader
