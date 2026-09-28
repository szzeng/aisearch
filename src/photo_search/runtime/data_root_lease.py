"""Cross-process ownership lease for one application data root."""

from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO

from photo_search.config import runtime_dir

LEASE_FILENAME = "service.lease"


class DataRootLeaseBusy(RuntimeError):
    """Another managed process owns the data root."""


class DataRootLease:
    """An advisory, OS-released exclusive lease held by managed processes only."""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream

    def close(self) -> None:
        stream, self._stream = self._stream, None  # type: ignore[assignment]
        if stream is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover - Windows is the supported product target.
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()

    def __enter__(self) -> DataRootLease:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def acquire_data_root_lease(data_root: str | Path) -> DataRootLease:
    """Acquire the non-blocking managed-process lease for ``data_root``.

    The file may persist after a crash; only the OS lock, which is released on process exit,
    represents ownership. This deliberately avoids treating a stale marker as a live process.
    """
    path = runtime_dir(data_root) / LEASE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    try:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:  # pragma: no cover - Windows is the supported product target.
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        stream.close()
        raise DataRootLeaseBusy(str(path)) from error
    return DataRootLease(stream)
