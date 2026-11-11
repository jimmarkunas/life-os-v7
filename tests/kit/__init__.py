"""Shared fakes for the platform boundaries (database, mailbox). No job or source knowledge: any OS can use them.

Rule (LIFE-551): a new fake class inside a test file needs a one-line comment saying why the kit fake here does not fit (scripted answers per test are a good reason; a copy of a recorder cursor is not).
The database recorder is `RecorderConn`, the one-row store behind the stuck-sync watches is `SingleRowDb`."""
