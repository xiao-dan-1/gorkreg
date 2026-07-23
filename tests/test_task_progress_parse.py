"""Regression: refresh log counters must match CLI, not progress-line count."""
from __future__ import annotations

from client.task_manager import TaskManager, parse_progress_line


def test_refresh_progress_uses_absolute_counters_not_plus_one():
    line = (
        "# [500/500] R refresh_ok yes4dk9tme@bj01.xdauv.xyz · "
        "refresh_ok=496 remint_ok=0 recovered=496 fail=4 · 6.82/s eta=0s elapsed=73s"
    )
    info = parse_progress_line(line)
    assert info is not None
    assert info["source"] in {"progress_abs", "summary"}
    assert info.get("done") == 500 or info.get("total") == 500 or info["success"] == 496
    assert info["success"] == 496
    assert info["failed"] == 4


def test_refresh_done_summary_line():
    line = (
        "refresh done source=auth.json total=500 refresh_ok=496 remint_ok=0 recovered=496 "
        "remint_fail=0 remint_skip_no_sso=0 fail_other=4 failed=4 j=6 needs_only=1 "
        "skew_min=5.0 limit=500 elapsed=73s"
    )
    info = parse_progress_line(line)
    assert info is not None
    assert info["source"] == "summary"
    assert info["success"] == 496
    assert info["failed"] == 4
    assert info.get("total") == 500


def test_done_footer_refresh_ok_fail():
    line = (
        "# done refresh run_id=31bc5dda total=500 remint_ok=0 recovered=496 "
        "fail=4 wall_s=73.3 j=6 limit=500"
    )
    info = parse_progress_line(line)
    assert info is not None
    assert info["source"] == "summary"
    assert info["success"] == 496
    assert info["failed"] == 4


def test_register_style_ok_fail_on_progress():
    line = "# [2/4] OK register_ok dry001@example.invalid · ok=2 fail=0 · 1.0/s"
    info = parse_progress_line(line)
    assert info is not None
    assert info["source"] in {"progress_abs", "progress"}


def test_many_progress_lines_do_not_imply_success_equals_line_count():
    for n in range(1, 104):
        line = f"# [{n}/500] R refresh_ok user{n}@x.y"
        info = parse_progress_line(line)
        assert info and info["source"] == "progress"
    final = parse_progress_line(
        "refresh done source=auth.json total=500 refresh_ok=496 remint_ok=0 "
        "recovered=496 failed=4"
    )
    assert final["success"] == 496 and final["failed"] == 4


def test_phase_progress_stderr_line_sets_done_total():
    """Bug repro: UI showed 520/1 because stderr phase lines were ignored."""
    line = (
        "19:28:29 INFO refresh run_id=b70257fa phase=progress "
        "done=520 total=892 kind=refresh_ok"
    )
    info = parse_progress_line(line)
    assert info is not None
    assert info["source"] == "progress_abs"
    assert info["done"] == 520
    assert info["total"] == 892


def test_apply_progress_phase_line_fixes_denominator():
    tm = TaskManager()
    tm.state.running = True
    tm.state.kind = "refresh"
    tm.state.total = 1
    tm.state.completed = 0
    info = parse_progress_line(
        "INFO refresh run_id=x phase=progress done=520 total=892 kind=refresh_ok"
    )
    assert info
    tm._apply_progress(info)
    assert tm.state.completed == 520
    assert tm.state.total == 892
    assert tm.state.to_dict()["percent"] > 50
