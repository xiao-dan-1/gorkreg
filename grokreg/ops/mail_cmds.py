"""Mail marks / pool status CLI commands."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Optional

from .. import mail_marks
from .. import logutil

log = logging.getLogger(__name__)

def _cmd_mail_marks_list() -> int:
    marks = mail_marks.load_marks()
    path = mail_marks.marks_path()
    print(f"mail_marks path={path} count={len(marks)}")
    if not marks:
        print("(empty)")
        return 0
    print(f"{'email':<42} {'status':<8} {'code':<12} reason")
    print("-" * 100)
    for em, m in sorted(marks.items()):
        strikes = m.get("timeout_strikes")
        strike_s = f" x{strikes}" if strikes else ""
        print(
            f"{em:<42} {str(m.get('status') or '-'):<8}{strike_s:<4} "
            f"{str(m.get('code') or '-'):<12} {(m.get('reason') or '-')[:60]}"
        )
    return 0


def _cmd_mail_mark(args: argparse.Namespace) -> int:
    em = (args.mail_mark or "").strip()
    if not em or "@" not in em:
        logging.error("用法: --mail-mark email@outlook.com [--mail-mark-reason ...] [--mail-mark-code ...]")
        return 2
    m = mail_marks.mark_email(
        em,
        reason=getattr(args, "mail_mark_reason", "") or "",
        code=getattr(args, "mail_mark_code", "") or "",
        status="dead",
        source="cli",
    )
    print(f"marked {em} status={m.get('status')} code={m.get('code')} reason={m.get('reason')}")
    print(f"path={mail_marks.marks_path()}")
    return 0


def _cmd_mail_unmark(args: argparse.Namespace) -> int:
    em = (args.mail_unmark or "").strip()
    if not em:
        logging.error("用法: --mail-unmark email@outlook.com")
        return 2
    ok = mail_marks.unmark_email(em)
    print(f"{'unmarked' if ok else 'not_found'} {em}")
    return 0 if ok else 1



def _parse_mail_sources_arg(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    parts: list[str] = []
    for chunk in str(raw).replace(";", ",").split(","):
        s = chunk.strip()
        if s:
            parts.append(s)
    return parts or None


def _cmd_mail_pool_status(cfg: dict, args: argparse.Namespace) -> int:
    from ..mail_pool import pool_stats

    sources = _parse_mail_sources_arg(getattr(args, "mail_sources", None))
    exp = getattr(args, "exp_name", None) or None
    st = pool_stats(sources, cfg=cfg, exp_name=exp)
    print(
        f"mail-pool sources={len(st['sources'])} ok={len(st['sources_ok'])} "
        f"missing={len(st['sources_missing'])} pool={st['pool']} free={st['free']}"
    )
    for s in st["sources"]:
        tag = "ok" if s in st["sources_ok"] else "MISSING"
        print(f"  [{tag}] {s}")
    if st["sources_missing"]:
        return 1
    return 0


