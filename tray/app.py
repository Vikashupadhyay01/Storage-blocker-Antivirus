"""
tray/app.py
------------
System-tray entry-point.

Runs in the user session (no root required).  Communicates with the
privileged USB Blocker service via IPC.

Usage
-----
    python -m tray.app
    # or directly:
    python tray/app.py
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pystray  # type: ignore[import]

from core.config import Config
from tray.icon import IconState, create_icon
from tray.ipc_client import IPCError, ServiceClient
from tray.menus import build_menu

logger = logging.getLogger(__name__)

POLL_INTERVAL = 5   # seconds between service status refreshes


class TrayApp:
    """USB Blocker system-tray application."""

    def __init__(self, config: Config) -> None:
        self._config  = config
        self._client  = ServiceClient(
            socket_path=config.ipc_socket_path,
            timeout=config.ipc_timeout,
        )
        self._state: dict = {
            "service_running":  False,
            "blocking_enabled": True,
            "connected_count":  0,
            "devices":          [],
            "allowlist":        [],
        }
        self._icon: pystray.Icon = self._make_icon()
        self._poll_thread: threading.Thread | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle                                                             #
    # ------------------------------------------------------------------ #

    def run(self) -> None:
        """Start the poll thread and hand control to pystray."""
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="usb-tray-poll"
        )
        self._poll_thread.start()
        logger.info("Tray app started")
        self._icon.run()

    def quit(self) -> None:
        logger.info("Tray app quitting")
        self._icon.stop()

    # ------------------------------------------------------------------ #
    # Icon / menu construction                                              #
    # ------------------------------------------------------------------ #

    def _make_icon(self) -> pystray.Icon:
        state = IconState.ERROR
        img   = create_icon(state)
        menu  = self._build_menu()
        return pystray.Icon(
            name="USB Blocker",
            icon=img,
            title="USB Blocker",
            menu=menu,
        )

    def _build_menu(self) -> pystray.Menu:
        return build_menu(
            client=self._client,
            on_add_allowlist=self._add_to_allowlist,
            on_remove_allowlist=self._remove_from_allowlist,
            on_toggle_blocking=self._toggle_blocking,
            on_quit=self.quit,
            state=self._state,
        )

    # ------------------------------------------------------------------ #
    # Poll loop                                                             #
    # ------------------------------------------------------------------ #

    def _poll_loop(self) -> None:
        """Refresh service state every POLL_INTERVAL seconds."""
        while True:
            self._refresh_state()
            time.sleep(POLL_INTERVAL)

    def _refresh_state(self) -> None:
        try:
            status   = self._client.get_status()
            devices  = self._client.list_devices()
            allowlist = self._client.list_allowlist()

            self._state["service_running"]  = True
            self._state["blocking_enabled"] = status.get("blocking_enabled", True)
            self._state["connected_count"]  = status.get("connected_count", 0)
            self._state["devices"]          = devices
            self._state["allowlist"]        = allowlist

            icon_state = (
                IconState.PROTECTED if self._state["blocking_enabled"]
                else IconState.WARNING
            )
        except IPCError:
            self._state["service_running"] = False
            icon_state = IconState.ERROR

        # Update icon image and menu
        self._icon.icon  = create_icon(icon_state)
        self._icon.menu  = self._build_menu()
        self._icon.title = self._make_tooltip()

    def _make_tooltip(self) -> str:
        if not self._state["service_running"]:
            return "USB Blocker — Service not running"
        blocking = self._state["blocking_enabled"]
        count    = self._state["connected_count"]
        return (
            f"USB Blocker — {'Protected' if blocking else 'Blocking OFF'}"
            f" | {count} device(s)"
        )

    # ------------------------------------------------------------------ #
    # Action handlers (called from menu)                                    #
    # ------------------------------------------------------------------ #

    def _add_to_allowlist(self, key: str) -> None:
        try:
            added = self._client.add_to_allowlist(key)
            msg = f"Device added to allow-list." if added else "Already in allow-list."
            logger.info("add_to_allowlist(%s): %s", key, msg)
            self._show_notification("Allow-list updated", msg)
            self._refresh_state()
        except IPCError as exc:
            self._show_notification("Error", str(exc))

    def _remove_from_allowlist(self, key: str) -> None:
        try:
            removed = self._client.remove_from_allowlist(key)
            msg = "Entry removed." if removed else "Entry not found."
            logger.info("remove_from_allowlist(%s): %s", key, msg)
            self._show_notification("Allow-list updated", msg)
            self._refresh_state()
        except IPCError as exc:
            self._show_notification("Error", str(exc))

    def _toggle_blocking(self, enabled: bool) -> None:
        try:
            new_state = self._client.set_blocking(enabled)
            status = "enabled" if new_state else "disabled"
            logger.info("Blocking %s via tray", status)
            self._show_notification("USB Blocker", f"Blocking {status}.")
            self._refresh_state()
        except IPCError as exc:
            self._show_notification("Error", str(exc))

    def _show_notification(self, title: str, message: str) -> None:
        """Display a desktop notification if the platform supports it."""
        try:
            self._icon.notify(message, title)
        except Exception:
            logger.debug("Notification not supported: %s — %s", title, message)


# --------------------------------------------------------------------------- #
# Entry-point                                                                   #
# --------------------------------------------------------------------------- #

def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = Config.load()
    app = TrayApp(config)
    app.run()


if __name__ == "__main__":
    main()
