"""One writer at a time on Dado's authenticated browsers.

WHY THIS EXISTS (2026-08-10, when the Discord lane was added). Until now Dado
had exactly one chat surface, so her own reasoning was effectively
single-threaded: two of her turns could not overlap. Adding a second lane
(Discord alongside Telegram) removes that accident. Hermes keys every
mutual-exclusion primitive by SESSION KEY, and the platform name is part of the
key, so a Telegram DM and a Discord DM run as two genuinely concurrent turns in
two threads of one gateway process. That is exactly what Rachad asked for -- and
it is safe everywhere except here.

THE HAZARD, PRECISELY. The authenticated UI sessions are singletons: Zoho Books/
Inventory on CDP 127.0.0.1:9228 and WordPress on 127.0.0.1:9229, each a
long-lived Edge window started by hand. The write tools do NOT launch a browser;
they ATTACH to the running one with playwright's connect_over_cdp and then drive
an EXISTING tab -- `wordpress_plugin_deployment_tool` takes contexts[0].pages[0]
outright, and `zoho_inventory_classification_tool` takes the first page whose URL
is under the Zoho /app origin and navigates it. Two concurrent turns therefore
receive THE SAME PAGE OBJECT. Because the Zoho classification path works by
driving Zoho's own native settings UI and clicking its real Save button, a click
that lands after the other lane has navigated is a live business write on the
wrong screen. Nothing upstream prevents it: the CDP endpoint has no notion of
ownership, and the tools' own replay locks are keyed per PLAN, so two DIFFERENT
plans touching the same browser never contend.

WHAT THIS IS NOT. This is not a company wall or a capability restriction -- it
takes nothing away and blocks no operation Dado could perform before. It is a
mutex: the second writer waits, and if the first is genuinely long-running it is
told plainly who holds the browser and for how long, instead of silently
interleaving clicks into someone else's form.

*** THE EXCLUSION IS A WINDOWS NAMED MUTEX, NOT A LOCK FILE. THIS IS LOAD-BEARING. ***
The first cut of this module used an O_EXCL lock file with PID-liveness
reclamation, and a review reproduced two defects in it that matter for something
guarding live money writes:

  1. RECLAIM WAS CHECK-THEN-ACT. One process read a stale record, was descheduled,
     and a second process reclaimed and legitimately took the lane; the first then
     resumed and unlinked the SECOND's live lock, and both ended up inside the
     with-body at once -- exactly the interleaved-clicks corruption this exists to
     prevent. Reproduced with real subprocesses and an injected >5s stall: two
     holders, nine seconds of overlap.
  2. IT COULD ORPHAN FOREVER. A holder killed between claim and release left a
     file that was only reclaimed if its PID was dead; a reused PID number made
     the lane permanently unclaimable, and the documented HARD_STALE backstop was
     never actually consulted on the acquire path.

A Windows named mutex has neither failure mode, because the kernel owns it:
acquisition is atomic by construction, and a holder that dies cannot wedge the
lane. Death is handled by one of two kernel routes, and both are tested: if
another process still holds a handle, the object survives and the next waiter is
told it inherited an abandoned mutex (WAIT_ABANDONED, recorded in the descriptor
as took_over_from_dead_holder); if the dead holder had the only handle, the
object is destroyed with it and the next caller simply creates a fresh one.
Either way there is no reclaim path to race and nothing to orphan. That is why
the primitive changed rather than the guard being patched.

The JSON file next to it is now PURELY INFORMATIONAL -- it answers "who is
holding this and why" for the refusal message and for lane_status(). It is never
the thing that grants or denies the lane, so a stale or missing descriptor can
mislead a human but can never let two writers in.

DESIGN NOTES, each one earned:

* ACQUIRE BEFORE THE SIDE EFFECT. Callers must take this lock BEFORE the plan's
  one-attempt commit lock. The commissioned write tools permanently lock a plan
  on any failure or indeterminate result, so "could not get the browser" must be
  a refusal that happens EARLY and costs Rachad nothing, never a mid-write abort
  that burns the plan.

* RE-ENTRANT. A command wraps its whole flow and the helpers it calls open their
  own browser sessions -- WordPress activation in particular re-opens a session
  for its emergency rollback, and the hold must span that gap or the other lane
  could take the browser at exactly the moment a half-activated plugin needed
  rolling back. Windows mutex ownership is per THREAD and recursive, so nesting
  is handled by the kernel; the depth counter here exists only so the descriptor
  file is written once and removed once.

* THE MUTEX NAME INCLUDES THE LOCK DIRECTORY. Tests point LOCK_DIR at a temp dir;
  without this the kernel object would still be global and a test run would
  contend with the live browser lane.

* FAIL OPEN IS NOT AN OPTION HERE. Unlike hermes' own turn lease -- which fails
  open on timeout because a wedged chat turn is worse than a rare double-run --
  this guards live money writes. It raises.
"""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import json
import os
import secrets
import threading
import time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# The two authenticated singletons, named by the browser they own rather than by
# the tool that happens to be driving -- several tools share each one.
LANES: dict[str, int] = {
    "zoho": 9228,
    "wordpress": 9229,
}

