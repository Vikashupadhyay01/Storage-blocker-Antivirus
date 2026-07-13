"""
backends/linux_backend.py
--------------------------
Linux USB mass-storage backend using pyudev for event-driven detection
and udisks2 / udev rules for blocking.

Two-layer blocking strategy (as designed):
  Layer 1 (proactive) — A udev rule file written by the service at install/
    startup time that immediately runs an "eject" script for any USB block
    device NOT on the allow-list.  This fires before auto-mount.
  Layer 2 (reactive)  — The pyudev event listener also triggers; if the
    device was auto-mounted anyway, we unmount + power-off via udisks2.

Privileges: the service runs as root; udisks2 commands still work because
we invoke them via subprocess (root can always call udisksctl).
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from typing import List, Optional

from core.device import DeviceStatus, UsbDevice
from core.interface import BaseBackend

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# udev rule template                                                           #
# --------------------------------------------------------------------------- #

UDEV_RULE_COMMENT = "# Managed by usb-blocker service — do not edit manually"

UDEV_RULE_TEMPLATE = """{comment}
ACTION=="add", SUBSYSTEM=="block", ENV{{ID_BUS}}=="usb", ENV{{DEVTYPE}}=="disk", \\
    RUN+="/usr/local/sbin/usb-blocker-eject %k"
