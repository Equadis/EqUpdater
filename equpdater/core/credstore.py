"""Secrets in the operating system's credential manager, never on disk.

``CredentialStore`` keeps small text secrets (a sign-in's tokens) under a
service and an account name:

* **Windows**: Credential Manager (``CredWriteW`` / ``CredReadW`` / ``CredDeleteW``
  through ``ctypes``). A credential holds at most 2,560 bytes, so a longer value
  is split across numbered entries and put back together.
* **Linux**: the Secret Service, through the ``keyring`` package if it is
  installed, else the ``secret-tool`` program (libsecret) if it is.
* **Otherwise**: ``MemoryStore``: the secret lives only as long as the program.
  Nothing is ever written to a file as a fallback; a player without a credential
  manager simply signs in again next time.

Nothing in here prints, logs or returns a secret except ``get``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

CHUNK = 1800            # bytes of UTF-8 per Windows credential (limit 2,560)
MAX_CHUNKS = 16


class CredentialStore:
    """Interface. ``kind`` names the backend for the UI and for tests."""
    kind = "none"
    persistent = False

    def set(self, service: str, account: str, secret: str) -> bool:
        raise NotImplementedError

    def get(self, service: str, account: str) -> str | None:
        raise NotImplementedError

    def delete(self, service: str, account: str) -> bool:
        raise NotImplementedError


class MemoryStore(CredentialStore):
    kind = "memory"
    persistent = False

    def __init__(self):
        self._d: dict = {}

    def set(self, service, account, secret):
        self._d[(service, account)] = secret
        return True

    def get(self, service, account):
        return self._d.get((service, account))

    def delete(self, service, account):
        return self._d.pop((service, account), None) is not None


# ── Windows Credential Manager ─────────────────────────────────────────────────────

class WindowsStore(CredentialStore):
    kind = "windows-credential-manager"
    persistent = True

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

        class CREDENTIALW(ctypes.Structure):
            _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                        ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                        ("LastWritten", FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                        ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                        ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                        ("UserName", wintypes.LPWSTR)]
        self.CRED = CREDENTIALW
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        adv.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
        adv.CredWriteW.restype = wintypes.BOOL
        adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
        adv.CredReadW.restype = wintypes.BOOL
        adv.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        adv.CredDeleteW.restype = wintypes.BOOL
        adv.CredFree.argtypes = [ctypes.c_void_p]
        self.adv = adv

    @staticmethod
    def _target(service, account, i):
        return f"{service}/{account}/{i}"

    def _write(self, target, account, data: bytes) -> bool:
        ct = self.ct
        buf = (ct.c_ubyte * max(1, len(data))).from_buffer_copy(data or b"\0")
        cred = self.CRED(Flags=0, Type=1, TargetName=target, Comment=None, CredentialBlobSize=len(data),
                         CredentialBlob=ct.cast(buf, ct.POINTER(ct.c_ubyte)), Persist=2,   # LOCAL_MACHINE: this PC only
                         AttributeCount=0, Attributes=None, TargetAlias=None, UserName=account)
        return bool(self.adv.CredWriteW(ct.byref(cred), 0))

    def _read(self, target) -> bytes | None:
        ct = self.ct
        pcred = ct.POINTER(self.CRED)()
        if not self.adv.CredReadW(target, 1, 0, ct.byref(pcred)):
            return None
        try:
            c = pcred.contents
            return ct.string_at(c.CredentialBlob, c.CredentialBlobSize) if c.CredentialBlobSize else b""
        finally:
            self.adv.CredFree(pcred)

    def set(self, service, account, secret):
        self.delete(service, account)
        data = secret.encode("utf-8")
        chunks = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)] or [b""]
        if len(chunks) > MAX_CHUNKS:
            return False
        for i, chunk in enumerate(chunks):
            if not self._write(self._target(service, account, i), account, chunk):
                self.delete(service, account)
                return False
        return True

    def get(self, service, account):
        parts = []
        for i in range(MAX_CHUNKS):
            part = self._read(self._target(service, account, i))
            if part is None:
                break
            parts.append(part)
        if not parts:
            return None
        try:
            return b"".join(parts).decode("utf-8")
        except UnicodeDecodeError:
            return None

    def delete(self, service, account):
        gone = False
        for i in range(MAX_CHUNKS):
            if self.adv.CredDeleteW(self._target(service, account, i), 1, 0):
                gone = True
            elif i > 0:
                break
        return gone


# ── Linux: Secret Service ──────────────────────────────────────────────────────────

class KeyringStore(CredentialStore):
    kind = "keyring"
    persistent = True

    def __init__(self, module):
        self.k = module

    def set(self, service, account, secret):
        try:
            self.k.set_password(service, account, secret)
            return True
        except Exception:                      # noqa: BLE001 - any backend error means "not stored"
            return False

    def get(self, service, account):
        try:
            return self.k.get_password(service, account)
        except Exception:                      # noqa: BLE001
            return None

    def delete(self, service, account):
        try:
            self.k.delete_password(service, account)
            return True
        except Exception:                      # noqa: BLE001
            return False


class SecretToolStore(CredentialStore):
    """libsecret's command-line tool. The secret goes in on standard input, never in an argument."""
    kind = "secret-tool"
    persistent = True

    def __init__(self, run=subprocess.run, exe="secret-tool"):
        self.run, self.exe = run, exe

    def _attrs(self, service, account):
        return ["service", service, "account", account]

    def set(self, service, account, secret):
        r = self.run([self.exe, "store", f"--label={service} {account}", *self._attrs(service, account)],
                     input=secret.encode("utf-8"), capture_output=True, timeout=20)
        return r.returncode == 0

    def get(self, service, account):
        r = self.run([self.exe, "lookup", *self._attrs(service, account)], capture_output=True, timeout=20)
        return r.stdout.decode("utf-8") if r.returncode == 0 and r.stdout else None

    def delete(self, service, account):
        return self.run([self.exe, "clear", *self._attrs(service, account)], capture_output=True,
                        timeout=20).returncode == 0


def open_store() -> CredentialStore:
    """The best credential manager here, else a memory-only store."""
    if os.environ.get("EQU3_CREDSTORE") == "memory":
        return MemoryStore()
    if sys.platform == "win32":
        try:
            return WindowsStore()
        except Exception:                      # noqa: BLE001
            return MemoryStore()
    try:
        import keyring
        from keyring.backends import fail
        if not isinstance(keyring.get_keyring(), fail.Keyring):
            return KeyringStore(keyring)
    except Exception:                          # noqa: BLE001
        pass
    if shutil.which("secret-tool"):
        return SecretToolStore()
    return MemoryStore()
