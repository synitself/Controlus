"""Re-export of common/controlus_backend.py, the shared device protocol code.

Kept as a module so existing imports (`controlus.backend`) keep working; the
real implementation lives in one place for Windows, Linux and the GNOME helper.
"""

import os as _os
import sys as _sys

_here = _os.path.dirname(_os.path.abspath(__file__))
for _p in (_here, _os.path.join(_here, "..", "common"), _os.path.join(_here, "..", "..", "common")):
    _p = _os.path.normpath(_p)
    if _os.path.isfile(_os.path.join(_p, "controlus_backend.py")) and _p not in _sys.path:
        _sys.path.insert(0, _p)
        break

import controlus_backend as _impl  # noqa: E402

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
