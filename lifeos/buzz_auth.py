"""LIFE-590 shim: this module moved to lifeos.buzz.auth. It keeps the old name working (`python -m lifeos.buzz_auth`, `import lifeos.buzz_auth`) until every caller, the Oracle service and the Jim-owned docs use the new one."""
import sys
from lifeos.buzz import auth as _moved

sys.modules[__name__] = _moved          # the old name IS the new module, so patches and attributes see one object