"""


class LinuxBackend(BaseBackend):
    """
    Linux platform backend.

    Parameters
    ----------
    udev_rules_path : Full path where the managed udev rule file is written.
    allowlist       : AllowList instance used to check allow status in the
                      udev-triggered eject helper (separate process path).
                      Pass None here; the eject helper uses its own instance.
    """

    def __init__(self, udev_rules_path: str = "/etc/udev/rules.d/99-usb-blocker.rules") -> None:
        super().__init__()
        self._udev_rules_path = udev_rules_path
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._pyudev_monitor = None   # set in _start_event_monitor

    # ------------------------------------------------------------------ #
    # BaseBackend implementation                                            #
    # ------------------------------------------------------------------ #

    def detect_devices(self) -> List[UsbDevice]:
        """
        One-shot scan: enumerate all currently connected USB block devices.
        """
        try:
            import pyudev  # type: ignore[import]
        except ImportError:
            logger.error("pyudev is not installed; cannot detect USB devices on Linux")
            return []

        context = pyudev.Context()
        devices: List[UsbDevice] = []
        for udev_dev in context.list_devices(subsystem="block"):
            if udev_dev.get("ID_BUS") != "usb":
                continue
            if udev_dev.get("DEVTYPE") != "disk":
                continue
            device = _udev_device_to_usb(udev_dev)
            if device:
                devices.append(device)
        logger.info("Initial scan: found %d USB block device(s)", len(devices))
        return devices

    def _start_event_monitor(self) -> None:
        """Start the pyudev event-monitoring daemon thread."""
        try:
            import pyudev  # type: ignore[import]
        except ImportError:
            logger.error("pyudev not installed — event monitoring disabled on Linux")
            return

        context = pyudev.Context()
        monitor = pyudev.Monitor.from_netlink(context)
        monitor.filter_by(subsystem="block")
        # Start the netlink socket *before* spawning the thread so
        # monitor.fileno() is valid and poll() can block on it.
        monitor.start()
        self._pyudev_monitor = monitor
        self._stop_event.clear()

        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(monitor,),
            daemon=True,
            name="usb-blocker-udev-monitor",
        )
        self._monitor_thread.start()
        print("[usb-blocker] pyudev monitor thread started", flush=True)
        logger.debug("pyudev monitor thread started")

    def _stop_event_monitor(self) -> None:
        """Signal the monitor thread to stop and wait for it."""
        self._stop_event.set()
        if self._pyudev_monitor:
            # Reduce the receive buffer to zero; this causes the next
            # OS-level recv() inside poll() to return immediately with
            # an error, waking the blocked thread cleanly.
            try:
                import socket as _sock
                self._pyudev_monitor._socket.shutdown(_sock.SHUT_RDWR)
            except Exception:
                pass
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5)
        logger.debug("pyudev monitor thread stopped")

    def block_device(self, device: UsbDevice) -> bool:
        """
        Block a USB device: unmount all its partitions, then power it off.

        Returns True on success (even partial), False if nothing could be done.
        """
        success = False

        # 1. Try to unmount all mount points via udisksctl
        success |= _udisks_unmount(device.device_path)

        # 2. Power off (spins down + removes from block subsystem)
        success |= _udisks_power_off(device.device_path)

        # 3. Fallback: raw umount via mount namespace
        if not success:
            success |= _raw_unmount(device.device_path)

        if success:
            logger.info("Blocked device %s (%s)", device.name, device.device_path)
        else:
            logger.warning(
                "Could not fully block device %s (%s) — manual intervention may be needed",
                device.name, device.device_path,
            )
        return success

    def unblock_device(self, device: UsbDevice) -> bool:
        """
        Unblock a previously blocked device (e.g. user added it to allow-list
        while it was connected).  On Linux we simply re-probe the device so
        udisks2 can mount it.
        """
        path = device.device_path
        if not path or not os.path.exists(path):
            logger.warning("unblock_device: device path %s not found", path)
            return False
        try:
            result = subprocess.run(
                ["udisksctl", "mount", "--block-device", path, "--no-user-interaction"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                logger.info("Unblocked / re-mounted %s", path)
                return True
            else:
                logger.warning("udisksctl mount failed: %s", result.stderr.strip())
                return False
        except Exception as exc:
            logger.error("unblock_device exception: %s", exc)
            return False

    # ------------------------------------------------------------------ #
    # pyudev event loop                                                     #
    # ------------------------------------------------------------------ #

    def _monitor_loop(self, monitor) -> None:
        """
        Run in a daemon thread; poll the pyudev monitor for device events.

        Uses monitor.poll(timeout=1) instead of select()+poll(0) to avoid
        a race where poll returns None even though select reported readable.
        The 1-second timeout lets us re-check _stop_event periodically.
        """
        print("[usb-blocker] Entering udev event loop — waiting for USB events…",
              flush=True)
        logger.debug("Entering udev event loop")

        while not self._stop_event.is_set():
            try:
                udev_device = monitor.poll(timeout=1)
            except Exception as exc:
                if self._stop_event.is_set():
                    break  # socket was shut down cleanly
                logger.warning("udev monitor.poll() error: %s", exc)
                break

            if udev_device is None:
                # Timeout — no event; loop back and check _stop_event
                continue

            # Only care about disk-level events, not partition events
            if udev_device.get("DEVTYPE") != "disk":
                continue
            if udev_device.get("ID_BUS") != "usb":
                continue

            action = udev_device.action
            device = _udev_device_to_usb(udev_device)
            if device is None:
                logger.warning(
                    "Could not parse USB device info from udev event "
                    "(action=%s) — skipping", action,
                )
                continue

            # ── Visible terminal output for every event ──────────────────
            print(
                f"[usb-blocker] udev {action.upper()}: "
                f"{device.name!r}  vid={device.vendor_id}  "
                f"pid={device.product_id}  serial={device.serial!r}  "
                f"path={device.device_path}",
                flush=True,
            )

            if action == "add":
                logger.info("udev ADD event: %s", device)
                self._handle_connect(device)
            elif action == "remove":
                logger.info("udev REMOVE event: %s", device)
                self._handle_disconnect(device)

        print("[usb-blocker] Exited udev event loop", flush=True)
        logger.debug("Exited udev event loop")

    # ------------------------------------------------------------------ #
    # udev rule management                                                  #
    # ------------------------------------------------------------------ #

    def install_udev_rule(self) -> bool:
        """
        Write the managed udev rule file and reload udev rules.

        Must be called as root.  Returns True on success.
        """
        rule_content = UDEV_RULE_TEMPLATE.format(comment=UDEV_RULE_COMMENT)
        try:
            os.makedirs(os.path.dirname(self._udev_rules_path), exist_ok=True)
            with open(self._udev_rules_path, "w", encoding="utf-8") as fh:
                fh.write(rule_content)
            _reload_udev_rules()
            logger.info("udev rule installed at %s", self._udev_rules_path)
            return True
        except OSError as exc:
            logger.error("Failed to install udev rule: %s", exc)
            return False

    def remove_udev_rule(self) -> bool:
        """Remove the managed udev rule file and reload."""
        try:
            if os.path.exists(self._udev_rules_path):
                os.unlink(self._udev_rules_path)
                _reload_udev_rules()
                logger.info("udev rule removed: %s", self._udev_rules_path)
            return True
        except OSError as exc:
            logger.error("Failed to remove udev rule: %s", exc)
            return False


# --------------------------------------------------------------------------- #
# udev attribute → UsbDevice                                                   #
# --------------------------------------------------------------------------- #

def _udev_device_to_usb(udev_dev) -> Optional[UsbDevice]:
    """
    Convert a pyudev Device object to a UsbDevice.

    Returns None if the device is definitely not a USB mass-storage device
    or if essential attributes cannot be read.
    """
    try:
        vendor_id  = udev_dev.get("ID_VENDOR_ID", "").lower().strip() or \
                     udev_dev.get("ID_USB_VENDOR_ID", "").lower().strip()
        product_id = udev_dev.get("ID_MODEL_ID", "").lower().strip() or \
                     udev_dev.get("ID_USB_PRODUCT_ID", "").lower().strip()
        serial     = udev_dev.get("ID_SERIAL_SHORT", "").strip() or \
                     udev_dev.get("ID_SERIAL", "").strip()
        name       = (
            udev_dev.get("ID_MODEL_ENC", "")
            .encode("utf-8").decode("unicode_escape").strip()
            or udev_dev.get("ID_MODEL", "Unknown USB Storage").strip()
        )
        device_path = udev_dev.device_node or ""
        mount_point = _find_mount_point(device_path)

        return UsbDevice(
            vendor_id=vendor_id,
            product_id=product_id,
            serial=serial,
            name=name,
            device_path=device_path,
            mount_point=mount_point,
            status=DeviceStatus.UNKNOWN,
        )
    except Exception as exc:
        logger.warning("Error parsing udev device attributes: %s", exc)
        return None


def _find_mount_point(device_path: str) -> Optional[str]:
    """
    Look up the mount point for *device_path* by reading /proc/mounts.
    Returns None if not mounted.
    """
    if not device_path:
        return None
    try:
        with open("/proc/mounts", "r") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and parts[0] == device_path:
                    return parts[1]
    except OSError:
        pass
    return None


# --------------------------------------------------------------------------- #
# Blocking helpers                                                              #
# --------------------------------------------------------------------------- #

def _udisks_unmount(device_path: str) -> bool:
    if not device_path:
        return False
    try:
        result = subprocess.run(
            ["udisksctl", "unmount", "--block-device", device_path,
             "--no-user-interaction", "--force"],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0
    except Exception as exc:
        logger.debug("udisks_unmount exception: %s", exc)
        return False


def _udisks_power_off(device_path: str) -> bool:
    if not device_path:
        return False
    try:
        result = subprocess.run(
            ["udisksctl", "power-off", "--block-device", device_path,
             "--no-user-interaction"],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0
    except Exception as exc:
        logger.debug("udisks_power_off exception: %s", exc)
        return False


def _raw_unmount(device_path: str) -> bool:
    """Last-resort: call umount directly."""
    if not device_path:
        return False
    try:
        result = subprocess.run(
            ["umount", "-l", device_path],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception as exc:
        logger.debug("raw_unmount exception: %s", exc)
        return False


def _reload_udev_rules() -> None:
    subprocess.run(["udevadm", "control", "--reload-rules"], check=False, timeout=10)
    subprocess.run(["udevadm", "trigger"], check=False, timeout=10)
