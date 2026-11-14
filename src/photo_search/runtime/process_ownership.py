"""Windows process-lifetime ownership for the spawned AI worker.

The API process remains the authority that owns the GPU permit.  A Job Object makes
that ownership executable by the operating system: if the parent exits for any reason,
the child is terminated instead of surviving with the permit's work still in flight.

Before the child is assigned, it waits on a startup gate.  This closes the short
``Process.start()`` -> ``AssignProcessToJobObject()`` race in which a child could
otherwise begin model loading before it is owned by the job.
"""

from __future__ import annotations

import os
from typing import Any

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
_SYNCHRONIZE = 0x00100000
_WAIT_TIMEOUT = 0x00000102
_WAIT_OBJECT_0 = 0x00000000
_INVALID_HANDLE_VALUE = -1


def process_is_alive(pid: int) -> bool:
    """Return whether ``pid`` is still alive without sending a signal."""
    if os.name != "nt":
        try:
            os.kill(int(pid), 0)
        except OSError:
            return False
        return True

    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(_SYNCHRONIZE, False, int(pid))
    if not handle:
        return False
    try:
        return kernel.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT
    finally:
        kernel.CloseHandle(handle)


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _IoCounters(ctypes.Structure):  # type: ignore[no-redef]
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]


    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]


    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]
else:
    class _IoCounters:  # type: ignore[no-redef]
        pass

    class _BasicLimitInformation:  # type: ignore[no-redef]
        pass

    class _ExtendedLimitInformation:  # type: ignore[no-redef]
        pass


class WindowsJobObject:
    """Kill the assigned child when this parent-owned job handle closes."""

    def __init__(self) -> None:
        self._kernel: Any = None
        self._handle: Any = None

    def assign(self, pid: int) -> None:
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        )
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL

        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._kernel, self._handle = kernel, handle
        info = _ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(
            handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error
        process = kernel.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, int(pid)
        )
        if not process or process == _INVALID_HANDLE_VALUE:
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error
        try:
            if not kernel.AssignProcessToJobObject(handle, process):
                error = ctypes.WinError(ctypes.get_last_error())
                self.close()
                raise error
        finally:
            kernel.CloseHandle(process)

    def close(self) -> None:
        kernel, handle = self._kernel, self._handle
        if kernel is not None and handle is not None:
            if not kernel.CloseHandle(handle):
                import ctypes

                raise ctypes.WinError(ctypes.get_last_error())
            self._kernel = None
            self._handle = None

    @property
    def assigned(self) -> bool:
        return self._handle is not None


__all__ = ["WindowsJobObject", "process_is_alive"]
