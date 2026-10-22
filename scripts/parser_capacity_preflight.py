"""Check available RAM from inside the Linux Docker VM that runs the parser."""
from __future__ import annotations

import argparse
import json
import os
import sys


def available_linux_bytes() -> int:
    """Read Linux VM capacity; Windows host RAM is deliberately not a fallback."""
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
        raise RuntimeError("run this preflight inside the Linux Docker environment") from exc


def check_capacity(
    available: int,
    docker_total_bytes: int,
    document_bytes: int,
    headroom_bytes: int,
) -> dict[str, int | bool]:
    required = document_bytes + headroom_bytes
    return {
        "document_memory_bytes": document_bytes,
        "headroom_bytes": headroom_bytes,
        "required_docker_available_bytes": required,
        "docker_linux_available_bytes": available,
        "docker_linux_total_bytes": docker_total_bytes,
        "container_memory_limit_bytes": document_bytes + 1_073_741_824,
        "accepted": available >= required and docker_total_bytes >= document_bytes + 1_073_741_824,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-bytes", type=int, default=4_294_967_296)
    parser.add_argument("--headroom-bytes", type=int, default=1_073_741_824)
    parser.add_argument("--docker-total-bytes", type=int, required=True)
    args = parser.parse_args()
    if args.document_bytes <= 0 or args.headroom_bytes < 0 or args.docker_total_bytes <= 0:
        parser.error("memory values must be positive (headroom may be zero)")
    try:
        report = check_capacity(
            available_linux_bytes(), args.docker_total_bytes, args.document_bytes, args.headroom_bytes
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"accepted": False, "diagnosis": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if report["accepted"] else 2


if __name__ == "__main__":
    sys.exit(main())
