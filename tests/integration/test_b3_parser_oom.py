"""Controlled cgroup OOM attribution in a disposable, memory-capped container."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from align_worker import context_parser

pytestmark = pytest.mark.skipif(
    os.environ.get("ALIGN_B3_OOM_INTEGRATION") != "true" or sys.platform != "linux",
    reason="requires an explicitly memory-capped disposable Linux container",
)


def test_confirmed_cgroup_oom_maps_to_parser_memory_limit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    limit_path = Path("/sys/fs/cgroup/memory.max")
    try:
        container_limit = int(limit_path.read_text(encoding="ascii").strip())
    except (OSError, ValueError) as exc:
        pytest.skip(f"cgroup v2 memory limit unavailable: {type(exc).__name__}")
    assert container_limit <= 1024**3, "OOM injection is allowed only in a disposable container capped at 1 GiB"

    # Fault injection is deliberately outside Docling: it checks the worker's
    # OS-confirmed OOM mapping without relying on arbitrary SIGKILL attribution.
    command = (
        "import mmap,os,time; "
        "open('/proc/self/oom_score_adj','w').write('500'); "
        "size=2*1024**3; allocation=mmap.mmap(-1,size); "
        "[allocation.__setitem__(offset,1) for offset in range(0,size,4096)]; "
        "time.sleep(30)"
    )
    real_popen = subprocess.Popen

    def launch(_args: object, *args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        return real_popen([sys.executable, "-c", command], *args, **kwargs)  # noqa: S603 -- bounded synthetic child

    monkeypatch.setattr(context_parser.subprocess, "Popen", launch)
    before = context_parser._cgroup_oom_kills()
    result, failure, exit_code = context_parser._run_child(
        str(tmp_path / "unused.pdf"), "unused.pdf", str(tmp_path / "result.json"),
        8 * 1024**3, 30, 45, poll_interval_seconds=0.02,
    )
    after = context_parser._cgroup_oom_kills()

    assert result is None
    assert failure == "parser_memory_limit"
    assert exit_code == str(-signal.SIGKILL)
    assert before is not None and after is not None and after > before
