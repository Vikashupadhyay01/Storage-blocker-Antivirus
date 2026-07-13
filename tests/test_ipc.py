"""
tests/test_ipc.py
------------------
Unit tests for core/ipc.py — message framing, encode/decode round-trips,
and the build_* helpers.
"""

from __future__ import annotations

import json
import socket
import struct
import threading

import pytest

from core import ipc


class TestEncodeDecode:
    def test_round_trip_simple(self):
        msg = {"cmd": "STATUS"}
        frame = ipc.encode_message(msg)
        decoded = ipc.decode_message(frame)
        assert decoded == msg

    def test_round_trip_unicode(self):
        msg = {"data": "Ünïcödé テスト"}
        frame = ipc.encode_message(msg)
        decoded = ipc.decode_message(frame)
        assert decoded["data"] == msg["data"]

    def test_round_trip_nested(self):
        msg = {"ok": True, "data": {"devices": [{"name": "USB Disk", "vendor_id": "0781"}]}}
        decoded = ipc.decode_message(ipc.encode_message(msg))
        assert decoded == msg

    def test_header_contains_correct_length(self):
        msg = {"x": "y"}
        frame = ipc.encode_message(msg)
        (length,) = struct.unpack("!I", frame[:4])
        assert length == len(frame) - 4

    def test_decode_too_short_raises(self):
        with pytest.raises(ValueError, match="too short"):
            ipc.decode_message(b"\x00\x00")

    def test_decode_oversized_raises(self):
        # Manufacture a header claiming 8 MB payload
        fake_header = struct.pack("!I", 8 * 1024 * 1024 + 1)
        with pytest.raises(ValueError, match="too large"):
            ipc.decode_message(fake_header + b"\x00")


class TestBuildHelpers:
    def test_build_command_known(self):
        msg = ipc.build_command("STATUS")
        assert msg["cmd"] == "STATUS"

    def test_build_command_with_kwargs(self):
        msg = ipc.build_command("ADD_ALLOWLIST", key="0781:5583:ABC")
        assert msg["key"] == "0781:5583:ABC"

    def test_build_command_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown IPC command"):
            ipc.build_command("UNKNOWN_CMD")

    def test_build_ok(self):
        resp = ipc.build_ok({"count": 3})
        assert resp["ok"] is True
        assert resp["data"]["count"] == 3

    def test_build_ok_none(self):
        resp = ipc.build_ok()
        assert resp["ok"] is True
        assert resp["data"] is None

    def test_build_error(self):
        resp = ipc.build_error("something went wrong")
        assert resp["ok"] is False
        assert "something went wrong" in resp["error"]


class TestSocketIO:
    """Integration test: send + recv over a real Unix socket pair."""

    def test_loopback_send_recv(self, tmp_path):
        import sys
        if sys.platform == "win32":
            pytest.skip("AF_UNIX not universally available on Windows CI")

        sock_path = str(tmp_path / "test.sock")
        received = []

        def server_thread():
            server = ipc.create_server_socket(sock_path)
            server.listen(1)
            client, _ = server.accept()
            msg = ipc.recv_message(client)
            received.append(msg)
            ipc.send_message(client, ipc.build_ok({"echo": msg.get("cmd")}))
            client.close()
            server.close()

        t = threading.Thread(target=server_thread, daemon=True)
        t.start()

        import time
        time.sleep(0.1)

        conn = ipc.create_client_socket(sock_path, timeout=3)
        ipc.send_message(conn, ipc.build_command("STATUS"))
        response = ipc.recv_message(conn)
        conn.close()

        t.join(timeout=3)

        assert received[0]["cmd"] == "STATUS"
        assert response["ok"] is True
        assert response["data"]["echo"] == "STATUS"

    def test_recv_exact_handles_partial(self):
        """_recv_exact must reassemble chunked data correctly."""
        a, b = socket.socketpair()
        try:
            data = b"Hello, World!"
            a.sendall(data[:5])
            a.sendall(data[5:])
            result = ipc._recv_exact(b, len(data))
            assert result == data
        finally:
            a.close()
            b.close()
