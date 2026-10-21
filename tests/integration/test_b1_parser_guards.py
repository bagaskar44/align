"""Controlled failure injection: real Redis leases and real Linux child processes."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from redis import Redis

from align_worker import context_parser, parser_admission

pytestmark = pytest.mark.skipif(
    os.environ.get("ALIGN_RUN_INTEGRATION") != "true" or sys.platform != "linux",
    reason="requires isolated Compose Redis and Linux process isolation",
)


def test_atomic_heavy_reservation_and_expired_lease_quarantine():
    prefix = f"b1-lease-test-{uuid4().hex}"
    keys = [f"{prefix}:{kind}" for kind in ("leases", "memory", "heavy", "fence")]
    client = Redis.from_url("redis://redis:6379/0")
    def acquire(identifier):
        now = int(time.time() * 1000)
        return client.eval(parser_admission._ACQUIRE_SCRIPT, 4, *keys,
                           now, 8 * 1024**3, 8 * 1024**3, "1", "1", identifier, now + 180_000)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(acquire, ("first", "second")))
        assert sorted(int(result[0]) for result in results) == [0, 1]
        owner = "first" if int(results[0][0]) else "second"
        client.zadd(keys[0], {owner: 0})
        assert int(acquire("third")[0]) == 0  # expiry is not process death
        assert client.hlen(keys[1]) == 1
        assert client.eval(parser_admission._RENEW_SCRIPT, 1, keys[0], owner, int(time.time() * 1000) + 180_000) == 1
        assert int(acquire("third")[0]) == 0  # renewal does not release memory
        client.eval(parser_admission._RELEASE_SCRIPT, 3, *keys[:3], owner)
        assert int(acquire("third")[0]) == 1
    finally:
        client.delete(*keys)
        client.close()


def _dead(pid: int) -> bool:
    status = Path(f"/proc/{pid}/stat")
    return not status.exists() or status.read_text().split()[2] == "Z"


def test_timeout_kills_the_whole_child_process_group(monkeypatch, tmp_path):
    original_popen = subprocess.Popen
    pid_path = tmp_path / "descendant.pid"
    command = (
        "import pathlib,subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']); "
        f"pathlib.Path({str(pid_path)!r}).write_text(str(p.pid)); time.sleep(30)"
    )
    def launch(_args, **kwargs):
        return original_popen([sys.executable, "-c", command], **kwargs)
    monkeypatch.setattr(context_parser.subprocess, "Popen", launch)
    result, failure, _exit = context_parser._run_child(
        str(tmp_path / "unused.docx"), "unused.docx", str(tmp_path / "result.json"), 2 * 1024**3, 1, 2,
    )
    assert result is None and failure == "parser_timeout"
    assert _dead(int(pid_path.read_text()))


def test_abrupt_parent_death_kills_direct_parser_child():
    command = (
        "import subprocess,sys,time; from align_worker.context_parser import _preexec_limits; "
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'], "
        "start_new_session=True,preexec_fn=_preexec_limits(2147483648,30)); "
        "print(p.pid,flush=True); time.sleep(30)"
    )
    parent = subprocess.Popen([sys.executable, "-c", command], stdout=subprocess.PIPE, text=True)  # noqa: S603
    child_pid = None
    try:
        assert parent.stdout is not None
        child_pid = int(parent.stdout.readline().strip())
        parent.kill()
        parent.wait(timeout=5)
        deadline = time.monotonic() + 3
        while not _dead(child_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert _dead(child_pid)
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait()
        if child_pid and not _dead(child_pid):
            os.killpg(child_pid, signal.SIGKILL)


def test_native_child_address_space_limit_rejects_oversized_allocation():
    # Real RLIMIT_AS exhaustion, without exhausting the host/cgroup or claiming
    # that an arbitrary SIGKILL proves a kernel OOM event.
    command = "import mmap\ntry:\n mmap.mmap(-1,3*1024**3)\nexcept (MemoryError,OSError):\n print('memory_limit_enforced')\nelse:\n raise SystemExit(9)"
    result = subprocess.run([sys.executable, "-c", command], check=True, capture_output=True, text=True,  # noqa: S603
                            timeout=5, preexec_fn=context_parser._preexec_limits(2 * 1024**3, 5))
    assert result.stdout.strip() == "memory_limit_enforced"
