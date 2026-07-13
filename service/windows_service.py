"""
service/windows_service.py
---------------------------
Windows Service wrapper using pywin32 (win32serviceutil).

Install
-------
    python service/windows_service.py install
    net start UsbBlockerService

Remove
------
    net stop UsbBlockerService
    python service/windows_service.py remove
"""

from __future__ import annotations

import os
import sys
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main():
    try:
        import servicemanager          # type: ignore[import]
        import win32event              # type: ignore[import]
        import win32service            # type: ignore[import]
        import win32serviceutil        # type: ignore[import]
    except ImportError:
        print("ERROR: pywin32 is required for Windows service support.", file=sys.stderr)
        sys.exit(1)

    from core.config import Config
    from service.daemon import UsbBlockerDaemon

    class UsbBlockerService(win32serviceutil.ServiceFramework):
        _svc_name_ = "UsbBlockerService"
        _svc_display_name_ = "USB Blocker Service"
        _svc_description_ = (
            "Monitors USB mass-storage device connections and enforces "
            "an allow-list policy."
        )

        def __init__(self, args):
            win32serviceutil.ServiceFramework.__init__(self, args)
            self._stop_event = win32event.CreateEvent(None, 0, 0, None)
            self._daemon = UsbBlockerDaemon(config=Config.load())

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self._daemon.stop()
            win32event.SetEvent(self._stop_event)

        def SvcDoRun(self):
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, ""),
            )
            self._daemon.start()
            # Block until SvcStop signals us
            win32event.WaitForSingleObject(self._stop_event, win32event.INFINITE)

    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(UsbBlockerService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(UsbBlockerService)


if __name__ == "__main__":
    main()