LOCK_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "FRPDepot-Dado" / "ui-lane-locks"

# How long a second writer waits before giving up. Long enough to ride out a
# normal page load or a short form fill; far short of a full plugin deployment,
# because past that point the honest answer is "come back when it is done".
DEFAULT_WAIT_SECONDS = 90.0

# (thread ident, lane) -> nesting depth. Bookkeeping for the descriptor file
# only; the kernel mutex is what actually excludes.
_REENTRY: dict[tuple[int, str], int] = {}

_WAIT_OBJECT_0 = 0x00000000
_WAIT_ABANDONED = 0x00000080
_WAIT_TIMEOUT = 0x00000102
_WAIT_FAILED = 0xFFFFFFFF


class UiLaneBusy(RuntimeError):
    """The other lane is driving this browser. Nothing was attempted."""


class UiLaneLockError(RuntimeError):
    """The lock itself could not be operated (bad lane name, no Win32 mutex)."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_lane(lane: str) -> None:
    if lane not in LANES:
        raise UiLaneLockError(
            f"Unknown browser lane {lane!r}. Known lanes: {', '.join(sorted(LANES))}."
        )


def _descriptor_path(lane: str) -> Path:
    _require_lane(lane)
    return LOCK_DIR / f"{lane}.lock.json"


def _mutex_name(lane: str) -> str:
    """Kernel object name, scoped to LOCK_DIR so tests cannot touch the live lane.

    "Local\\" keeps it inside the logon session, which is correct: every Dado
    process runs as the same user in the same session.
    """
    scope = hashlib.sha256(str(LOCK_DIR).casefold().encode("utf-8")).hexdigest()[:16]
    return f"Local\\FRPDepot-UI-Lane-{lane}-{scope}"


def _kernel32():
    try:
        library = ctypes.WinDLL("kernel32", use_last_error=True)
    except (AttributeError, OSError) as exc:  # pragma: no cover - non-Windows
        raise UiLaneLockError(
            "The browser lane lock needs a Windows named mutex and this is not "
            "Windows. Refusing rather than running two writers unprotected."
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


def _read_descriptor(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_descriptor(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Informational only, so a write failure must never deny a lane we hold.
    with contextlib.suppress(OSError):
        path.write_text(json.dumps(record, ensure_ascii=True, indent=2) + "\n",
                        encoding="utf-8")


def _describe(record: dict[str, Any] | None) -> str:
    if not record:
        return "another operation (it left no description)"
    purpose = record.get("purpose") or "an unnamed operation"
    pid = record.get("pid")
    since = record.get("acquired_utc") or "an unknown time"
    return f"{purpose} (pid {pid}, holding since {since})"


def _pid_is_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    except Exception:
        return True


@contextlib.contextmanager
def ui_browser_lock(
    lane: str,
    *,
    purpose: str,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
) -> Iterator[None]:
    """Hold the named authenticated browser exclusively for the duration.

    Raises UiLaneBusy -- before doing anything at all -- when the other lane is
    still driving it. Take this BEFORE any plan commit lock so a refusal is free.
    """
    _require_lane(lane)
    path = _descriptor_path(lane)

    # Nested on this thread: the kernel mutex is recursive for the owning thread,
    # so re-acquiring would succeed anyway. Count it here so the descriptor is
    # written once and, crucially, is NOT removed by the inner exit mid-command.
    held_key = (threading.get_ident(), lane)
    if _REENTRY.get(held_key):
        _REENTRY[held_key] += 1
        try:
            yield
        finally:
            _REENTRY[held_key] -= 1
        return

    library = _kernel32()
    handle = library.CreateMutexW(None, False, _mutex_name(lane))
    if not handle:
        raise UiLaneLockError(
            f"Could not create the {lane} browser lock "
            f"(Win32 error {ctypes.get_last_error()})."
        )

    timeout_ms = int(max(0.0, wait_seconds) * 1000)
    try:
        result = library.WaitForSingleObject(handle, timeout_ms)
    except Exception:
        library.CloseHandle(handle)
        raise

    if result == _WAIT_TIMEOUT:
        holder = _describe(_read_descriptor(path))
        library.CloseHandle(handle)
        raise UiLaneBusy(
            f"The {lane} browser is already being driven by {holder}. Nothing was "
            f"attempted and nothing was changed -- no plan was locked and no write "
            f"was sent. Wait for that operation to finish and run this again. "
            f"(Both of Dado's chat lanes share this one authenticated browser "
            f"window on CDP 127.0.0.1:{LANES[lane]}, so only one may drive it.)"
        )
    if result == _WAIT_FAILED:
        error = ctypes.get_last_error()
        library.CloseHandle(handle)
        raise UiLaneLockError(f"Waiting for the {lane} browser lock failed (Win32 {error}).")
    if result not in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
        library.CloseHandle(handle)
        raise UiLaneLockError(f"Unexpected wait result {result} for the {lane} browser lock.")

    # WAIT_ABANDONED means the previous holder died without releasing. The kernel
    # has handed us ownership regardless -- this is the orphan case, and it needs
    # no reclaim logic, no PID guessing and no stale-age heuristic.
    _write_descriptor(path, {
        "pid": os.getpid(),
        "nonce": secrets.token_hex(8),
        "lane": lane,
        "cdp_port": LANES[lane],
        "purpose": purpose,
        "acquired_utc": _now_iso(),
        "took_over_from_dead_holder": result == _WAIT_ABANDONED,
    })

    _REENTRY[held_key] = 1
    try:
        yield
    finally:
        _REENTRY.pop(held_key, None)
        with contextlib.suppress(OSError):
            path.unlink()
        try:
            library.ReleaseMutex(handle)
        finally:
            library.CloseHandle(handle)


def lane_status(lane: str) -> dict[str, Any]:
    """Read-only view of who holds a lane. Diagnostics only.

    This reads the INFORMATIONAL descriptor, not the kernel mutex, so treat it as
    a hint for humans rather than as proof of the lane's state.
    """
    path = _descriptor_path(lane)
    if not path.exists():
        return {"lane": lane, "held": False}
    record = _read_descriptor(path)
    alive = _pid_is_alive((record or {}).get("pid"))
    return {
        "lane": lane,
        "held": True,
        "alive": alive,
        "stale": not alive,
        "purpose": (record or {}).get("purpose"),
        "pid": (record or {}).get("pid"),
        "acquired_utc": (record or {}).get("acquired_utc"),
    }
