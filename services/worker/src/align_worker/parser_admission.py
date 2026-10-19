from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from redis import Redis

from align_api.config import Settings

_ACQUIRE_SCRIPT = """
local expired = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
for _, id in ipairs(expired) do
  redis.call('HDEL', KEYS[2], id)
  redis.call('SREM', KEYS[3], id)
  redis.call('ZREM', KEYS[1], id)
end
local used = 0
local values = redis.call('HVALS', KEYS[2])
for _, value in ipairs(values) do used = used + tonumber(value) end
if used + tonumber(ARGV[2]) > tonumber(ARGV[3]) then return {0, 0, 'parser_capacity_unavailable'} end
if ARGV[4] == '1' and redis.call('SCARD', KEYS[3]) >= tonumber(ARGV[5]) then
  return {0, 0, 'parser_capacity_unavailable'}
end
local fence = redis.call('INCR', KEYS[4])
redis.call('HSET', KEYS[2], ARGV[6], ARGV[2])
redis.call('ZADD', KEYS[1], ARGV[7], ARGV[6])
if ARGV[4] == '1' then redis.call('SADD', KEYS[3], ARGV[6]) end
return {1, fence, 'acquired'}
"""

_RELEASE_SCRIPT = """
redis.call('HDEL', KEYS[2], ARGV[1])
redis.call('SREM', KEYS[3], ARGV[1])
redis.call('ZREM', KEYS[1], ARGV[1])
return 1
"""


@dataclass(frozen=True)
class AdmissionLease:
    lease_id: str
    fence: int
    memory_bytes: int
    heavy: bool
    node_id: str


def _memory_sources() -> tuple[int | None, int | None]:
    host_available: int | None = None
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                host_available = int(line.split()[1]) * 1024
                break
    except OSError:
        host_available = None
    container_available: int | None = None
    for max_path, current_path in (
        ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.current"),
        ("/sys/fs/cgroup/memory/memory.limit_in_bytes", "/sys/fs/cgroup/memory/memory.usage_in_bytes"),
    ):
        try:
            limit_text = Path(max_path).read_text(encoding="ascii").strip()
            current_text = Path(current_path).read_text(encoding="ascii").strip()
            if limit_text != "max":
                container_available = max(0, int(limit_text) - int(current_text))
                break
        except (OSError, ValueError):
            continue
    return host_available, container_available


def _memory_available() -> int:
    host_available, container_available = _memory_sources()
    if host_available is None and container_available is None:
        return 0
    if host_available is None:
        return container_available or 0
    if container_available is None:
        return host_available
    return min(host_available, container_available)


def acquire_parser_lease(settings: Settings, profile: str) -> tuple[AdmissionLease | None, str]:
    heavy = profile == settings.parser_profile_document
    memory_bytes = settings.parser_document_memory_bytes if heavy else settings.parser_native_memory_bytes
    required = memory_bytes + settings.parser_node_headroom_bytes
    host_available, container_available = _memory_sources()
    if (host_available is None and container_available is None) or (
        host_available is not None and host_available < required
    ) or (container_available is not None and container_available < memory_bytes):
        return None, "parser_capacity_unavailable"
    node_id = settings.parser_node_id
    prefix = settings.redis_queue_prefix
    keys = [
        f"{prefix}:parser:leases:{node_id}",
        f"{prefix}:parser:memory:{node_id}",
        f"{prefix}:parser:heavy:{node_id}",
        f"{prefix}:parser:fence:{node_id}",
    ]
    lease_id = uuid4().hex
    now_ms = int(time.time() * 1000)
    lease_ttl_ms = max(settings.worker_lease_seconds, settings.parser_task_hard_seconds + 30) * 1000
    client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    try:
        acquired, fence, reason = cast(Any, client.eval)(
            _ACQUIRE_SCRIPT,
            len(keys),
            *keys,
            str(now_ms),
            str(memory_bytes),
            str(settings.parser_node_memory_budget_bytes),
            "1" if heavy else "0",
            str(settings.parser_heavy_max_per_node),
            lease_id,
            str(now_ms + lease_ttl_ms),
        )
    finally:
        client.close()
    if int(acquired) != 1:
        return None, str(reason)
    return AdmissionLease(lease_id, int(fence), memory_bytes, heavy, node_id), "acquired"


def release_parser_lease(settings: Settings, lease: AdmissionLease) -> None:
    prefix = settings.redis_queue_prefix
    keys = [
        f"{prefix}:parser:leases:{lease.node_id}",
        f"{prefix}:parser:memory:{lease.node_id}",
        f"{prefix}:parser:heavy:{lease.node_id}",
    ]
    client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    try:
        client.eval(_RELEASE_SCRIPT, len(keys), *keys, lease.lease_id)
    finally:
        client.close()
