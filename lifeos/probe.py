"""LIFE-590 shim: this module moved to lifeos.sources.devtools.probe. It keeps the old name working (`python -m lifeos.probe`, `import lifeos.probe`) until every caller, the Oracle service and the Jim-owned docs use the new one."""
import sys
from lifeos.sources.devtools import probe as _moved

sys.modules[__name__] = _moved          # the old name IS the new module, so patches and attributes see one object
