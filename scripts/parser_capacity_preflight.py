"""Fail before rollout when the host cannot preserve parser memory headroom."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys


def available_host_bytes() -> int:
    if os.name == "nt":
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_uint32),
                ("dwMemoryLoad", ctypes.c_uint32),
                ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("GlobalMemoryStatusEx failed")
        return int(status.ullAvailPhys)

    try:
        with open("/proc/meminfo", encoding="ascii") as meminfo:
            for line in meminfo:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass

    try:
        return int(os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError) as exc:
        raise RuntimeError("could not read available physical memory") from exc


def check_capacity(available: int, document_bytes: int, headroom_bytes: int) -> dict[str, int | bool]:
    required = document_bytes + headroom_bytes
    return {
        "document_memory_bytes": document_bytes,
        "host_headroom_bytes": headroom_bytes,
        "required_host_available_bytes": required,
        "host_available_bytes": available,
        "container_memory_limit_bytes": document_bytes + 1_073_741_824,
        "accepted": available >= required,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-bytes", type=int, default=4_294_967_296)
    parser.add_argument("--headroom-bytes", type=int, default=1_073_741_824)
    args = parser.parse_args()
    if args.document_bytes <= 0 or args.headroom_bytes < 0:
        parser.error("memory values must be positive (headroom may be zero)")
    try:
        report = check_capacity(available_host_bytes(), args.document_bytes, args.headroom_bytes)
    except (OSError, RuntimeError) as exc:
        print(json.dumps({"accepted": False, "diagnosis": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if report["accepted"] else 2


if __name__ == "__main__":
    sys.exit(main())
