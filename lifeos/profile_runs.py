"""LIFE-590 shim: this module moved to lifeos.sources.devtools.profile_runs. It keeps the old name working (`python -m lifeos.profile_runs`, `import lifeos.profile_runs`) until every caller, the Oracle service and the Jim-owned docs use the new one."""
import sys
from lifeos.sources.devtools import profile_runs as _moved

if __name__ == "__main__":
    sys.exit(_moved.main())
sys.modules[__name__] = _moved          # the old name IS the new module, so patches and attributes see one object
