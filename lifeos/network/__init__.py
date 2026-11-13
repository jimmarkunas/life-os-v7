"""Network Intelligence: import a LinkedIn connections file and match a job's employer to the stored network (docs/NETWORK_INTELLIGENCE_SCOPE.md)."""
# Imports `platform` only; the Jobs glue lives in `lifeos/sources/`. Counts only: no name, address, company or other row content is ever printed.

__all__ = ["errors", "identity", "importer", "inspect", "match", "parse", "source", "store", "surface"]
