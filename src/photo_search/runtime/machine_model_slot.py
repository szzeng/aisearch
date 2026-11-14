"""Reentrant model execution slot, shared across Windows processes."""

from __future__ import annotations

import math
import os
import threading
import time
from typing import Any


class MachineModelSlot:
    """Retain RLock semantics while serializing cooperating Windows processes.

    No files, services, or ACL changes are created. An abandoned OS mutex is acquired
    normally, since Windows releases a dead process's ownership.
    """

    def __init__(self, name: str = r"Global\PhotoSearch.ModelExecution.v1") -> None:
        self._local = threading.RLock()
        self._name = name
        self._kernel: Any = None
        self._handle: Any = None
        self._priority_lock = threading.Lock()
        self._priority_marker: MachineModelSlot | None = None

    def owned_by_current_thread(self) -> bool:
        return bool(self._local._is_owned())

    def priority_marker(self) -> MachineModelSlot:
        with self._priority_lock:
            if self._priority_marker is None:
                self._priority_marker = MachineModelSlot(self._name + ".Foreground")
            return self._priority_marker

    def _ensure_windows_mutex(self) -> None:
        # The local RLock is already held. Delaying OS access keeps CPU-only startup
        # independent of GPU-slot permissions and avoids changing any ACL.
        if os.name != "nt" or self._kernel is not None:
            return
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        kernel.ReleaseMutex.restype = wintypes.BOOL
        handle = kernel.CreateMutexW(None, False, self._name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._kernel = kernel
        self._handle = handle

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        if not blocking and timeout != -1:
            raise ValueError("can't specify a timeout for a non-blocking call")
        started = time.monotonic()
        acquired = self._local.acquire(blocking, timeout) if blocking else self._local.acquire(False)
        if not acquired:
            return False
        try:
            self._ensure_windows_mutex()
        except BaseException:
            self._local.release()
            raise
        if self._kernel is None:
            return True
        import ctypes

        if not blocking:
            milliseconds = 0
        elif timeout < 0:
            milliseconds = 0xFFFFFFFF
        else:
            milliseconds = min(0xFFFFFFFE, math.ceil(max(0.0, timeout - (time.monotonic() - started)) * 1000))
        result = self._kernel.WaitForSingleObject(self._handle, milliseconds)
        if result in (0, 0x80):
            return True
        self._local.release()
        if result == 0x102:
            return False
        raise ctypes.WinError(ctypes.get_last_error())

    def release(self) -> None:
        if self._kernel is not None:
            import ctypes

            if not self._kernel.ReleaseMutex(self._handle):
                raise ctypes.WinError(ctypes.get_last_error())
        self._local.release()

    def __enter__(self) -> MachineModelSlot:
        if not self.acquire(timeout=60.0):
            raise TimeoutError("background model slot remained busy for 60 seconds")
        return self

    def __exit__(self, *_: object) -> None:
        self.release()
