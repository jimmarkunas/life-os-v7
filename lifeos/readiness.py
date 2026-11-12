"""LIFE-590 shim: this module moved to lifeos.sources.devtools.readiness. It keeps the old name working (`python -m lifeos.readiness`, `import lifeos.readiness`) until every caller, the Oracle service and the Jim-owned docs use the new one."""
import sys
from lifeos.sources.devtools import readiness as _moved

if __name__ == "__main__":
    sys.exit(_moved.run(sys.argv[1:]))
sys.modules[__name__] = _moved          # the old name IS the new module, so patches and attributes see one object
