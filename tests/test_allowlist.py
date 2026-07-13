"""
tests/test_allowlist.py
------------------------
Unit tests for core/allowlist.py.

All tests use an in-memory SQLite database so no filesystem or privilege
checks are exercised.  The _require_admin() guard is monkey-patched out.
"""

from __future__ import annotations

import os
import sys
import pytest

# Patch privilege check before importing allowlist
import unittest.mock as mock


@pytest.fixture(autouse=True)
def no_admin_check(monkeypatch):
    """Bypass the root/admin check for all tests in this file."""
    monkeypatch.setattr("core.allowlist._require_admin", lambda: None)


@pytest.fixture
def db(tmp_path):
    """Return an AllowList backed by a temp-dir SQLite file."""
    from core.allowlist import AllowList
    return AllowList(str(tmp_path / "test_allowlist.db"))


def _dev(vendor_id="0781", product_id="5583", serial="ABC", name="TestDisk"):
    from core.device import UsbDevice
    return UsbDevice(vendor_id=vendor_id, product_id=product_id,
                     serial=serial, name=name)


class TestAllowListCRUD:
    def test_initially_empty(self, db):
        assert db.list_entries() == []

    def test_add_device(self, db):
        dev = _dev()
        result = db.add_device(dev, added_by="tester")
        assert result is True

    def test_add_duplicate_returns_false(self, db):
        dev = _dev()
        db.add_device(dev)
        result = db.add_device(dev)
        assert result is False

    def test_is_allowed_after_add(self, db):
        dev = _dev()
        db.add_device(dev)
        assert db.is_allowed(dev) is True

    def test_is_not_allowed_before_add(self, db):
        dev = _dev()
        assert db.is_allowed(dev) is False

    def test_different_serial_not_allowed(self, db):
        dev1 = _dev(serial="AAA")
        dev2 = _dev(serial="BBB")
        db.add_device(dev1)
        assert db.is_allowed(dev2) is False

    def test_remove_device(self, db):
        dev = _dev()
        db.add_device(dev)
        removed = db.remove_device(dev.allowlist_key())
        assert removed is True
        assert db.is_allowed(dev) is False

    def test_remove_nonexistent_returns_false(self, db):
        assert db.remove_device("0000:0000:NONE") is False

    def test_list_entries_returns_correct_count(self, db):
        db.add_device(_dev(serial="S1"))
        db.add_device(_dev(serial="S2"))
        db.add_device(_dev(serial="S3"))
        assert len(db.list_entries()) == 3

    def test_get_entry(self, db):
        dev = _dev()
        db.add_device(dev, added_by="admin")
        entry = db.get_entry(dev.allowlist_key())
        assert entry is not None
        assert entry["vendor_id"] == dev.vendor_id
        assert entry["product_id"] == dev.product_id
        assert entry["serial"] == dev.serial
        assert entry["added_by"] == "admin"

    def test_get_entry_missing_returns_none(self, db):
        assert db.get_entry("ffff:ffff:NONE") is None


class TestAllowListEventLog:
    def test_log_event(self, db):
        dev = _dev()
        db.log_event("BLOCKED", dev, action="not_in_allowlist")
        events = db.list_events()
        assert len(events) == 1
        assert events[0]["event"] == "BLOCKED"

    def test_list_events_limit(self, db):
        dev = _dev()
        for i in range(10):
            db.log_event("CONNECTED", dev)
        assert len(db.list_events(limit=5)) == 5

    def test_events_ordered_desc(self, db):
        dev = _dev()
        db.log_event("CONNECTED", dev, action="first")
        db.log_event("BLOCKED", dev, action="second")
        events = db.list_events()
        assert events[0]["action"] == "second"
        assert events[1]["action"] == "first"


class TestPrivilegeGuard:
    def test_add_without_privilege_raises(self, tmp_path, monkeypatch):
        """Re-enable the guard and confirm PermissionError is raised."""
        import core.allowlist as al_module
        original = al_module._require_admin

        def _raise():
            raise PermissionError("Not root")

        monkeypatch.setattr(al_module, "_require_admin", _raise)
        from core.allowlist import AllowList
        db2 = AllowList(str(tmp_path / "priv_test.db"))
        with pytest.raises(PermissionError):
            db2.add_device(_dev())
