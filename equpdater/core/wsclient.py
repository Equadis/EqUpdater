"""A small WebSocket client (RFC 6455), standard library only, Qt-free.

EqUpdater needs one thing from WebSockets: a text-message connection to the social test backend. This is that and
nothing more, so the Qt WebSockets module does not have to be packaged: the opening handshake, masked text
frames, ping/pong, close, and a read timeout. ``wss://`` uses the same hardened TLS context as the rest of the
program (``core/net.make_ssl_context``); plain ``ws://`` is allowed only for this computer or a private network (the caller checks).
"""

from __future__ import annotations

import base64
import hashlib
import os
import select
import socket
import ssl
import struct
import urllib.parse

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE = 64 * 1024


class WebSocketError(OSError):
    """The connection could not be made, was refused (``status`` is the HTTP status) or broke."""

    def __init__(self, message, status=0, retry_after=0.0):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


class WebSocket:
    def __init__(self, sock):
        self.sock = sock
        self.closed = False
        self._buf = b""

    # -- sending ---------------------------------------------------------------------------------
    def _frame(self, opcode: int, payload: bytes) -> bytes:
        head = bytes([0x80 | opcode])
        n = len(payload)
        mask = os.urandom(4)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

    def send_text(self, text: str) -> None:
        self.sock.sendall(self._frame(1, text.encode("utf-8")))

    def close(self, code: int = 1000) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.sock.sendall(self._frame(8, struct.pack(">H", code)))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    # -- receiving -------------------------------------------------------------------------------
    def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise WebSocketError("the connection closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def recv(self, timeout: float | None = None):
        """The next text message; None on a clean close. Raises ``socket.timeout`` if ``timeout`` passes with
        nothing at all (a partly read frame is kept for the next call), ``WebSocketError`` if the link breaks."""
        while True:
            if not self._buf and not (hasattr(self.sock, "pending") and self.sock.pending()):
                ready, _w, _x = select.select([self.sock], [], [], timeout)     # wait only for a frame to START;
                if not ready:                                                    # once it has, read it whole
                    raise socket.timeout("nothing arrived")
            self.sock.settimeout(15.0)
            b1, b2 = self._read(2)
            opcode, ln = b1 & 0x0F, b2 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._read(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._read(8))[0]
            if ln > MAX_MESSAGE:
                self.close(1009)
                raise WebSocketError("a message was too large")
            masked = bool(b2 & 0x80)
            mask = self._read(4) if masked else b""
            data = self._read(ln) if ln else b""
            if masked:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 8:
                self.closed = True
                try:
                    self.sock.close()
                except OSError:
                    pass
                return None
            if opcode == 9:                                   # ping: answer, keep reading
                self.sock.sendall(self._frame(10, data))
                continue
            if opcode == 1:
                return data.decode("utf-8", "replace")
            # binary, pong, continuation: not used by this protocol


def connect(url: str, headers: dict | None = None, timeout: float = 10.0, context=None) -> WebSocket:
    """Open ``ws://`` or ``wss://`` ``url``. Raises WebSocketError (with the HTTP status when the server said no)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("ws", "wss") or not parts.hostname:
        raise WebSocketError("not a WebSocket address")
    tls = parts.scheme == "wss"
    port = parts.port or (443 if tls else 80)
    try:
        sock = socket.create_connection((parts.hostname, port), timeout=timeout)
        if tls:
            if context is None:
                from .net import make_ssl_context
                context = make_ssl_context()[0]
            sock = context.wrap_socket(sock, server_hostname=parts.hostname)
    except (OSError, ssl.SSLError) as e:
        raise WebSocketError(f"could not connect: {e}") from None
    key = base64.b64encode(os.urandom(16)).decode()
    path = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
    lines = [f"GET {path} HTTP/1.1", f"Host: {parts.netloc}", "Upgrade: websocket", "Connection: Upgrade",
             f"Sec-WebSocket-Key: {key}", "Sec-WebSocket-Version: 13"]
    lines += [f"{k}: {v}" for k, v in (headers or {}).items()]
    try:
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        sock.settimeout(timeout)
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = sock.recv(4096)
            if not chunk:
                break
            raw += chunk
            if len(raw) > 16384:
                break
    except (OSError, ssl.SSLError) as e:
        sock.close()
        raise WebSocketError(f"the handshake failed: {e}") from None
    head, _, rest = raw.partition(b"\r\n\r\n")
    status_line, *header_lines = head.decode("latin-1").split("\r\n")
    try:
        status = int(status_line.split(" ", 2)[1])
    except (IndexError, ValueError):
        sock.close()
        raise WebSocketError("the server did not answer like a web server") from None
    hdrs = {}
    for ln in header_lines:
        if ":" in ln:
            k, v = ln.split(":", 1)
            hdrs[k.strip().lower()] = v.strip()
    if status != 101:
        sock.close()
        try:
            retry = float(hdrs.get("retry-after", 0))
        except ValueError:
            retry = 0.0
        raise WebSocketError(f"the server refused the connection ({status})", status, retry)
    want = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
    if hdrs.get("sec-websocket-accept") != want:
        sock.close()
        raise WebSocketError("the server's answer was not a valid WebSocket handshake")
    ws = WebSocket(sock)
    ws._buf = rest                                             # frames that arrived with the handshake
    return ws
