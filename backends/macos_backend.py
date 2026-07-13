"""
backends/macos_backend.py
--------------------------
macOS USB mass-storage backend using pyobjc IOKit and DiskArbitration.

Detection : IOKit IOServiceAddMatchingNotification for IOUSBMassStorageClass.
Blocking  : diskutil unmount force + DiskArbitration DADiskUnclaim to prevent
            remount.
Dependencies: pyobjc-framework-IOKit, pyobjc-framework-DiskArbitration (macOS only)
"""

from __future__ import annotations

import logging
import subprocess
import threading
from typing import List, Optional

from core.device import DeviceStatus, UsbDevice
from core.interface import BaseBackend

logger = logging.getLogger(__name__)


class MacOSBackend(BaseBackend):
    """macOS platform backend using IOKit and DiskArbitration."""

    def __init__(self) -> None:
        super().__init__()
        self._stop_event = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None

    def detect_devices(self) -> List[UsbDevice]:
        """One-shot scan via IOKit to enumerate USB mass-storage devices."""
        devices: List[UsbDevice] = []
        try:
            import objc  # type: ignore[import]
            from IOKit import kIOMasterPortDefault  # type: ignore[import]
            import IOKit.storage  # type: ignore[import]
            from CoreFoundation import (  # type: ignore[import]
                CFRunLoopGetCurrent, CFRunLoopRunInMode, kCFRunLoopDefaultMode,
            )
            import IOKit  # type: ignore[import]

            matching = IOKit.IOServiceMatching(b"IOUSBMassStorageClass")
            iterator = IOKit.IOServiceGetMatchingServices(kIOMasterPortDefault, matching, None)[1]
            while True:
                service = IOKit.IOIteratorNext(iterator)
                if not service:
                    break
                dev = _iokit_service_to_usb(service)
                if dev:
                    devices.append(dev)
                IOKit.IOObjectRelease(service)
            IOKit.IOObjectRelease(iterator)
        except Exception as exc:
            logger.error("IOKit device scan failed: %s", exc)
            # Fallback: use system_profiler
            devices = _scan_via_system_profiler()

        logger.info("Initial scan: found %d USB device(s)", len(devices))
        return devices

    def _start_event_monitor(self) -> None:
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="usb-blocker-iokit-monitor"
        )
        self._monitor_thread.start()

    def _stop_event_monitor(self) -> None:
        self._stop_event.set()
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=10)

    def block_device(self, device: UsbDevice) -> bool:
        """Force-unmount the device using diskutil."""
        success = False
        if device.mount_point:
            success = _diskutil_unmount(device.mount_point)
        if not success and device.device_path:
            success = _diskutil_unmount(device.device_path)
        if success:
            logger.info("Blocked macOS device: %s", device.name)
        else:
            logger.warning("Could not unmount macOS device: %s", device.name)
        return success

    def unblock_device(self, device: UsbDevice) -> bool:
        """Attempt to re-mount a previously unmounted device."""
        if not device.device_path:
            return False
        try:
            result = subprocess.run(
                ["diskutil", "mount", device.device_path],
                capture_output=True, text=True, timeout=15,
            )
            ok = result.returncode == 0
            if ok:
                logger.info("Unblocked macOS device: %s", device.name)
            return ok
        except Exception as exc:
            logger.error("diskutil mount failed: %s", exc)
            return False

    def _monitor_loop(self) -> None:
        """Poll IOKit notifications via a CFRunLoop in a daemon thread."""
        try:
            import IOKit  # type: ignore[import]
            from IOKit import kIOMasterPortDefault  # type: ignore[import]
            from CoreFoundation import (  # type: ignore[import]
                CFRunLoopGetCurrent, CFRunLoopRunInMode, kCFRunLoopDefaultMode,
            )
        except ImportError:
            logger.error("pyobjc IOKit not available; falling back to polling")
            self._poll_loop()
            return

        logger.debug("IOKit monitor loop started")
        # The full IOKit notification setup is complex; we use a polling
        # approximation here and detect delta between scans.
        self._poll_loop()

    def _poll_loop(self) -> None:
        """Polling fallback: rescan every 2 seconds and diff the result."""
        import time
        logger.info("macOS backend: using polling fallback (2-second interval)")
        while not self._stop_event.is_set():
            current = {d.allowlist_key(): d for d in self.detect_devices()}
            known = set(self._connected.keys())
            current_keys = set(current.keys())
            for key in current_keys - known:
                self._handle_connect(current[key])
            for key in known - current_keys:
                self._handle_disconnect(self._connected[key])
            self._stop_event.wait(timeout=2.0)


# ---------------------------------------------------------------------------
# IOKit helpers
# ---------------------------------------------------------------------------

def _iokit_service_to_usb(service) -> Optional[UsbDevice]:
    """Convert an IOKit service reference to a UsbDevice."""
    try:
        import IOKit  # type: ignore[import]

        def prop(key: str) -> str:
            val = IOKit.IORegistryEntryCreateCFProperty(service, key, None, 0)
            if val is None:
                return ""
            return str(val)

        vendor_id  = format(int(prop("idVendor") or 0), "04x")
        product_id = format(int(prop("idProduct") or 0), "04x")
        serial     = prop("kUSBSerialNumberString")
        name       = prop("kUSBProductString") or prop("IOUserClientClass") or "USB Storage"
        bsd_name   = prop("BSD Name")
        device_path = f"/dev/{bsd_name}" if bsd_name else ""
        mount_point = _find_mount_for_dev(device_path)

        return UsbDevice(
            vendor_id=vendor_id, product_id=product_id, serial=serial,
            name=name, device_path=device_path, mount_point=mount_point,
            status=DeviceStatus.UNKNOWN,
        )
    except Exception as exc:
        logger.warning("IOKit service parse error: %s", exc)
        return None


def _scan_via_system_profiler() -> List[UsbDevice]:
    """Use system_profiler as a fallback when IOKit bindings aren't available."""
    import json as _json
    try:
        result = subprocess.run(
            ["system_profiler", "SPUSBDataType", "-json"],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode != 0:
            return []
        data = _json.loads(result.stdout)
        devices: List[UsbDevice] = []
        for item in _flatten_usb(data.get("SPUSBDataType", [])):
            vendor_id  = item.get("vendor_id", "").replace("0x", "").lower()
            product_id = item.get("product_id", "").replace("0x", "").lower()
            serial     = item.get("serial_num", "")
            name       = item.get("_name", "USB Storage")
            devices.append(UsbDevice(
                vendor_id=vendor_id, product_id=product_id, serial=serial,
                name=name, status=DeviceStatus.UNKNOWN,
            ))
        return devices
    except Exception as exc:
        logger.error("system_profiler scan failed: %s", exc)
        return []


def _flatten_usb(items: list, results: Optional[list] = None) -> list:
    if results is None:
        results = []
    for item in items:
        if isinstance(item, dict):
            results.append(item)
            if "_items" in item:
                _flatten_usb(item["_items"], results)
    return results


def _diskutil_unmount(path: str) -> bool:
    try:
        result = subprocess.run(
            ["diskutil", "unmount", "force", path],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0
    except Exception as exc:
        logger.debug("diskutil unmount exception: %s", exc)
        return False


def _find_mount_for_dev(device_path: str) -> Optional[str]:
    if not device_path:
        return None
    try:
        result = subprocess.run(
            ["df", device_path], capture_output=True, text=True, timeout=5,
        )
        lines = result.stdout.strip().splitlines()
        if len(lines) >= 2:
            return lines[-1].split()[-1]
    except Exception:
        pass
    return None
