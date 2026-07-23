"""Mail account pool sources (files of email----pass----cid----rt lines).

Not a receive-code backend — only where unused mail *lines* come from.
Multiple files merge with first-seen email wins (dedupe by address).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence, Union

from .mail import parse_mail_line

log = logging.getLogger(__name__)

PathLike = Union[str, Path]
DEFAULT_MAILS = Path("mails.txt")


def _as_path(p: PathLike) -> Path:
    return p if isinstance(p, Path) else Path(str(p).strip())


def iter_source_paths(
    sources: Optional[Sequence[PathLike]] = None,
    *,
    cfg: Optional[dict[str, Any]] = None,
    env_key: str = "GROK_MAIL_SOURCES",
) -> list[Path]:
    """Resolve ordered mail pool files.

    Priority:
      1) explicit ``sources`` (CLI / caller)
      2) env GROK_MAIL_SOURCES (comma/semicolon separated)
      3) config mail.sources (list of paths)
      4) default mails.txt
    """
    paths: list[Path] = []
    if sources:
        paths = [_as_path(p) for p in sources if str(p).strip()]
    if not paths:
        env = (os.environ.get(env_key) or "").strip()
        if env:
            for part in env.replace(";", ",").split(","):
                part = part.strip()
                if part:
                    paths.append(Path(part))
    if not paths and isinstance(cfg, dict):
        mail_cfg = cfg.get("mail") or {}
        if isinstance(mail_cfg, dict):
            raw = mail_cfg.get("sources")
            if isinstance(raw, str) and raw.strip():
                paths = [Path(raw.strip())]
            elif isinstance(raw, (list, tuple)):
                for item in raw:
                    if item is None:
                        continue
                    s = str(item).strip()
                    if s:
                        paths.append(Path(s))
    if not paths:
        paths = [DEFAULT_MAILS]
    # de-dupe paths (keep order), normalize
    seen: set[str] = set()
    out: list[Path] = []
    for p in paths:
        key = str(p).replace("\\", "/").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def read_mail_lines_from_file(path: Path) -> list[str]:
    """Read valid mail lines from one file; skip comments/blank/invalid."""
    if not path.is_file():
        return []
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if "----" not in s:
            log.warning("mail-pool skip non-line file=%s sample=%s", path.name, s[:40])
            continue
        try:
            parse_mail_line(s)
        except Exception as e:
            log.warning("mail-pool skip bad line file=%s err=%s", path.name, e)
            continue
        lines.append(s)
    return lines


def load_mail_lines(
    sources: Sequence[PathLike],
    *,
    unique: bool = True,
) -> list[str]:
    """Load lines from multiple sources; optional dedupe by email (first wins)."""
    merged: list[str] = []
    seen_email: set[str] = set()
    for src in sources:
        path = _as_path(src)
        for line in read_mail_lines_from_file(path):
            if not unique:
                merged.append(line)
                continue
            try:
                em = parse_mail_line(line)["email"].lower()
            except Exception:
                continue
            if em in seen_email:
                continue
            seen_email.add(em)
            merged.append(line)
    return merged


def free_mail_lines(
    sources: Optional[Sequence[PathLike]] = None,
    *,
    mails_path: Optional[PathLike] = None,
    sso_roster: PathLike = Path("sso_roster.txt"),
    use_marks: bool = True,
    exp_name: Optional[str] = None,
    cfg: Optional[dict[str, Any]] = None,
) -> list[str]:
    """Unused mail lines for batch/exp (not in sso_roster / marks / claimed).

    ``sources`` or legacy single ``mails_path``; else cfg/env/default.
    """
    from . import mail_marks
    from .experiment import claimed_emails

    if sources is None and mails_path is not None:
        sources = [mails_path]
    paths = iter_source_paths(sources, cfg=cfg)

    reg: set[str] = set()
    cli_path = _as_path(sso_roster)
    if cli_path.is_file():
        for line in cli_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            t = line.strip()
            if not t or t.startswith("#"):
                continue
            if "----" in t:
                reg.add(t.split("----", 1)[0].strip().lower())
            elif "@" in t:
                reg.add(t.split()[0].strip().lower())

    marked = mail_marks.skipped_set() if use_marks else set()
    claimed = claimed_emails(exp_name) if exp_name else set()

    free: list[str] = []
    for line in load_mail_lines(paths, unique=True):
        try:
            email = parse_mail_line(line)["email"].lower()
        except Exception:
            continue
        if email in reg or email in marked or email in claimed:
            continue
        free.append(line)
    return free


def pool_stats(
    sources: Optional[Sequence[PathLike]] = None,
    *,
    cfg: Optional[dict[str, Any]] = None,
    exp_name: Optional[str] = None,
    use_marks: bool = True,
) -> dict[str, Any]:
    """Small summary for CLI / logs (no secrets)."""
    paths = iter_source_paths(sources, cfg=cfg)
    existing = [p for p in paths if p.is_file()]
    missing = [str(p) for p in paths if not p.is_file()]
    all_lines = load_mail_lines(paths, unique=True)
    free = free_mail_lines(paths, cfg=cfg, exp_name=exp_name, use_marks=use_marks)
    return {
        "sources": [str(p) for p in paths],
        "sources_ok": [str(p) for p in existing],
        "sources_missing": missing,
        "pool": len(all_lines),
        "free": len(free),
    }
