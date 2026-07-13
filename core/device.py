"""
core/device.py
--------------
Shared UsbDevice dataclass used across all platform backends and the service layer.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class DeviceStatus(str, Enum):
    """Lifecycle status of a USB device as tracked by the service."""

    UNKNOWN = "unknown"
    ALLOWED = "allowed"
    BLOCKED = "blocked"
    PENDING = "pending"   # seen but allow-list check not yet complete


@dataclass
class UsbDevice:
    """
    Represents a USB mass-storage device detected by any platform backend.

    Fields
    ------
    vendor_id   : 4-hex-digit USB vendor ID string, e.g. "0781"
    product_id  : 4-hex-digit USB product ID string, e.g. "5583"
    serial      : Device serial number string (may be empty if device has none)
    name        : Human-readable device name / model string
    device_path : OS-level device node, e.g. "/dev/sdb" (Linux), "\\\\.\\\\PhysicalDrive1" (Win)
    mount_point : Filesystem mount point if auto-mounted, e.g. "/media/user/DISK"
    connected_at: UTC timestamp when the device was first seen this session
    status      : Current DeviceStatus classification
    """

    vendor_id: str = ""
    product_id: str = ""
    serial: str = ""
    name: str = ""
    device_path: str = ""
    mount_point: Optional[str] = None
    connected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: DeviceStatus = DeviceStatus.UNKNOWN

    # ------------------------------------------------------------------ #
    # Identity / allow-list helpers                                        #
    # ------------------------------------------------------------------ #

    def allowlist_key(self) -> str:
        """
        Stable composite key used as the primary identifier in the allow-list.

        Format: "<vendor_id>:<product_id>:<serial>"

        Note: serial may be empty for some devices; those are keyed by
        vendor+product only and are therefore less precisely identified.
        """
        return f"{self.vendor_id}:{self.product_id}:{self.serial}"

    def is_identified(self) -> bool:
        """Return True if we have at least vendor+product IDs."""
        return bool(self.vendor_id and self.product_id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, UsbDevice):
            return NotImplemented
        return self.allowlist_key() == other.allowlist_key()

    def __hash__(self) -> int:
        return hash(self.allowlist_key())

    # ------------------------------------------------------------------ #
    # Serialisation                                                        #
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        d = asdict(self)
        d["connected_at"] = self.connected_at.isoformat()
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "UsbDevice":
        """Reconstruct a UsbDevice from a dictionary (e.g. parsed from JSON)."""
        data = dict(data)
        if "connected_at" in data and isinstance(data["connected_at"], str):
            data["connected_at"] = datetime.fromisoformat(data["connected_at"])
        if "status" in data:
            data["status"] = DeviceStatus(data["status"])
        return cls(**data)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, s: str) -> "UsbDevice":
        return cls.from_dict(json.loads(s))

    def __repr__(self) -> str:
        return (
            f"UsbDevice(name={self.name!r}, vid={self.vendor_id}, "
            f"pid={self.product_id}, serial={self.serial!r}, "
            f"path={self.device_path!r}, status={self.status.value})"
        )
