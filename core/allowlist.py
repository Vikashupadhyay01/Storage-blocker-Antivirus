"""
core/allowlist.py
-----------------
Persistent allow-list backed by SQLite.

The allow-list lives in a protected system directory and can only be
modified by root / Administrator.  The tray app sends modification requests
to the privileged service via IPC; the service calls AllowList methods
directly.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, List, Optional

from core.device import UsbDevice

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Schema                                                                       #
# --------------------------------------------------------------------------- #

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS allowlist (
    key         TEXT    PRIMARY KEY,   -- "vendor_id:product_id:serial"
    vendor_id   TEXT    NOT NULL,
    product_id  TEXT    NOT NULL,
    serial      TEXT    NOT NULL DEFAULT '',
    name        TEXT    NOT NULL DEFAULT '',
    added_at    TEXT    NOT NULL,      -- ISO-8601 UTC timestamp
    added_by    TEXT    NOT NULL DEFAULT ''  -- username of who added it
);
"""

_CREATE_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS event_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    event       TEXT    NOT NULL,
    device_key  TEXT    NOT NULL,
    device_name TEXT    NOT NULL DEFAULT '',
    action      TEXT    NOT NULL DEFAULT ''
);
"""


class AllowList:
    """
    Thread-safe allow-list manager.

    Parameters
    ----------
    db_path : Absolute path to the SQLite database file.
              The directory must be writable by the service (root).
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._ensure_db()

    # ------------------------------------------------------------------ #
    # Database helpers                                                      #
    # ------------------------------------------------------------------ #

    def _ensure_db(self) -> None:
        """Create the database file and tables if they don't exist."""
        db_dir = os.path.dirname(self._db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE)
            conn.execute(_CREATE_LOG_TABLE)
            conn.commit()
        logger.debug("AllowList DB ready at %s", self._db_path)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self._db_path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------ #
    # Public API                                                            #
    # ------------------------------------------------------------------ #

    def is_allowed(self, device: UsbDevice) -> bool:
        """
        Return True if *device* is on the allow-list.

        Matching is by the composite key (vendor_id:product_id:serial).
        """
        key = device.allowlist_key()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM allowlist WHERE key = ?", (key,)
            ).fetchone()
        return row is not None

    def add_device(self, device: UsbDevice, added_by: str = "") -> bool:
        """
        Add *device* to the allow-list.

        Returns True if a new entry was created, False if already present.
        Raises PermissionError if the caller is not root/admin.
        """
        _require_admin()
        key = device.allowlist_key()
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO allowlist (key, vendor_id, product_id, serial, name, added_at, added_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (key, device.vendor_id, device.product_id,
                     device.serial, device.name, now, added_by),
                )
                conn.commit()
            logger.info("AllowList: added %s (%s)", device.name, key)
            return True
        except sqlite3.IntegrityError:
            logger.debug("AllowList: %s already present", key)
            return False

    def remove_device(self, key: str) -> bool:
        """
        Remove the allow-list entry with *key*.

        Returns True if an entry was deleted, False if not found.
        Raises PermissionError if the caller is not root/admin.
        """
        _require_admin()
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM allowlist WHERE key = ?", (key,))
            conn.commit()
        deleted = cursor.rowcount > 0
        if deleted:
            logger.info("AllowList: removed entry %s", key)
        else:
            logger.debug("AllowList: remove_device — key %s not found", key)
        return deleted

    def list_entries(self) -> List[dict]:
        """Return all allow-list entries as a list of dicts."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT key, vendor_id, product_id, serial, name, added_at, added_by "
                "FROM allowlist ORDER BY added_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_entry(self, key: str) -> Optional[dict]:
        """Return a single allow-list entry or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT key, vendor_id, product_id, serial, name, added_at, added_by "
                "FROM allowlist WHERE key = ?",
                (key,),
            ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------ #
    # Event log                                                             #
    # ------------------------------------------------------------------ #

    def log_event(
        self,
        event: str,
        device: UsbDevice,
        action: str = "",
    ) -> None:
        """Persist a structured event to the SQLite event_log table."""
        ts = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO event_log (ts, event, device_key, device_name, action) "
                "VALUES (?, ?, ?, ?, ?)",
                (ts, event, device.allowlist_key(), device.name, action),
            )
            conn.commit()

    def list_events(self, limit: int = 200) -> List[dict]:
        """Return the most recent *limit* event log entries."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, ts, event, device_key, device_name, action "
                "FROM event_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


# --------------------------------------------------------------------------- #
# Privilege guard                                                              #
# --------------------------------------------------------------------------- #

def _require_admin() -> None:
    """
    Raise PermissionError if the current process is not running as
    root (Linux/macOS) or Administrator (Windows).
    """
    if sys.platform == "win32":
        try:
            import ctypes
            if not ctypes.windll.shell32.IsUserAnAdmin():  # type: ignore[attr-defined]
                raise PermissionError(
                    "Allow-list modification requires Administrator privileges."
                )
        except AttributeError:
            pass  # Safety: if we can't check, assume OK (service is always admin)
    else:
        if os.geteuid() != 0:  # type: ignore[attr-defined]
            raise PermissionError(
                "Allow-list modification requires root privileges."
            )
