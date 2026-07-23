"""Default probe log is compact: progress + fails, not full row table."""
from __future__ import annotations

import inspect

from grokreg.ops import credential_cmds


def test_probe_default_compact_has_emit_and_verbose_flag():
    src = inspect.getsource(credential_cmds._cmd_probe_quota)
    assert "_emit_result" in src
    assert "probe_verbose" in src or "verbose =" in src
    assert "probe compact" in src or "progress + fails" in src
    # default path should not always _print_row before emit
    assert "if verbose or _is_fail" in src or "verbose or _is_fail" in src


def test_cli_has_probe_verbose_flag():
    from grokreg import cli

    src = inspect.getsource(cli)
    assert "--probe-verbose" in src


def test_task_manager_forwards_verbose():
    from client.task_manager import TaskManager
    import inspect as ins

    src = ins.getsource(TaskManager.start_probe_pool)
    assert "probe-verbose" in src
    assert "verbose" in src
