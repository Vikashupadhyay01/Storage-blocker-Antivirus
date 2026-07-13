"""
tests/test_device.py
---------------------
Unit tests for core/device.py — UsbDevice dataclass, allowlist_key(),
equality, serialisation, and DeviceStatus.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from core.device import DeviceStatus, UsbDevice


def _make_device(**kwargs) -> UsbDevice:
    defaults = dict(
        vendor_id="0781",
        product_id="5583",
        serial="ABCD1234",
        name="SanDisk Ultra",
        device_path="/dev/sdb",
    )
    defaults.update(kwargs)
    return UsbDevice(**defaults)


class TestAllowlistKey:
    def test_basic(self):
        dev = _make_device(vendor_id="0781", product_id="5583", serial="XYZ")
        assert dev.allowlist_key() == "0781:5583:XYZ"

    def test_empty_serial(self):
        dev = _make_device(serial="")
        assert dev.allowlist_key() == "0781:5583:"

    def test_consistent(self):
        dev = _make_device()
        assert dev.allowlist_key() == dev.allowlist_key()


class TestEquality:
    def test_equal_devices(self):
        a = _make_device(vendor_id="1234", product_id="abcd", serial="S1")
        b = _make_device(vendor_id="1234", product_id="abcd", serial="S1")
        assert a == b

    def test_different_serial(self):
        a = _make_device(serial="S1")
        b = _make_device(serial="S2")
        assert a != b

    def test_different_type(self):
        dev = _make_device()
        assert dev != "not a device"

    def test_hashable(self):
        a = _make_device()
        b = _make_device()
        s = {a, b}
        assert len(s) == 1   # same key → same set element


class TestIsIdentified:
    def test_identified(self):
        assert _make_device().is_identified()

    def test_missing_vendor(self):
        assert not _make_device(vendor_id="").is_identified()

    def test_missing_product(self):
        assert not _make_device(product_id="").is_identified()


class TestSerialisation:
    def test_to_dict_keys(self):
        dev = _make_device()
        d = dev.to_dict()
        for field in ("vendor_id", "product_id", "serial", "name",
                      "device_path", "mount_point", "connected_at", "status"):
            assert field in d

    def test_status_is_string(self):
        dev = _make_device()
        d = dev.to_dict()
        assert isinstance(d["status"], str)

    def test_connected_at_is_iso_string(self):
        dev = _make_device()
        d = dev.to_dict()
        # Should not raise
        parsed = datetime.fromisoformat(d["connected_at"])
        assert parsed.tzinfo is not None

    def test_round_trip_dict(self):
        dev = _make_device(status=DeviceStatus.BLOCKED, mount_point="/media/usb")
        d = dev.to_dict()
        restored = UsbDevice.from_dict(d)
        assert restored == dev
        assert restored.status == DeviceStatus.BLOCKED
        assert restored.mount_point == "/media/usb"

    def test_round_trip_json(self):
        dev = _make_device()
        restored = UsbDevice.from_json(dev.to_json())
        assert restored == dev


class TestDeviceStatus:
    def test_values(self):
        assert DeviceStatus.ALLOWED.value == "allowed"
        assert DeviceStatus.BLOCKED.value == "blocked"
        assert DeviceStatus.UNKNOWN.value == "unknown"
        assert DeviceStatus.PENDING.value == "pending"

    def test_from_string(self):
        assert DeviceStatus("allowed") == DeviceStatus.ALLOWED
