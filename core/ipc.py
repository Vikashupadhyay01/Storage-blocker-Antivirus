"""
core/ipc.py
-----------
IPC protocol definitions: message framing, command/response schemas, and
transport helpers shared by the service (server) and tray app (client).

Transport
---------
* Linux / macOS : Unix domain socket (AF_UNIX)
* Windows       : Named pipe  (\\\\.\\pipe\\usb-blocker)

Wire format
-----------
Each message is a length-prefixed JSON frame:

    [ 4 bytes big-endian uint32 length ][ <length> bytes UTF-8 JSON ]

Commands (client → service)
---------------------------
    {"cmd": "STATUS"}
    {"cmd": "LIST_DEVICES"}
    {"cmd": "LIST_ALLOWLIST"}
    {"cmd": "ADD_ALLOWLIST",    "key": "<vid:pid:serial>"}
    {"cmd": "REMOVE_ALLOWLIST", "key": "<vid:pid:serial>"}
    {"cmd": "SET_BLOCKING",     "enabled": true|false}
    {"cmd": "LIST_EVENTS",      "limit": 50}

Responses (service → client)
-----------------------------
All responses include:
    {"ok": true,  "data": ...}   on success
    {"ok": false, "error": "message"}  on failure
"""

from __future__ import annotations

import json
import struct
import socket
import sys
from typing import Any

# --------------------------------------------------------------------------- #
# Constants                                                                    #
# --------------------------------------------------------------------------- #

COMMANDS = frozenset({
    "STATUS",
    "LIST_DEVICES",
    "LIST_ALLOWLIST",
    "ADD_ALLOWLIST",
    "REMOVE_ALLOWLIST",
    "SET_BLOCKING",
    "LIST_EVENTS",
})

_HEADER_FMT = "!I"          # big-endian unsigned int (4 bytes)
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)
_MAX_MESSAGE_BYTES = 4 * 1024 * 1024   # 4 MB safety cap


# --------------------------------------------------------------------------- #
# Message building helpers                                                      #
# --------------------------------------------------------------------------- #

def build_command(cmd: str, **kwargs: Any) -> dict:
    """Return a command dict.  Validates that *cmd* is a known command."""
    if cmd not in COMMANDS:
        raise ValueError(f"Unknown IPC command: {cmd!r}")
    msg: dict[str, Any] = {"cmd": cmd}
    msg.update(kwargs)
    return msg


def build_ok(data: Any = None) -> dict:
    return {"ok": True, "data": data}


def build_error(message: str) -> dict:
    return {"ok": False, "error": message}


# --------------------------------------------------------------------------- #
# Frame encode / decode                                                        #
# --------------------------------------------------------------------------- #

def encode_message(obj: dict) -> bytes:
    """Serialise *obj* to a length-prefixed JSON frame."""
    payload = json.dumps(obj, default=str).encode("utf-8")
    header = struct.pack(_HEADER_FMT, len(payload))
    return header + payload


def decode_message(data: bytes) -> dict:
    """
    Decode a raw frame (header + payload) to a Python dict.

    *data* must contain exactly one complete frame (header + payload).
    """
    if len(data) < _HEADER_SIZE:
        raise ValueError("Frame too short to contain a header")
    (length,) = struct.unpack(_HEADER_FMT, data[:_HEADER_SIZE])
    if length > _MAX_MESSAGE_BYTES:
        raise ValueError(f"Frame payload too large: {length} bytes")
    payload = data[_HEADER_SIZE: _HEADER_SIZE + length]
    return json.loads(payload.decode("utf-8"))


# --------------------------------------------------------------------------- #
# Socket I/O helpers (used by both server and client)                          #
# --------------------------------------------------------------------------- #

def send_message(sock: socket.socket, obj: dict) -> None:
    """Send a single framed message over *sock*."""
    data = encode_message(obj)
    sock.sendall(data)


def recv_message(sock: socket.socket) -> dict:
    """
    Receive one framed message from *sock*.

    Blocks until a complete frame has been read.
    Raises ConnectionError if the connection is closed mid-frame.
    """
    # Read header
    header = _recv_exact(sock, _HEADER_SIZE)
    (length,) = struct.unpack(_HEADER_FMT, header)
    if length > _MAX_MESSAGE_BYTES:
        raise ValueError(f"Incoming frame too large: {length} bytes")
    # Read payload
    payload = _recv_exact(sock, length)
    return json.loads(payload.decode("utf-8"))


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly *n* bytes from *sock*, handling partial reads."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Socket closed before full message received")
        buf.extend(chunk)
    return bytes(buf)


# --------------------------------------------------------------------------- #
# Platform socket factory                                                       #
# --------------------------------------------------------------------------- #

def create_server_socket(socket_path: str) -> socket.socket:
    """
    Create and bind the IPC server socket appropriate for the current OS.

    * Linux / macOS : AF_UNIX stream socket at *socket_path*
    * Windows       : Falls back to localhost TCP on a fixed port derived
                      from the pipe name hash (real named-pipe support would
                      require win32pipe which is handled in service layer).
    """
    if sys.platform == "win32":
        # Use TCP loopback on Windows as a simple fallback.
        # The Windows service layer replaces this with a named pipe.
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", _win32_port(socket_path)))
        return srv
    else:
        import os
        if os.path.exists(socket_path):
            os.unlink(socket_path)
        os.makedirs(os.path.dirname(socket_path), exist_ok=True)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(socket_path)
        os.chmod(socket_path, 0o660)   # group-readable; tray runs as user
        return srv


def create_client_socket(socket_path: str, timeout: int = 5) -> socket.socket:
    """
    Create and connect a client socket to the IPC server.
    """
    if sys.platform == "win32":
        conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        conn.settimeout(timeout)
        conn.connect(("127.0.0.1", _win32_port(socket_path)))
    else:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(timeout)
        conn.connect(socket_path)
    return conn


def _win32_port(socket_path: str) -> int:
    """Derive a stable loopback port number from the socket path string."""
    return 49152 + (abs(hash(socket_path)) % 16383)
