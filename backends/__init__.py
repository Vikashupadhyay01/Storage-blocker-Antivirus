"""
backends/__init__.py
---------------------
Auto-selects the correct platform backend based on sys.platform.

Usage
-----
    from backends import get_backend
    backend = get_backend()   # returns the right BaseBackend subclass instance
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.interface import BaseBackend


def get_backend(**kwargs) -> "BaseBackend":
    """
    Return an instantiated backend appropriate for the current OS.

    Raises RuntimeError on unsupported platforms.
    """
    if sys.platform.startswith("linux"):
        from backends.linux_backend import LinuxBackend
        return LinuxBackend(**kwargs)
    elif sys.platform == "win32":
        from backends.windows_backend import WindowsBackend
        return WindowsBackend(**kwargs)
    elif sys.platform == "darwin":
        from backends.macos_backend import MacOSBackend
        return MacOSBackend(**kwargs)
    else:
        raise RuntimeError(f"Unsupported platform: {sys.platform!r}")
