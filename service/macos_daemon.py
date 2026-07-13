"""
service/macos_daemon.py
------------------------
macOS launchd daemon entry-point.

launchd keeps the process alive (KeepAlive = true in the plist).
We handle SIGTERM for clean shutdown.

Usage (manual)
--------------
    sudo python -m service.macos_daemon

Install via launchd
-------------------
    sudo cp config/com.usblocker.plist /Library/LaunchDaemons/
    sudo launchctl load -w /Library/LaunchDaemons/com.usblocker.plist
"""

from __future__ import annotations

import logging
import os
import signal
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core.config import Config
from service.daemon import UsbBlockerDaemon

logger = logging.getLogger(__name__)


def main() -> None:
    config = Config.load()
    daemon = UsbBlockerDaemon(config=config)

    def _handle_signal(signum, frame):
        logger.info("Received signal %d — stopping daemon", signum)
        daemon.stop()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    daemon.run()
    sys.exit(0)


if __name__ == "__main__":
    main()
