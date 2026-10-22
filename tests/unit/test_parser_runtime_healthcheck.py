from __future__ import annotations

from align_worker import runtime_check


def test_worker_healthcheck_pings_only_the_local_destination_with_two_second_timeout(monkeypatch) -> None:
    calls: list[tuple[int, list[str]]] = []
    destination = f"celery@{runtime_check.socket.gethostname()}"
    previous_timeout = runtime_check.celery_app.conf.broker_connection_timeout
    previous_retries = runtime_check.celery_app.conf.broker_connection_max_retries

    def ping(*, timeout: int, destination: list[str]):
        assert runtime_check.celery_app.conf.broker_connection_timeout == 2
        assert runtime_check.celery_app.conf.broker_connection_max_retries == 0
        calls.append((timeout, destination))
        return [{destination[0]: {"ok": "pong"}}]

    monkeypatch.setattr(runtime_check.celery_app.control, "ping", ping)

    assert runtime_check.worker_responding() is True
    assert calls == [(2, [destination])]
    assert runtime_check.celery_app.conf.broker_connection_timeout == previous_timeout
    assert runtime_check.celery_app.conf.broker_connection_max_retries == previous_retries


def test_worker_healthcheck_fails_closed_when_ping_times_out(monkeypatch) -> None:
    def ping(**_kwargs):
        raise TimeoutError("synthetic broker timeout")

    monkeypatch.setattr(runtime_check.celery_app.control, "ping", ping)
    assert runtime_check.worker_responding() is False
