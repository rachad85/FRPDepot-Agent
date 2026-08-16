"""Kernel-owned global single-flight guard for WooCommerce tax delivery commits.

The permanent replay lock is per plan, so it cannot prevent two different plans
from entering the non-atomic 17-PUT correction together.  This distinct Windows
named mutex excludes every delivery-tax commit across threads and processes.  A
small JSON descriptor is informational only; the kernel mutex is authoritative.
A dead holder cannot orphan the guard.
"""
from __future__ import annotations

import contextlib
import ctypes
import hashlib
import json
import os
import secrets
import threading
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

LOCK_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "FRPDepot-Dado" / "tax-delivery-commit-lock"
DEFAULT_WAIT_SECONDS = 0.0
_REENTRY: dict[int, int] = {}

_WAIT_OBJECT_0 = 0x00000000
_WAIT_ABANDONED = 0x00000080
_WAIT_TIMEOUT = 0x00000102
_WAIT_FAILED = 0xFFFFFFFF


class TaxDeliveryCommitBusy(RuntimeError):
    """Another delivery-tax commit owns the global guard; nothing was attempted."""


class TaxDeliveryCommitLockError(RuntimeError):
    """The global commit guard itself could not be operated safely."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _descriptor_path() -> Path:
    return LOCK_DIR / "global.lock.json"


def _mutex_name() -> str:
    scope = hashlib.sha256(str(LOCK_DIR).casefold().encode("utf-8")).hexdigest()[:16]
    return f"Local\\FRPDepot-Woo-Tax-Delivery-Commit-{scope}"


def _kernel32():
    try:
        library = ctypes.WinDLL("kernel32", use_last_error=True)
    except (AttributeError, OSError) as exc:  # pragma: no cover - non-Windows
        raise TaxDeliveryCommitLockError(
            "The delivery-tax commit guard requires a Windows named mutex. "
            "Refusing rather than permitting concurrent financial writes."
        ) from exc
    library.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    library.CreateMutexW.restype = wintypes.HANDLE
    library.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    library.WaitForSingleObject.restype = wintypes.DWORD
    library.ReleaseMutex.argtypes = [wintypes.HANDLE]
    library.ReleaseMutex.restype = wintypes.BOOL
    library.CloseHandle.argtypes = [wintypes.HANDLE]
    library.CloseHandle.restype = wintypes.BOOL
    return library


def _read_descriptor() -> dict[str, Any] | None:
    try:
        value = json.loads(_descriptor_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_descriptor(record: dict[str, Any]) -> None:
    path = _descriptor_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.write_text(json.dumps(record, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def _describe(record: dict[str, Any] | None) -> str:
    if not record:
        return "another delivery-tax operation (no descriptor was available)"
    return (
        f"{record.get('purpose') or 'another delivery-tax operation'} "
        f"(pid {record.get('pid')}, since {record.get('acquired_utc') or 'unknown'})"
    )


@contextlib.contextmanager
def tax_delivery_commit_lock(
    *, purpose: str, wait_seconds: float = DEFAULT_WAIT_SECONDS
) -> Iterator[None]:
    """Acquire the global tax-delivery mutex before any per-plan attempt lock."""
    thread_id = threading.get_ident()
    if _REENTRY.get(thread_id):
        _REENTRY[thread_id] += 1
        try:
            yield
        finally:
            _REENTRY[thread_id] -= 1
        return

    library = _kernel32()
    handle = library.CreateMutexW(None, False, _mutex_name())
    if not handle:
        raise TaxDeliveryCommitLockError(
            f"Could not create the delivery-tax mutex (Win32 {ctypes.get_last_error()})."
        )
    timeout_ms = int(max(0.0, wait_seconds) * 1000)
    try:
        result = library.WaitForSingleObject(handle, timeout_ms)
    except Exception:
        library.CloseHandle(handle)
        raise
    if result == _WAIT_TIMEOUT:
        holder = _describe(_read_descriptor())
        library.CloseHandle(handle)
        raise TaxDeliveryCommitBusy(
            "A WooCommerce delivery-tax commit is already running under the global "
            f"single-flight guard: {holder}. Nothing was attempted or changed; no "
            "plan was permanently locked and no credential or network access occurred."
        )
    if result == _WAIT_FAILED:
        error = ctypes.get_last_error()
        library.CloseHandle(handle)
        raise TaxDeliveryCommitLockError(f"Waiting for the delivery-tax mutex failed (Win32 {error}).")
    if result not in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
        library.CloseHandle(handle)
        raise TaxDeliveryCommitLockError(f"Unexpected delivery-tax mutex wait result {result}.")

    _write_descriptor({
        "pid": os.getpid(),
        "nonce": secrets.token_hex(8),
        "purpose": purpose,
        "acquired_utc": _now_iso(),
        "took_over_from_dead_holder": result == _WAIT_ABANDONED,
        "mutex_name_sha256": hashlib.sha256(_mutex_name().encode("utf-8")).hexdigest(),
    })
    _REENTRY[thread_id] = 1
    try:
        yield
    finally:
        _REENTRY.pop(thread_id, None)
        with contextlib.suppress(OSError):
            _descriptor_path().unlink()
        try:
            library.ReleaseMutex(handle)
        finally:
            library.CloseHandle(handle)
