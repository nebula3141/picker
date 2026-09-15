"""System memory probe — lets the in-RAM caches size themselves to the machine.

A 64 GB workstation should keep thousands of thumbnails and a few dozen
full-resolution frames hot; an 8 GB laptop should stay modest. No psutil
dependency: Windows uses GlobalMemoryStatusEx, POSIX uses sysconf / vm_stat.
Results are cached briefly so hot paths can call this freely.
"""
from __future__ import annotations

import os
import sys
import time

_GB = 1024 ** 3
_cache: tuple[float, int, int] | None = None   # (stamp, total, available)
_TTL = 5.0


def _probe() -> tuple[int, int]:
    if sys.platform == "win32":
        try:
            import ctypes

            class _MEMSTATUS(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            st = _MEMSTATUS()
            st.dwLength = ctypes.sizeof(_MEMSTATUS)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return int(st.ullTotalPhys), int(st.ullAvailPhys)
        except Exception:
            pass
    else:
        try:
            page = os.sysconf("SC_PAGE_SIZE")
            total = page * os.sysconf("SC_PHYS_PAGES")
            try:
                avail = page * os.sysconf("SC_AVPHYS_PAGES")
            except (ValueError, OSError):
                avail = total // 2
            # macOS reports only "free" pages here, ignoring reclaimable cache —
            # treat half of total as available so caches aren't starved.
            if sys.platform == "darwin":
                avail = max(avail, total // 2)
            return int(total), int(avail)
        except (ValueError, OSError, AttributeError):
            pass
    return 8 * _GB, 4 * _GB   # conservative fallback


def total_bytes() -> int:
    return _read()[0]


def available_bytes() -> int:
    return _read()[1]


def _read() -> tuple[int, int]:
    global _cache
    now = time.monotonic()
    if _cache is None or now - _cache[0] > _TTL:
        total, avail = _probe()
        _cache = (now, total, avail)
    return _cache[1], _cache[2]


def budget(fraction: float, floor: int, ceiling: int) -> int:
    """A byte budget of ``fraction`` of *currently available* RAM, clamped to
    [floor, ceiling]. Callers re-query periodically so the budget shrinks when
    other apps claim memory and grows back when it's freed."""
    return max(floor, min(ceiling, int(available_bytes() * fraction)))
