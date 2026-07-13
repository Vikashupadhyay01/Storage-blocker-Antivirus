"""
core/logger.py
--------------
Configures and returns the application-wide rotating logger.

Usage
-----
    from core.logger import setup_logging, log_event
    setup_logging(config)
    log_event("BLOCKED", device, action="unmounted")
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.config import Config
    from core.device import UsbDevice

_APP_LOGGER_NAME = "usb_blocker"


def setup_logging(config: "Config") -> logging.Logger:
    """
    Initialise the rotating file handler and (optionally) a stderr handler.

    Must be called once near program startup.  Returns the root application
    logger; all submodules should use ``logging.getLogger(__name__)`` which
    will inherit this configuration via the hierarchy.
    """
    log_path = config.log_path
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    root_logger = logging.getLogger(_APP_LOGGER_NAME)
    root_logger.setLevel(getattr(logging, config.log_level, logging.INFO))

    # Avoid duplicate handlers if setup_logging() is called more than once
    if root_logger.handlers:
        return root_logger

    fmt = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    # Rotating file handler
    try:
        fh = RotatingFileHandler(
            log_path,
            maxBytes=config.log_max_bytes,
            backupCount=config.log_backup_count,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        root_logger.addHandler(fh)
    except OSError as exc:
        # Log directory may not exist / not writable (e.g. in tests)
        print(f"[usb-blocker] WARNING: could not open log file {log_path!r}: {exc}", file=sys.stderr)

    # Console handler (useful when running in foreground / systemd journal)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root_logger.addHandler(sh)

    return root_logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a child logger of the application root logger."""
    if name:
        return logging.getLogger(f"{_APP_LOGGER_NAME}.{name}")
    return logging.getLogger(_APP_LOGGER_NAME)


def log_event(
    event: str,
    device: "UsbDevice",
    action: str = "",
    logger: Optional[logging.Logger] = None,
    extra: Optional[dict] = None,
) -> None:
    """
    Emit a structured JSON event log entry at INFO level.

    Parameters
    ----------
    event  : One of CONNECTED / ALLOWED / BLOCKED / DISCONNECTED / WARNING.
    device : The UsbDevice involved.
    action : Additional action descriptor, e.g. "unmounted", "power-off".
    logger : Logger to use; defaults to the app root logger.
    extra  : Any additional key-value pairs to include in the JSON record.
    """
    if logger is None:
        logger = get_logger("events")

    record: dict = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "device": device.to_dict(),
        "action": action,
    }
    if extra:
        record.update(extra)

    logger.info("EVENT %s | %s", event, json.dumps(record))
