"""
tray/ipc_client.py
-------------------
IPC client used by the tray application to communicate with the
USB Blocker service (privileged daemon).

All public methods are synchronous and return parsed response data or
raise IPCError on failure.
"""

from __future__ import annotations

import socket
from typing import Any, List, Optional

from core import ipc


class IPCError(Exception):
    """Raised when the service returns an error response or is unreachable."""


class ServiceClient:
    """
    Thin IPC client for the USB Blocker daemon.

    Parameters
    ----------
    socket_path : Unix domain socket path (Linux/macOS) or loopback address
                  used by the IPC module's Windows fallback.
    timeout     : Socket timeout in seconds.
    """

    def __init__(self, socket_path: str, timeout: int = 5) -> None:
        self._socket_path = socket_path
        self._timeout = timeout

    # ------------------------------------------------------------------ #
    # Transport helpers                                                     #
    # ------------------------------------------------------------------ #

    def _call(self, cmd: str, **kwargs) -> Any:
        """Send a command and return the response data field."""
        msg = ipc.build_command(cmd, **kwargs)
        try:
            conn = ipc.create_client_socket(self._socket_path, timeout=self._timeout)
        except (ConnectionRefusedError, FileNotFoundError, OSError) as exc:
            raise IPCError(f"Cannot connect to USB Blocker service: {exc}") from exc

        try:
            ipc.send_message(conn, msg)
            response = ipc.recv_message(conn)
        finally:
            try:
                conn.close()
            except Exception:
                pass

        if not response.get("ok"):
            raise IPCError(response.get("error", "Unknown service error"))
        return response.get("data")

    # ------------------------------------------------------------------ #
    # High-level commands                                                   #
    # ------------------------------------------------------------------ #

    def get_status(self) -> dict:
        """
        Return service status dict:
        {blocking_enabled: bool, connected_count: int, pid: int}
        """
        return self._call("STATUS")

    def list_devices(self) -> List[dict]:
        """Return list of currently connected device dicts."""
        return self._call("LIST_DEVICES") or []

    def list_allowlist(self) -> List[dict]:
        """Return the full allow-list as a list of entry dicts."""
        return self._call("LIST_ALLOWLIST") or []

    def add_to_allowlist(self, key: str) -> bool:
        """
        Request the service to add the device identified by *key* to the
        allow-list.  The device must currently be connected.

        Returns True if a new entry was created, False if already present.
        Raises IPCError on failure.
        """
        result = self._call("ADD_ALLOWLIST", key=key)
        return bool(result.get("added", False))

    def remove_from_allowlist(self, key: str) -> bool:
        """
        Remove the allow-list entry for *key*.
        Returns True if deleted, False if it was not found.
        """
        result = self._call("REMOVE_ALLOWLIST", key=key)
        return bool(result.get("removed", False))

    def set_blocking(self, enabled: bool) -> bool:
        """Toggle global blocking on/off.  Returns new blocking_enabled value."""
        result = self._call("SET_BLOCKING", enabled=enabled)
        return bool(result.get("blocking_enabled", enabled))

    def list_events(self, limit: int = 50) -> List[dict]:
        """Return the most recent *limit* event log entries."""
        return self._call("LIST_EVENTS", limit=limit) or []

    def is_service_running(self) -> bool:
        """Return True if the service is reachable, False otherwise."""
        try:
            self.get_status()
            return True
        except IPCError:
            return False
