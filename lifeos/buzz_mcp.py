"""LIFE-590 shim: this module moved to lifeos.buzz.mcp. It keeps the old name working (`python -m lifeos.buzz_mcp`, `import lifeos.buzz_mcp`) until every caller, the Oracle service and the Jim-owned docs use the new one."""
import sys
from lifeos.buzz import mcp as _moved

if __name__ == "__main__":
    _moved.main()
sys.modules[__name__] = _moved          # the old name IS the new module, so patches and attributes see one object
