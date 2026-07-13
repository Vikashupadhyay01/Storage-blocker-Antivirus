"""
service/linux_daemon.py
------------------------
Linux entry-point: integrates with systemd via sd_notify and handles
SIGTERM / SIGINT for clean shutdown.

Run as root.  Normally started by systemd via the unit file at
config/usb_blocker.service.

Usage (manual)
--------------
    sudo python -m service.linux_daemon
"""

from __future__ import annotations

import logging
import os
import signal
import sys

# Ensure the project root is on sys.path when run as a script
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.config import Config
from service.daemon import UsbBlockerDaemon

logger = logging.getLogger(__name__)


def _sd_notify(message: str) -> None:
    """Send a sd_notify message if NOTIFY_SOCKET is set."""
    notify_socket = os.environ.get("NOTIFY_SOCKET")
    if not notify_socket:
        return
    import socket as _socket
    try:
        with _socket.socket(_socket.AF_UNIX, _socket.SOCK_DGRAM) as sock:
            sock.connect(notify_socket)
            sock.sendall(message.encode())
    except Exception:
        pass


def main() -> None:
    config = Config.load()
    daemon = UsbBlockerDaemon(config=config)

    def _handle_signal(signum, frame):
        print(f"\n[usb-blocker] Received signal {signum} — shutting down…", flush=True)
        logger.info("Received signal %d — initiating shutdown", signum)
        daemon.stop()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    daemon.start()
    _sd_notify("READY=1\nSTATUS=USB Blocker monitoring active")

    daemon.wait()

    print("[usb-blocker] Daemon stopped cleanly.", flush=True)
    _sd_notify("STOPPING=1")
    sys.exit(0)


if __name__ == "__main__":
    main()
