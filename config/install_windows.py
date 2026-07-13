"""
config/install_windows.py
--------------------------
Windows Service installer helper.

Run as Administrator:
    python config/install_windows.py install   # Install + auto-start
    python config/install_windows.py remove    # Stop + remove
    python config/install_windows.py start
    python config/install_windows.py stop
"""

from __future__ import annotations

import subprocess
import sys
import os

_SERVICE_MODULE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "service", "windows_service.py",
)


def main():
    if len(sys.argv) < 2:
        print("Usage: install_windows.py [install|remove|start|stop]")
        sys.exit(1)

    action = sys.argv[1].lower()

    if action == "install":
        subprocess.check_call([sys.executable, _SERVICE_MODULE, "install"])
        subprocess.check_call([sys.executable, _SERVICE_MODULE, "start"])
        print("USB Blocker Service installed and started.")

    elif action == "remove":
        subprocess.check_call([sys.executable, _SERVICE_MODULE, "stop"])
        subprocess.check_call([sys.executable, _SERVICE_MODULE, "remove"])
        print("USB Blocker Service stopped and removed.")

    elif action in ("start", "stop"):
        subprocess.check_call([sys.executable, _SERVICE_MODULE, action])

    else:
        print(f"Unknown action: {action!r}")
        sys.exit(1)


if __name__ == "__main__":
    main()
