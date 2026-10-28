"""The switches that stop HyperFrames from reporting usage, in the one place every HyperFrames call reads them.

The HyperFrames CLI sends anonymous usage counts unless HYPERFRAMES_NO_TELEMETRY or DO_NOT_TRACK is set. The kit
sets both on every HyperFrames call it makes: `python -m kit.media run` (the UI story render, lint and snapshot),
the mini me renderer, the UI story test clip and the version check in bin/doctor.py.

bin/doctor.py reads this file with the Python that comes with a Mac, before the kit's own environment exists, so
the file uses only the standard library and runs on Python 3.9.
"""

from __future__ import annotations

import os
from typing import Dict, Mapping, Optional

OFF = {"HYPERFRAMES_NO_TELEMETRY": "1", "DO_NOT_TRACK": "1"}


def env(base: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """A copy of `base` (this process's own environment when it is None) with both switches on."""
    return {**(os.environ if base is None else base), **OFF}
