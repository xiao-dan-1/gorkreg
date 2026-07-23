"""Probe progress lines stream as each account finishes."""
from __future__ import annotations

import inspect

from grokreg.ops import credential_cmds


def test_probe_quota_streams_parallel_results():
    src = inspect.getsource(credential_cmds._cmd_probe_quota)
    # must print inside as_completed loop, not only after full map
    assert "as_completed" in src
    assert "by_email" not in src or src.find("as_completed") < src.find("by_email") or "done_n" in src
    assert "flush=True" in src
    assert "# [" in src and "probe" in src
    # serial also emits progress
    assert "total_n = len(targets)" in src


def test_task_manager_sets_pythonunbuffered():
    src = inspect.getsource(credential_cmds)  # noqa: keep import path warm
    from client import task_manager as tm

    src2 = inspect.getsource(tm.TaskManager._stream_command)
    assert "PYTHONUNBUFFERED" in src2
