from __future__ import annotations

from typing import Any

import pytest

from align_api.config import Settings
from align_worker import parser_admission


class _RedisStub:
    def __init__(self, result: list[Any]) -> None:
        self.result = result
        self.eval_calls: list[tuple[Any, ...]] = []
        self.closed = False

    def eval(self, *args: Any) -> list[Any]:
        self.eval_calls.append(args)
        return self.result

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("host_available", "container_available"),
    [
        (9 * 1024**3 - 1, 8 * 1024**3),
        (9 * 1024**3, 8 * 1024**3 - 1),
        (None, None),
        (None, 8 * 1024**3),
    ],
)
def test_document_profile_fails_closed_without_required_capacity(
    monkeypatch: pytest.MonkeyPatch,
    host_available: int | None,
    container_available: int | None,
) -> None:
    settings = Settings(_env_file=None)
    monkeypatch.setattr(parser_admission, "_memory_sources", lambda: (host_available, container_available))
    monkeypatch.setattr(
        parser_admission.Redis,
        "from_url",
        classmethod(lambda _cls, *_args, **_kwargs: pytest.fail("capacity rejection must precede lease access")),
    )

    lease, reason = parser_admission.acquire_parser_lease(settings, settings.parser_profile_document)

    assert lease is None
    assert reason == "parser_capacity_unavailable"


def test_document_profile_acquires_lease_only_after_capacity_check(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None)
    redis_stub = _RedisStub([1, 42, b"acquired"])
    required_host_bytes = settings.parser_document_memory_bytes + settings.parser_node_headroom_bytes
    monkeypatch.setattr(
        parser_admission,
        "_memory_sources",
        lambda: (required_host_bytes, settings.parser_document_memory_bytes),
    )
    monkeypatch.setattr(
        parser_admission.Redis,
        "from_url",
        classmethod(lambda _cls, *_args, **_kwargs: redis_stub),
    )

    lease, reason = parser_admission.acquire_parser_lease(settings, settings.parser_profile_document)

    assert reason == "acquired"
    assert lease is not None
    assert lease.heavy is True
    assert lease.memory_bytes == settings.parser_document_memory_bytes
    assert lease.fence == 42
    assert redis_stub.closed is True
    assert len(redis_stub.eval_calls) == 1


def test_cgroup_capacity_credits_only_clean_inactive_file_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    contents = {
        "/proc/meminfo": "MemAvailable: 10000000 kB\n",
        "/sys/fs/cgroup/memory.max": str(9 * 1024**3),
        "/sys/fs/cgroup/memory.current": str(2 * 1024**3),
        "/sys/fs/cgroup/memory.stat": f"inactive_file {1024**3}\nfile_dirty 1024\nfile_writeback 2048\nanon {1024**3}\n",
    }
    def read(path, **_kwargs):
        return contents[path.as_posix()]
    monkeypatch.setattr(parser_admission.Path, "read_text", read)
    host, container = parser_admission._memory_sources()
    assert host == 10_000_000 * 1024
    assert container == 8 * 1024**3 - 3072
    contents["/sys/fs/cgroup/memory.stat"] = "malformed"
    assert parser_admission._memory_sources()[1] == 7 * 1024**3
