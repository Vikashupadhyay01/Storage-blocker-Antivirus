"""
core/interface.py
-----------------
Abstract base class that every platform backend must implement.

All three backends (linux_backend, windows_backend, macos_backend) inherit
from BaseBackend and fill in the platform-specific logic.  The service layer
only ever calls the methods declared here, keeping it fully portable.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Callable, List, Optional

from core.device import UsbDevice

logger = logging.getLogger(__name__)


# Type alias for the callback signatures expected by start_monitoring()
OnConnectCallback = Callable[[UsbDevice], None]
OnDisconnectCallback = Callable[[UsbDevice], None]


class BaseBackend(ABC):
    """
    Platform-agnostic interface for USB mass-storage device management.

    Implementations must be thread-safe: the monitoring thread(s) and the
    IPC handler may call list_connected_devices() concurrently.
    """

    def __init__(self) -> None:
        self._connected: dict[str, UsbDevice] = {}   # key → device
        self._monitoring: bool = False
        self._on_connect: Optional[OnConnectCallback] = None
        self._on_disconnect: Optional[OnDisconnectCallback] = None

    # ------------------------------------------------------------------ #
    # Abstract — must be implemented by every backend                      #
    # ------------------------------------------------------------------ #

    @abstractmethod
    def detect_devices(self) -> List[UsbDevice]:
        """
        Perform a one-shot scan of currently connected USB mass-storage
        devices and return a list.  Called at service startup and on demand.
        """

    @abstractmethod
    def _start_event_monitor(self) -> None:
        """
        Start the OS-native event-driven monitoring loop (background thread
        or async loop).  Must be non-blocking from the caller's perspective
        (i.e. start a daemon thread internally).

        When a device connects, call ``self._handle_connect(device)``.
        When a device disconnects, call ``self._handle_disconnect(device)``.
        """

    @abstractmethod
    def _stop_event_monitor(self) -> None:
        """Stop the OS event loop / monitoring thread cleanly."""

    @abstractmethod
    def block_device(self, device: UsbDevice) -> bool:
        """
        Prevent the given device from being accessible on this OS.

        Returns True if blocking succeeded, False otherwise.
        Implementations should log their own errors but NOT raise.
        """

    @abstractmethod
    def unblock_device(self, device: UsbDevice) -> bool:
        """
        Reverse a previous block (e.g. if the device was just added to the
        allow-list while still connected).

        Returns True if unblocking succeeded, False otherwise.
        """

    # ------------------------------------------------------------------ #
    # Concrete — shared scaffolding used by all backends                   #
    # ------------------------------------------------------------------ #

    def start_monitoring(
        self,
        on_connect: OnConnectCallback,
        on_disconnect: OnDisconnectCallback,
    ) -> None:
        """
        Register callbacks and begin event-driven monitoring.

        Parameters
        ----------
        on_connect    : Called with a UsbDevice whenever a USB mass-storage
                        device is detected.
        on_disconnect : Called with a UsbDevice (status may be stale) when
                        a device is removed.
        """
        if self._monitoring:
            logger.warning("start_monitoring() called while already monitoring — ignoring")
            return
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._monitoring = True
        self._start_event_monitor()
        logger.info("USB event monitoring started (%s)", self.__class__.__name__)

    def stop_monitoring(self) -> None:
        """Stop event-driven monitoring and clean up resources."""
        if not self._monitoring:
            return
        self._monitoring = False
        self._stop_event_monitor()
        logger.info("USB event monitoring stopped (%s)", self.__class__.__name__)

    def list_connected_devices(self) -> List[UsbDevice]:
        """Return a snapshot of currently tracked connected devices."""
        return list(self._connected.values())

    # ------------------------------------------------------------------ #
    # Internal event dispatchers (called by backend implementations)       #
    # ------------------------------------------------------------------ #

    def _handle_connect(self, device: UsbDevice) -> None:
        """
        Record a newly connected device and fire the on_connect callback.
        Backends call this; do not override in subclasses.
        """
        key = device.allowlist_key()
        self._connected[key] = device
        logger.debug("Device connected: %s", device)
        if self._on_connect:
            try:
                self._on_connect(device)
            except Exception:
                logger.exception("Unhandled exception in on_connect callback")

    def _handle_disconnect(self, device: UsbDevice) -> None:
        """
        Remove a disconnected device from the tracked set and fire the
        on_disconnect callback.  Backends call this; do not override.
        """
        key = device.allowlist_key()
        removed = self._connected.pop(key, None)
        if removed is None:
            # May happen if the device was never fully identified
            logger.debug("Disconnect event for untracked device: %s", device)
        else:
            logger.debug("Device disconnected: %s", removed)
        if self._on_disconnect:
            try:
                self._on_disconnect(device)
            except Exception:
                logger.exception("Unhandled exception in on_disconnect callback")
