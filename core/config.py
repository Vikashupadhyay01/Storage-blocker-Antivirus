"""
core/config.py
--------------
Config loader and validator.

Merges the shipped default_config.yaml with an optional site-level config
file.  Returns a typed Config object consumed by the service and tray app.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

# --------------------------------------------------------------------------- #
# Platform-specific default paths                                              #
# --------------------------------------------------------------------------- #

def _default_config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        return base / "UsbBlocker"
    elif sys.platform == "darwin":
        return Path("/Library/Application Support/usb-blocker")
    else:
        return Path("/etc/usb-blocker")


def _default_log_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        return base / "UsbBlocker" / "logs"
    elif sys.platform == "darwin":
        return Path("/Library/Logs/usb-blocker")
    else:
        return Path("/var/log/usb-blocker")


def _default_run_dir() -> Path:
    if sys.platform == "win32":
        return Path(r"\\.\pipe")       # Named pipe root
    elif sys.platform == "darwin":
        return Path("/var/run/usb-blocker")
    else:
        return Path("/run/usb-blocker")


# --------------------------------------------------------------------------- #
# Config schema / defaults                                                     #
# --------------------------------------------------------------------------- #

DEFAULT_CONFIG: dict[str, Any] = {
    "blocking_enabled": True,
    "ipc": {
        "socket_path": str(_default_run_dir() / "service.sock"),
        "pipe_name": r"\\.\pipe\usb-blocker",   # Windows only
        "timeout_seconds": 5,
    },
    "log": {
        "path": str(_default_log_dir() / "usb-blocker.log"),
        "max_bytes": 10 * 1024 * 1024,   # 10 MB
        "backup_count": 5,
        "level": "INFO",
    },
    "allowlist_db": str(_default_config_dir() / "allowlist.db"),
    "udev_rules_dir": "/etc/udev/rules.d",    # Linux only
    "udev_rules_file": "99-usb-blocker.rules",# Linux only
}


class Config:
    """
    Typed, dot-access wrapper around the merged configuration dictionary.

    Instantiate via Config.load() rather than calling __init__ directly.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    # -- top-level -----------------------------------------------------------

    @property
    def blocking_enabled(self) -> bool:
        return bool(self._data.get("blocking_enabled", True))

    @blocking_enabled.setter
    def blocking_enabled(self, value: bool) -> None:
        self._data["blocking_enabled"] = bool(value)

    # -- ipc -----------------------------------------------------------------

    @property
    def ipc_socket_path(self) -> str:
        return self._data["ipc"]["socket_path"]

    @property
    def ipc_pipe_name(self) -> str:
        return self._data["ipc"]["pipe_name"]

    @property
    def ipc_timeout(self) -> int:
        return int(self._data["ipc"].get("timeout_seconds", 5))

    # -- log -----------------------------------------------------------------

    @property
    def log_path(self) -> str:
        return self._data["log"]["path"]

    @property
    def log_max_bytes(self) -> int:
        return int(self._data["log"].get("max_bytes", 10 * 1024 * 1024))

    @property
    def log_backup_count(self) -> int:
        return int(self._data["log"].get("backup_count", 5))

    @property
    def log_level(self) -> str:
        return self._data["log"].get("level", "INFO").upper()

    # -- allowlist -----------------------------------------------------------

    @property
    def allowlist_db(self) -> str:
        return self._data["allowlist_db"]

    # -- linux ---------------------------------------------------------------

    @property
    def udev_rules_dir(self) -> str:
        return self._data.get("udev_rules_dir", "/etc/udev/rules.d")

    @property
    def udev_rules_file(self) -> str:
        return self._data.get("udev_rules_file", "99-usb-blocker.rules")

    @property
    def udev_rules_path(self) -> str:
        return os.path.join(self.udev_rules_dir, self.udev_rules_file)

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict:
        return dict(self._data)

    # -- class methods -------------------------------------------------------

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Config":
        """
        Load configuration.

        Starts with DEFAULT_CONFIG and deep-merges the YAML file at *path*
        (if provided and it exists).  If yaml is not installed, only the
        built-in defaults are used.

        Parameters
        ----------
        path : Path to a YAML config file.  If None, looks for
               ``<config_dir>/config.yaml`` automatically.
        """
        merged = _deep_merge({}, DEFAULT_CONFIG)

        if path is None:
            path = str(_default_config_dir() / "config.yaml")

        if yaml is not None and os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                user_data = yaml.safe_load(fh) or {}
            merged = _deep_merge(merged, user_data)

        return cls(merged)

    def save(self, path: Optional[str] = None) -> None:
        """Persist current config to a YAML file."""
        if yaml is None:
            raise RuntimeError("PyYAML is not installed; cannot save config.")
        if path is None:
            path = str(_default_config_dir() / "config.yaml")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(self._data, fh, default_flow_style=False)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base* and return the result."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
