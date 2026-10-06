from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol, Self


class LockUnavailable(RuntimeError):
    pass


class GitCommonDirectoryReader(Protocol):
    def canonical_common_directory(self, repository: Path) -> Path: ...


class RepositoryMutationLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: object | None = None

    @classmethod
    def for_repository(
        cls, repository: Path, *, git: GitCommonDirectoryReader
    ) -> RepositoryMutationLock:
        common_directory = git.canonical_common_directory(repository).resolve()
        return cls(common_directory / "local-issue-runner.lock")

    @property
    def held(self) -> bool:
        return self._file is not None

    def acquire(self) -> None:
        if self.held:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                if lock_file.seek(0, os.SEEK_END) == 0:
                    lock_file.write(b"\0")
                    lock_file.flush()
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as error:
            lock_file.close()
            raise LockUnavailable(
                f"a mutation-capable runner already holds {self.path}"
            ) from error
        self._file = lock_file

    def release(self) -> None:
        if self._file is None:
            return
        lock_file = self._file
        self._file = None
        try:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0)  # type: ignore[attr-defined]
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
        finally:
            lock_file.close()  # type: ignore[attr-defined]

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


class WindowsKernel(Protocol):
    def create_kill_on_close_job(self) -> int: ...
    def assign_process(self, job_handle: int, process_handle: int) -> None: ...
    def close_handle(self, handle: int) -> None: ...


class _NativeWindowsKernel:
    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("Windows Job Objects are only available on Windows")

    def create_kill_on_close_job(self) -> int:
        import ctypes
        from ctypes import wintypes

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL(  # pyright: ignore[reportAttributeAccessIssue]
            "kernel32", use_last_error=True
        )
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError("CreateJobObjectW failed")
        information = ExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = 0x00002000
        if not kernel32.SetInformationJobObject(
            handle, 9, ctypes.byref(information), ctypes.sizeof(information)
        ):
            kernel32.CloseHandle(handle)
            raise OSError("SetInformationJobObject failed")
        return int(handle)

    def assign_process(self, job_handle: int, process_handle: int) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL(  # pyright: ignore[reportAttributeAccessIssue]
            "kernel32", use_last_error=True
        )
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        if not kernel32.AssignProcessToJobObject(job_handle, process_handle):
            raise OSError("AssignProcessToJobObject failed")

    def close_handle(self, handle: int) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL(  # pyright: ignore[reportAttributeAccessIssue]
            "kernel32", use_last_error=True
        )
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        if not kernel32.CloseHandle(handle):
            raise OSError("CloseHandle failed")


class WindowsJobObject:
    def __init__(self, *, kernel: WindowsKernel | None = None) -> None:
        self._kernel = kernel or _NativeWindowsKernel()
        self._handle = self._kernel.create_kill_on_close_job()

    def assign(self, *, process_handle: int) -> None:
        if self._handle is None:
            raise RuntimeError("job object is closed")
        self._kernel.assign_process(self._handle, process_handle)

    def close(self) -> None:
        if self._handle is not None:
            handle, self._handle = self._handle, None
            self._kernel.close_handle(handle)
