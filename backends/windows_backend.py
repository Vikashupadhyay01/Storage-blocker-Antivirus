"""
backends/windows_backend.py
-----------------------------
Windows USB mass-storage backend.

Detection : WMI Win32_DeviceChangeEvent + Win32_DiskDrive polling.
Blocking  : Win32_PnPEntity.Disable() via WMI; fallback to UsbStor registry.
Dependencies: pywin32, wmi  (Windows-only)
"""

from __future__ import annotations

import logging
import threading
from typing import List, Optional

from core.device import DeviceStatus, UsbDevice
from core.interface import BaseBackend

logger = logging.getLogger(__name__)

_DEVICE_CHANGE_ARRIVAL = 2
_DEVICE_CHANGE_REMOVAL = 3
_USBSTOR_REG_PATH = r"SYSTEM\CurrentControlSet\Services\UsbStor"
_USBSTOR_START_DISABLED = 4


class WindowsBackend(BaseBackend):
    """Windows platform backend using WMI and pywin32."""

    def __init__(self) -> None:
        super().__init__()
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def detect_devices(self) -> List[UsbDevice]:
        try:
            import wmi as wmi_module  # type: ignore[import]
            c = wmi_module.WMI()
        except Exception as exc:
            logger.error("WMI initialisation failed: %s", exc)
            return []
        devices: List[UsbDevice] = []
        try:
            for disk in c.Win32_DiskDrive(InterfaceType="USB"):
                dev = _wmi_disk_to_usb(disk)
                if dev:
                    devices.append(dev)
        except Exception as exc:
            logger.error("WMI disk enumeration failed: %s", exc)
        logger.info("Initial scan: found %d USB disk(s)", len(devices))
        return devices

    def _start_event_monitor(self) -> None:
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="usb-blocker-wmi-monitor"
        )
        self._monitor_thread.start()

    def _stop_event_monitor(self) -> None:
        self._stop_event.set()
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=10)

    def block_device(self, device: UsbDevice) -> bool:
        success = _disable_pnp_device(device)
        if not success:
            success = _set_usbstor_start(_USBSTOR_START_DISABLED)
            if success:
                logger.warning("Fell back to disabling UsbStor driver globally")
        if success:
            logger.info("Blocked Windows device: %s", device.name)
        else:
            logger.error("Failed to block Windows device: %s", device.name)
        return success

    def unblock_device(self, device: UsbDevice) -> bool:
        success = _enable_pnp_device(device)
        if success:
            logger.info("Unblocked Windows device: %s", device.name)
        return success

    def _monitor_loop(self) -> None:
        try:
            import wmi as wmi_module  # type: ignore[import]
            import pythoncom  # type: ignore[import]
            pythoncom.CoInitialize()
            c = wmi_module.WMI()
            watcher = c.Win32_DeviceChangeEvent.watch_for()
        except Exception as exc:
            logger.error("WMI monitor thread could not start: %s", exc)
            return
        while not self._stop_event.is_set():
            try:
                event = watcher(timeout_ms=1000)
                if event is None:
                    continue
                event_type = getattr(event, "EventType", None)
                if event_type == _DEVICE_CHANGE_ARRIVAL:
                    self._on_wmi_arrival()
                elif event_type == _DEVICE_CHANGE_REMOVAL:
                    self._on_wmi_removal()
            except Exception:
                pass
        try:
            import pythoncom  # type: ignore[import]
            pythoncom.CoUninitialize()
        except Exception:
            pass

    def _on_wmi_arrival(self) -> None:
        known_keys = set(self._connected.keys())
        for dev in self.detect_devices():
            if dev.allowlist_key() not in known_keys:
                self._handle_connect(dev)

    def _on_wmi_removal(self) -> None:
        current = {d.allowlist_key() for d in self.detect_devices()}
        for key, dev in list(self._connected.items()):
            if key not in current:
                self._handle_disconnect(dev)


def _wmi_disk_to_usb(disk) -> Optional[UsbDevice]:
    try:
        pnp_id = getattr(disk, "PNPDeviceID", "") or ""
        vendor_id  = _extract_pnp_field(pnp_id, "VID_").lower()
        product_id = _extract_pnp_field(pnp_id, "PID_").lower()
        serial     = (getattr(disk, "SerialNumber", "") or "").strip()
        name       = (getattr(disk, "Model", "Unknown USB Disk") or "Unknown USB Disk").strip()
        device_path = getattr(disk, "DeviceID", "") or ""
        return UsbDevice(
            vendor_id=vendor_id, product_id=product_id, serial=serial,
            name=name, device_path=device_path, status=DeviceStatus.UNKNOWN,
        )
    except Exception as exc:
        logger.warning("Error parsing WMI disk object: %s", exc)
        return None


def _extract_pnp_field(pnp_id: str, prefix: str) -> str:
    idx = pnp_id.upper().find(prefix.upper())
    if idx == -1:
        return ""
    start = idx + len(prefix)
    end = pnp_id.find("&", start)
    return pnp_id[start:end] if end != -1 else pnp_id[start:]


def _disable_pnp_device(device: UsbDevice) -> bool:
    try:
        import wmi as wmi_module  # type: ignore[import]
        c = wmi_module.WMI()
        for pnp in c.Win32_PnPEntity(DeviceID=device.device_path):
            result = pnp.Disable()
            return result[0] == 0
    except Exception as exc:
        logger.warning("PnP Disable failed: %s", exc)
    return False


def _enable_pnp_device(device: UsbDevice) -> bool:
    try:
        import wmi as wmi_module  # type: ignore[import]
        c = wmi_module.WMI()
        for pnp in c.Win32_PnPEntity(DeviceID=device.device_path):
            result = pnp.Enable()
            return result[0] == 0
    except Exception as exc:
        logger.warning("PnP Enable failed: %s", exc)
    return False


def _set_usbstor_start(value: int) -> bool:
    try:
        import winreg  # type: ignore[import]
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, _USBSTOR_REG_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, "Start", 0, winreg.REG_DWORD, value)
        return True
    except Exception as exc:
        logger.error("Registry UsbStor update failed: %s", exc)
        return False
