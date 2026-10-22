from __future__ import annotations

import os

from align_api import evaluation, robustness


def test_evaluation_metadata_keeps_working_when_sysconf_is_unavailable(monkeypatch) -> None:
    monkeypatch.delattr(os, "sysconf", raising=False)

    assert evaluation._environment_metadata()["host_memory_bytes"] is None


def test_robustness_metadata_keeps_working_when_sysconf_is_unavailable(monkeypatch) -> None:
    monkeypatch.delattr(os, "sysconf", raising=False)

    def no_proc_meminfo(*_args, **_kwargs) -> str:
        raise FileNotFoundError

    monkeypatch.setattr(
        robustness.Path,
        "read_text",
        no_proc_meminfo,
    )

    assert robustness._environment_metadata()["host_or_vm_memory_bytes"] is None
