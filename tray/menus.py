"""
tray/menus.py
--------------
Builds the pystray dynamic menu from live service data.

The menu is rebuilt every poll cycle (every 5 s) via pystray's
dynamic menu support (callable menu items).
"""

from __future__ import annotations

from typing import Callable, List, Optional

import pystray  # type: ignore[import]

from tray.ipc_client import IPCError, ServiceClient


def build_menu(
    client: ServiceClient,
    on_add_allowlist: Callable[[str], None],
    on_remove_allowlist: Callable[[str], None],
    on_toggle_blocking: Callable[[bool], None],
    on_quit: Callable[[], None],
    state: dict,   # mutable dict shared with app.py to hold cached data
) -> pystray.Menu:
    """
    Return a pystray.Menu built from the current service state.

    Parameters
    ----------
    client               : IPC client connected to the service.
    on_add_allowlist     : Called with device key when user selects "Allow".
    on_remove_allowlist  : Called with device key when user selects "Remove".
    on_toggle_blocking   : Called with new boolean when user toggles blocking.
    on_quit              : Called when user selects "Quit".
    state                : Shared mutable dict updated by the poll loop.
    """

    def _status_item():
        if not state.get("service_running"):
            return pystray.MenuItem("⚠  Service not running", None, enabled=False)
        blocking = state.get("blocking_enabled", True)
        count    = state.get("connected_count", 0)
        label    = f"{'🔒 Protected' if blocking else '🔓 Blocking OFF'} — {count} device(s)"
        return pystray.MenuItem(label, None, enabled=False)

    def _blocking_toggle():
        enabled = state.get("blocking_enabled", True)
        label   = "Disable blocking" if enabled else "Enable blocking"
        def _toggle(_icon, _item):
            on_toggle_blocking(not enabled)
        return pystray.MenuItem(label, _toggle)

    def _device_submenu():
        devices: List[dict] = state.get("devices", [])
        if not devices:
            return pystray.MenuItem("No devices connected", None, enabled=False)

        items = []
        for dev in devices:
            name   = dev.get("name", "Unknown Device")
            key    = dev.get("vendor_id","") + ":" + dev.get("product_id","") + ":" + dev.get("serial","")
            status = dev.get("status", "unknown")
            label  = f"{'✓' if status == 'allowed' else '✗'} {name}"

            def _add_action(_k=key):
                def _inner(_icon, _item):
                    on_add_allowlist(_k)
                return _inner

            sub = pystray.Menu(
                pystray.MenuItem(f"Status: {status}", None, enabled=False),
                pystray.MenuItem(f"Key: {key}", None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Add to allow-list", _add_action()),
            )
            items.append(pystray.MenuItem(label, sub))

        return pystray.Menu(*items)

    def _allowlist_submenu():
        entries: List[dict] = state.get("allowlist", [])
        if not entries:
            return pystray.MenuItem("Allow-list is empty", None, enabled=False)

        items = []
        for entry in entries:
            key  = entry.get("key", "")
            name = entry.get("name", key)

            def _remove_action(_k=key):
                def _inner(_icon, _item):
                    on_remove_allowlist(_k)
                return _inner

            sub = pystray.Menu(
                pystray.MenuItem(f"Key: {key}", None, enabled=False),
                pystray.MenuItem(f"Added: {entry.get('added_at','')[:10]}", None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Remove from allow-list", _remove_action()),
            )
            items.append(pystray.MenuItem(name, sub))

        return pystray.Menu(*items)

    return pystray.Menu(
        pystray.MenuItem("USB Blocker", None, enabled=False),
        pystray.Menu.SEPARATOR,
        _status_item(),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Connected Devices", _device_submenu()),
        pystray.MenuItem("Allow-list", _allowlist_submenu()),
        pystray.Menu.SEPARATOR,
        _blocking_toggle(),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", lambda _icon, _item: on_quit()),
    )
