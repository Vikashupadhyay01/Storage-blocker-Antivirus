"""
service/daemon.py
-----------------
Main service entry-point (platform-agnostic).

The OS-specific wrappers (linux_daemon.py, windows_service.py,
macos_daemon.py) all call UsbBlockerDaemon.run() after setting up
platform-specific lifecycle hooks (systemd notify, launchd keepalive, etc.).

Responsibilities
----------------
1. Load config and initialise logging.
2. Perform an initial device scan (catch pre-connected devices).
3. Start event-driven monitoring via the platform backend.
4. On each connect: check allow-list → block or allow + log.
5. Start the IPC server thread so the tray app can query status and
   request allow-list modifications.
6. Handle SIGTERM / SIGINT for clean shutdown.
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import threading
from typing import Optional

from backends import get_backend
from core.allowlist import AllowList
from core.config import Config
from core.device import DeviceStatus, UsbDevice
from core import ipc
from core.logger import log_event, setup_logging

logger = logging.getLogger(__name__)


class UsbBlockerDaemon:
    """Main service controller."""

    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config or Config.load()
        self._stop_event = threading.Event()
        self._allowlist: Optional[AllowList] = None
        self._backend = None
        self._ipc_thread: Optional[threading.Thread] = None
        self._server_sock: Optional[socket.socket] = None

    # ------------------------------------------------------------------ #
    # Public lifecycle                                                      #
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Initialise all subsystems and enter the monitoring loop."""
        print("[usb-blocker] ═══════════════════════════════════════════════", flush=True)
        print("[usb-blocker] USB Blocker daemon starting up…", flush=True)

        # Logging
        setup_logging(self.config)
        logger.info("USB Blocker daemon starting (pid=%d)", os.getpid())
        print(f"[usb-blocker] PID {os.getpid()} | blocking_enabled={self.config.blocking_enabled}",
              flush=True)

        # Allow-list
        self._allowlist = AllowList(self.config.allowlist_db)
        print(f"[usb-blocker] Allow-list DB: {self.config.allowlist_db}", flush=True)

        # Backend
        extra = {}
        if sys.platform.startswith("linux"):
            extra["udev_rules_path"] = self.config.udev_rules_path
        self._backend = get_backend(**extra)

        # Install udev rule on Linux at each service start
        if sys.platform.startswith("linux"):
            self._backend.install_udev_rule()  # type: ignore[attr-defined]

        # Initial scan (catch pre-connected devices)
        print("[usb-blocker] Performing initial device scan…", flush=True)
        logger.info("Performing initial device scan…")
        initial = list(self._backend.detect_devices())
        print(f"[usb-blocker] Initial scan complete: {len(initial)} USB block device(s) found",
              flush=True)
        for device in initial:
            self._on_connect(device)

        # Start event monitoring
        self._backend.start_monitoring(
            on_connect=self._on_connect,
            on_disconnect=self._on_disconnect,
        )

        # Start IPC server
        self._start_ipc_server()

        print("[usb-blocker] ───────────────────────────────────────────────", flush=True)
        print("[usb-blocker] Daemon started — monitoring for USB devices.", flush=True)
        print("[usb-blocker] Plug in a USB drive to test.  Press Ctrl-C to stop.",
              flush=True)
        print("[usb-blocker] ═══════════════════════════════════════════════", flush=True)
        logger.info("USB Blocker daemon running — blocking_enabled=%s",
                    self.config.blocking_enabled)

    def stop(self) -> None:
        """Signal the daemon to stop and clean up."""
        logger.info("USB Blocker daemon stopping…")
        self._stop_event.set()
        if self._backend:
            self._backend.stop_monitoring()
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
        logger.info("USB Blocker daemon stopped")

    def wait(self) -> None:
        """Block until stop() is called (used by Linux/macOS daemons)."""
        self._stop_event.wait()

    def run(self) -> None:
        """start() + wait() — convenience method for OS-native wrappers."""
        self.start()
        self.wait()

    # ------------------------------------------------------------------ #
    # Device event callbacks                                                #
    # ------------------------------------------------------------------ #

    def _on_connect(self, device: UsbDevice) -> None:
        """Called by the backend whenever a USB mass-storage device connects."""
        print(
            f"[usb-blocker] CONNECTED: {device.name!r}  "
            f"vid={device.vendor_id}  pid={device.product_id}  "
            f"serial={device.serial!r}  path={device.device_path}",
            flush=True,
        )
        logger.info("Device connected: %s (key=%s)", device.name, device.allowlist_key())
        log_event("CONNECTED", device, logger=logger)
        self._allowlist.log_event("CONNECTED", device)

        if not self.config.blocking_enabled:
            device.status = DeviceStatus.ALLOWED
            print(f"[usb-blocker] DECISION: ALLOWED (blocking disabled globally) — {device.name!r}",
                  flush=True)
            logger.info("Blocking disabled globally — allowing %s", device.name)
            log_event("ALLOWED", device, action="blocking_disabled", logger=logger)
            self._allowlist.log_event("ALLOWED", device, action="blocking_disabled")
            return

        if not device.is_identified():
            # Fail-safe: if we can't identify, block and warn
            print(
                f"[usb-blocker] DECISION: BLOCKED (fail-safe — device unidentified) "
                f"— path={device.device_path}",
                flush=True,
            )
            logger.warning(
                "Device %s could not be fully identified — blocking (fail-safe)",
                device.device_path,
            )
            device.status = DeviceStatus.BLOCKED
            self._block(device, action="fail_safe_block")
            return

        if self._allowlist.is_allowed(device):
            device.status = DeviceStatus.ALLOWED
            print(f"[usb-blocker] DECISION: ALLOWED (allow-list match) — {device.name!r}",
                  flush=True)
            logger.info("Allowed device: %s", device.name)
            log_event("ALLOWED", device, action="allowlist_match", logger=logger)
            self._allowlist.log_event("ALLOWED", device, action="allowlist_match")
        else:
            device.status = DeviceStatus.BLOCKED
            print(f"[usb-blocker] DECISION: BLOCKED (not in allow-list) — {device.name!r}",
                  flush=True)
            logger.warning("Blocking unlisted device: %s", device.name)
            self._block(device, action="not_in_allowlist")

    def _on_disconnect(self, device: UsbDevice) -> None:
        print(f"[usb-blocker] DISCONNECTED: {device.name!r}  path={device.device_path}",
              flush=True)
        logger.info("Device disconnected: %s", device.name)
        log_event("DISCONNECTED", device, logger=logger)
        self._allowlist.log_event("DISCONNECTED", device, action="removed")

    def _block(self, device: UsbDevice, action: str = "") -> None:
        self._backend.block_device(device)
        log_event("BLOCKED", device, action=action, logger=logger)
        self._allowlist.log_event("BLOCKED", device, action=action)

    # ------------------------------------------------------------------ #
    # IPC server                                                            #
    # ------------------------------------------------------------------ #

    def _start_ipc_server(self) -> None:
        try:
            self._server_sock = ipc.create_server_socket(self.config.ipc_socket_path)
            self._server_sock.listen(5)
        except Exception as exc:
            logger.error("IPC server could not bind: %s", exc)
            return

        self._ipc_thread = threading.Thread(
            target=self._ipc_loop,
            daemon=True,
            name="usb-blocker-ipc-server",
        )
        self._ipc_thread.start()
        logger.info("IPC server listening at %s", self.config.ipc_socket_path)

    def _ipc_loop(self) -> None:
        """Accept IPC connections and dispatch commands."""
        while not self._stop_event.is_set():
            try:
                self._server_sock.settimeout(1.0)
                try:
                    client, _ = self._server_sock.accept()
                except socket.timeout:
                    continue
                t = threading.Thread(
                    target=self._handle_client,
                    args=(client,),
                    daemon=True,
                )
                t.start()
            except OSError:
                # Socket was closed by stop()
                break

    def _handle_client(self, client: socket.socket) -> None:
        """Handle a single IPC client connection."""
        try:
            msg = ipc.recv_message(client)
            cmd = msg.get("cmd", "")
            response = self._dispatch(cmd, msg)
            ipc.send_message(client, response)
        except Exception as exc:
            logger.debug("IPC client error: %s", exc)
            try:
                ipc.send_message(client, ipc.build_error(str(exc)))
            except Exception:
                pass
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _dispatch(self, cmd: str, msg: dict) -> dict:
        """Route an IPC command to the appropriate handler."""
        if cmd == "STATUS":
            return ipc.build_ok({
                "blocking_enabled": self.config.blocking_enabled,
                "connected_count": len(self._backend.list_connected_devices()),
                "pid": os.getpid(),
            })

        elif cmd == "LIST_DEVICES":
            devices = [d.to_dict() for d in self._backend.list_connected_devices()]
            return ipc.build_ok(devices)

        elif cmd == "LIST_ALLOWLIST":
            return ipc.build_ok(self._allowlist.list_entries())

        elif cmd == "ADD_ALLOWLIST":
            key = msg.get("key", "")
            if not key:
                return ipc.build_error("Missing 'key' field")
            # Find the device in connected list
            device = self._find_connected_by_key(key)
            if device is None:
                return ipc.build_error(f"No connected device with key {key!r}")
            try:
                added = self._allowlist.add_device(device, added_by="tray_user")
                if added:
                    # Unblock immediately if currently connected
                    self._backend.unblock_device(device)
                return ipc.build_ok({"added": added})
            except PermissionError as exc:
                return ipc.build_error(str(exc))

        elif cmd == "REMOVE_ALLOWLIST":
            key = msg.get("key", "")
            if not key:
                return ipc.build_error("Missing 'key' field")
            try:
                removed = self._allowlist.remove_device(key)
                return ipc.build_ok({"removed": removed})
            except PermissionError as exc:
                return ipc.build_error(str(exc))

        elif cmd == "SET_BLOCKING":
            enabled = bool(msg.get("enabled", True))
            self.config.blocking_enabled = enabled
            logger.info("Blocking globally set to %s via IPC", enabled)
            return ipc.build_ok({"blocking_enabled": enabled})

        elif cmd == "LIST_EVENTS":
            limit = int(msg.get("limit", 50))
            events = self._allowlist.list_events(limit=limit)
            return ipc.build_ok(events)

        else:
            return ipc.build_error(f"Unknown command: {cmd!r}")

    def _find_connected_by_key(self, key: str) -> Optional[UsbDevice]:
        for dev in self._backend.list_connected_devices():
            if dev.allowlist_key() == key:
                return dev
        return None


# --------------------------------------------------------------------------- #
# __main__ — allows:  sudo python3 -m service.daemon                          #
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    # Ensure the project root is on sys.path when run directly
    import os as _os
    _here = _os.path.dirname(_os.path.abspath(__file__))
    _root = _os.path.dirname(_here)
    if _root not in sys.path:
        sys.path.insert(0, _root)

    if sys.platform.startswith("linux"):
        from service.linux_daemon import main
    elif sys.platform == "win32":
        from service.windows_service import main  # type: ignore[import]
    elif sys.platform == "darwin":
        from service.macos_daemon import main  # type: ignore[import]
    else:
        print(f"[usb-blocker] Unsupported platform: {sys.platform}", file=sys.stderr)
        sys.exit(1)

    main()
