"""Lightweight ops logging: dual-channel, kv events, # progress lines.

Channels:
  - stderr  → process events (logging / logutil.kv)
  - stdout  → human progress (# [n/N]) + result tables / summary

Contract:
  - event name + key=value fields; secret-like keys redacted
  - optional status icon (✓ ✗ ▸ …); --ascii-log for OK/FAIL
  - run_id auto-attached when set via new_run_id()
  - progress lines start with "# " for easy rg '^# '
"""
from __future__ import annotations

import logging
import sys
import re
import threading
import time
import uuid
from contextvars import ContextVar
from typing import Any, Mapping

# Event names (documented; not hard-enforced)
EVENTS = (
    "proxy-preflight",
    "batch",
    "register",
    "cpa-upload",
    "sub2api-upload",
    "mail",
    "proxy",
    "mint",
    "refresh",
    "remint",
    "export",
    "summary",
)

_SECRET_KEYS = re.compile(
    r"(password|passwd|secret|token|authorization|cookie|refresh|api[_-]?key)",
    re.I,
)

# status → (unicode, ascii)
_ICONS: dict[str, tuple[str, str]] = {
    "ok": ("✓", "OK"),
    "fail": ("✗", "FAIL"),
    "run": ("▸", ">"),
    "skip": ("○", "skip"),
    "warn": ("!", "WARN"),
    "refresh": ("↻", "R"),
    "remint": ("✦", "M"),
    "upload": ("↑", "^"),
    "start": ("●", "*"),
    "done": ("■", "="),
}

# outcome kind → icon key
_KIND_ICON: dict[str, str] = {
    "refresh_ok": "refresh",
    "remint_ok": "remint",
    "remint_fail": "fail",
    "remint_skip_no_sso": "skip",
    "fail_other": "fail",
    "ok": "ok",
    "fail": "fail",
    "skip": "skip",
    "upload_ok": "upload",
    "mint_ok": "ok",
    "mint_fail": "fail",
    "register_ok": "ok",
    "register_fail": "fail",
}

_run_id: ContextVar[str] = ContextVar("grokreg_run_id", default="")
_ascii_mode: ContextVar[bool] = ContextVar("grokreg_ascii_log", default=False)
_progress_lock = threading.Lock()


def new_run_id() -> str:
    """Start a CLI run; subsequent kv lines include run_id=…"""
    rid = uuid.uuid4().hex[:8]
    _run_id.set(rid)
    return rid


def get_run_id() -> str:
    return _run_id.get() or ""


def set_ascii_log(enabled: bool) -> None:
    _ascii_mode.set(bool(enabled))


def icon(name: str) -> str:
    """Return status icon; empty string if unknown."""
    pair = _ICONS.get((name or "").strip().lower())
    if not pair:
        return ""
    return pair[1] if _ascii_mode.get() else pair[0]


def icon_for_kind(kind: str) -> str:
    return icon(_KIND_ICON.get((kind or "").strip().lower(), "run"))


def _fmt_val(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        if abs(v) >= 100 or v == 0:
            return str(int(v)) if v == int(v) else f"{v:.1f}"
        return f"{v:.1f}"
    if isinstance(v, int):
        return str(v)
    s = str(v).replace("\n", " ").replace("\r", " ").strip()
    if not s:
        return "-"
    if any(ch.isspace() for ch in s) or "=" in s:
        s = s.replace('"', "'")
        return f'"{s[:160]}"'
    return s[:160]


def kv(event: str, **fields: Any) -> str:
    """Build `event k=v k=v` line. Drops None; redacts secret-like keys; injects run_id."""
    parts = [event.strip() or "event"]
    rid = get_run_id()
    if rid and "run_id" not in fields:
        parts.append(f"run_id={rid}")
    for k, v in fields.items():
        if v is None:
            continue
        key = str(k)
        if _SECRET_KEYS.search(key):
            continue
        parts.append(f"{key}={_fmt_val(v)}")
    return " ".join(parts)


def log_event(
    logger: logging.Logger | None,
    level: int,
    event: str,
    **fields: Any,
) -> str:
    """Emit one structured line to stderr logging; return the message."""
    status = fields.pop("icon", None) or fields.pop("status_icon", None)
    msg = kv(event, **fields)
    if status:
        ic = icon(str(status)) if len(str(status)) > 2 else str(status)
        if ic:
            msg = f"{ic} {msg}"
    (logger or logging.getLogger("grokreg")).log(level, "%s", msg)
    return msg


def info(event: str, **fields: Any) -> str:
    return log_event(None, logging.INFO, event, **fields)


def warning(event: str, **fields: Any) -> str:
    return log_event(None, logging.WARNING, event, **fields)


def error(event: str, **fields: Any) -> str:
    return log_event(None, logging.ERROR, event, **fields)


def debug(event: str, **fields: Any) -> str:
    return log_event(None, logging.DEBUG, event, **fields)


def progress_line(
    done: int,
    total: int,
    *,
    kind: str = "",
    email: str = "",
    counters: Mapping[str, Any] | None = None,
    rate: float | None = None,
    eta_s: float | None = None,
    elapsed_s: float | None = None,
) -> str:
    """Human progress for stdout: ``# [n/N] ✓ kind email · k=v · rate …``"""
    ic = icon_for_kind(kind) if kind else icon("run")
    kind_s = (kind or "-").strip()
    em = (email or "").strip()
    if len(em) > 36:
        em = em[:33] + "…"
    parts = [f"# [{int(done)}/{int(total)}]"]
    if ic:
        parts.append(ic)
    parts.append(kind_s)
    if em:
        parts.append(em)
    body = " ".join(parts)
    bits: list[str] = []
    if counters:
        for k, v in counters.items():
            if v is None:
                continue
            bits.append(f"{k}={_fmt_val(v)}")
    trail: list[str] = []
    if rate is not None:
        trail.append(f"{rate:.2f}/s")
    if eta_s is not None:
        trail.append(f"eta={eta_s:.0f}s")
    if elapsed_s is not None:
        trail.append(f"elapsed={elapsed_s:.0f}s")
    if bits:
        body += "  · " + " ".join(bits)
    if trail:
        body += "  · " + " ".join(trail)
    return body


def print_progress(
    done: int,
    total: int,
    *,
    kind: str = "",
    email: str = "",
    counters: Mapping[str, Any] | None = None,
    rate: float | None = None,
    eta_s: float | None = None,
    elapsed_s: float | None = None,
    every: int | None = None,
    force: bool = False,
) -> str | None:
    """Print a # progress line to stdout (thread-safe). Returns line or None if skipped.

    Sparse rule when every is None:
      total<=100 → every line; else every 5th + first + last + force.
    """
    if total <= 0:
        return None
    if not force:
        if every is not None and every > 1:
            if done not in (1, total) and done % every != 0:
                return None
        elif total > 100:
            if done not in (1, total) and done % 5 != 0:
                return None
    line = progress_line(
        done,
        total,
        kind=kind,
        email=email,
        counters=counters,
        rate=rate,
        eta_s=eta_s,
        elapsed_s=elapsed_s,
    )
    with _progress_lock:
        print(line, flush=True)
    return line


def done_footer(event: str, **fields: Any) -> str:
    """Final machine-friendly stdout line: ``# done event k=v``."""
    parts = [f"# done {event.strip() or 'job'}"]
    rid = get_run_id()
    if rid:
        parts.append(f"run_id={rid}")
    for k, v in fields.items():
        if v is None:
            continue
        if _SECRET_KEYS.search(str(k)):
            continue
        parts.append(f"{k}={_fmt_val(v)}")
    line = " ".join(parts)
    with _progress_lock:
        print(line, flush=True)
    return line



_SPINNER_UNICODE = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
_SPINNER_ASCII = ("|", "/", "-", "\\")


class LineSpinner:
    """Docker/npm-style single-line spinner (\\r refresh, no line spam).

    with LineSpinner("setup proxy") as sp:
        do_work()
    """

    def __init__(self, label: str = "", *, interval: float = 0.12) -> None:
        self.label = (label or "").strip()
        self.interval = max(0.05, float(interval))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame = 0
        self._t0 = 0.0
        self._active = False
        self._tty = bool(getattr(sys.stdout, "isatty", lambda: False)())

    def _frames(self) -> tuple[str, ...]:
        return _SPINNER_ASCII if _ascii_mode.get() else _SPINNER_UNICODE


    def _write_cr(self, msg: str) -> None:
        try:
            sys.stdout.write("\r" + msg.ljust(78)[:120])
            sys.stdout.flush()
        except Exception:
            pass

    def _clear_cr(self) -> None:
        try:
            sys.stdout.write("\r" + " " * 78 + "\r")
            sys.stdout.flush()
        except Exception:
            pass

    def _run(self) -> None:
        frames = self._frames()
        while not self._stop.wait(self.interval):
            ch = frames[self._frame % len(frames)]
            self._frame += 1
            el = time.time() - self._t0
            self._write_cr(f"  {ch} {self.label}  {el:.0f}s")

    def start(self) -> "LineSpinner":
        if self._active:
            return self
        self._active = True
        self._t0 = time.time()
        if not self._tty:
            with _progress_lock:
                print(f"  {self._frames()[0]} {self.label} ...", flush=True)
            return self
        self._stop.clear()
        self._write_cr(f"  {self._frames()[0]} {self.label}  0s")
        self._thread = threading.Thread(target=self._run, name="line-spinner", daemon=True)
        self._thread.start()
        return self

    def stop(self, status: str = "ok") -> None:
        if not self._active:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._active = False
        el = time.time() - self._t0 if self._t0 else 0.0
        if not self._tty:
            return
        self._clear_cr()
        st = (status or "ok").lower()
        if st in {"ok", "1", "true", "pass"}:
            ic = icon("ok")
        elif st in {"fail", "0", "false", "error"}:
            ic = icon("fail")
        else:
            ic = icon("skip")
        with _progress_lock:
            print(f"{ic} {self.label}  {el:.1f}s", flush=True)

    def stop_silent(self) -> None:
        """Stop spinner and clear line; no status line (caller prints # progress)."""
        if not self._active:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._active = False
        if self._tty:
            self._clear_cr()

    def __enter__(self) -> "LineSpinner":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop("fail" if exc_type else "ok")




class SlotBoard:
    """Account progress lines in fixed template (cmd-friendly).

    Finished line (always)::

        [OK] [1/10]  register_ok  user@x.com  ·  ok=1 fail=0  · 0.09/s eta=11s elapsed=97s

    Running (TTY small-N board only): spinner replaces [OK]/[>>].
    Large N / non-TTY: stream finished lines only.
    """

    BOARD_MAX = 12

    def __init__(self, total: int, *, width: int = 120, batch_t0: float | None = None) -> None:
        self.total = max(1, int(total))
        self.width = max(80, int(width))
        self._lock = threading.Lock()
        self._tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
        self._ascii = bool(_ascii_mode.get())
        self._stream = (not self._tty) or (self.total > self.BOARD_MAX)
        self._labels = [""] * self.total
        # final: (ok, kind, email, note, slot_elapsed)
        self._final: list[tuple[bool, str, str, str, float] | None] = [None] * self.total
        self._t0 = [0.0] * self.total
        self._active: set[int] = set()
        self._frame = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._drawn = False
        self._ok = 0
        self._fail = 0
        self._batch_t0 = float(batch_t0 if batch_t0 is not None else time.time())

    def _frames(self) -> tuple[str, ...]:
        if self._ascii:
            return ("|", "/", "-", "\\")
        return ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def _tag(self, name: str) -> str:
        # Always width-4 tags for column alignment
        if name == "ok":
            return "[OK]"
        if name == "fail":
            return "[X ]"
        if name == "run":
            return "[>>]"
        if name == "wait":
            return "[  ]"
        return "[  ]"

    def _rates(self) -> tuple[float, float, float]:
        """rate /s, eta s, elapsed s for whole batch."""
        elapsed = max(0.001, time.time() - self._batch_t0)
        done = self._ok + self._fail
        rate = done / elapsed if done else 0.0
        remain = max(0, self.total - done)
        eta = (remain / rate) if rate > 0 else 0.0
        return rate, eta, elapsed

    def _slot(self, idx: int) -> str:
        # "[ 1/30]" fixed width so columns stay aligned up to 999
        tw = max(2, len(str(self.total)))
        return f"[{idx:>{tw}}/{self.total}]"

    def _fmt_finish(
        self,
        *,
        ok: bool,
        idx: int,
        kind: str,
        email: str,
        note: str = "",
    ) -> str:
        # Fixed columns (cmd-friendly):
        # [OK] [ 1/30]  register_ok   email@x.com                 ·  ok=1 fail=0  · 0.09/s eta=11s elapsed=97s
        tag = self._tag("ok" if ok else "fail")  # "[OK]" / "[X ]" — width 4
        slot = self._slot(idx)
        kind_s = (kind or ("register_ok" if ok else "fail")).strip()[:12]
        email_s = (email or "").strip()[:36]
        rate, eta, elapsed = self._rates()
        note_s = (note or "").strip()[:16]
        # kind 12 + email 36 keeps body aligned regardless of kind length
        body = f"{kind_s:<12}  {email_s:<36}"
        if note_s:
            body = f"{body}  {note_s}"
        stats = (
            f"ok={self._ok:<3} fail={self._fail:<3}  ·  "
            f"{rate:5.2f}/s eta={eta:4.0f}s elapsed={elapsed:4.0f}s"
        )
        return f"{tag} {slot}  {body}  ·  {stats}"

    def _fmt_running(self, *, idx: int, email: str, slot_elapsed: float) -> str:
        frames = self._frames()
        ch = frames[self._frame % len(frames)]
        tag = f"[{ch}]" if len(ch) == 1 else self._tag("run")
        # pad tag to 4 like [OK]
        tag = f"{tag:<4}"[:4]
        slot = self._slot(idx)
        email_s = (email or "…").strip()[:36]
        rate, eta, elapsed = self._rates()
        body = f"{'running':<12}  {email_s:<36}"
        stats = (
            f"ok={self._ok:<3} fail={self._fail:<3}  ·  "
            f"slot={slot_elapsed:4.0f}s elapsed={elapsed:4.0f}s"
        )
        return f"{tag} {slot}  {body}  ·  {stats}"

    def _line_text(self, i: int) -> str:
        idx = i + 1
        fin = self._final[i]
        if fin is not None:
            ok, kind, email, note, _el = fin
            return self._fmt_finish(ok=ok, idx=idx, kind=kind, email=email, note=note)
        if i in self._active:
            el = time.time() - self._t0[i] if self._t0[i] else 0.0
            return self._fmt_running(idx=idx, email=self._labels[i], slot_elapsed=el)
        return (
            f"{self._tag('wait')} [{idx}/{self.total}]  waiting  ·  "
            f"ok={self._ok} fail={self._fail}"
        )

    def _paint_all(self) -> None:
        if self._stream or not self._tty:
            return
        try:
            if self._drawn:
                sys.stdout.write(f"\033[{self.total}A")
            for i in range(self.total):
                # clear line then write
                sys.stdout.write("\033[2K" + self._line_text(i) + "\n")
            sys.stdout.flush()
            self._drawn = True
        except Exception:
            pass

    def _loop(self) -> None:
        while not self._stop.wait(0.12):
            if not self._active:
                continue
            self._frame += 1
            with self._lock:
                self._paint_all()

    def start(self) -> "SlotBoard":
        if not self._stream and self._tty:
            with self._lock:
                for i in range(self.total):
                    sys.stdout.write(self._line_text(i) + "\n")
                sys.stdout.flush()
                self._drawn = True
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="slot-board", daemon=True
            )
            self._thread.start()
        return self

    def open(self, index: int, label: str) -> None:
        i = max(1, int(index)) - 1
        if i >= self.total:
            i = self.total - 1
        with self._lock:
            self._labels[i] = (label or "").strip()
            self._t0[i] = time.time()
            self._final[i] = None
            self._active.add(i)
            if not self._stream and self._tty:
                self._paint_all()

    def finish(
        self,
        index: int,
        ok: bool,
        text: str,
        *,
        kind: str | None = None,
    ) -> None:
        """text: email or 'email  · note'. kind: register_ok / dry_fail / …"""
        i = max(1, int(index)) - 1
        if i >= self.total:
            i = self.total - 1
        raw = (text or "").strip()
        email, note = raw, ""
        if "  · " in raw:
            email, note = raw.split("  · ", 1)
        for prefix in ("done  ", "fail  ", "OK  ", "FAIL  ", "running  "):
            if email.startswith(prefix):
                email = email[len(prefix) :]
                break
        kind_s = (kind or "").strip()
        if not kind_s:
            kind_s = "register_ok" if ok else (note or "fail")
        with self._lock:
            el = time.time() - self._t0[i] if self._t0[i] else 0.0
            self._active.discard(i)
            if ok:
                self._ok += 1
            else:
                self._fail += 1
            self._final[i] = (bool(ok), kind_s, email.strip(), note.strip(), el)
            line = self._fmt_finish(
                ok=bool(ok),
                idx=i + 1,
                kind=kind_s,
                email=email.strip(),
                note=note.strip(),
            )
            if not self._stream and self._tty:
                self._paint_all()
            else:
                print(line, flush=True)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        with self._lock:
            if not self._stream and self._tty:
                self._paint_all()



def _enable_windows_console() -> None:
    """UTF-8 + VT sequences so symbols work better in cmd / Windows Terminal."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        for std_id in (-11, -12):
            h = kernel32.GetStdHandle(std_id)
            mode = wintypes.DWORD()
            if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                kernel32.SetConsoleMode(h, mode.value | 0x0001 | 0x0002 | 0x0004)
    except Exception:
        pass
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass


def configure_logging(verbose: bool = False, *, ascii_log: bool = False) -> None:
    """Stderr-only process logs; short timestamp + level + message."""
    _enable_windows_console()
    set_ascii_log(ascii_log)
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
            force=False,
        )
    else:
        root.setLevel(level)
        for h in root.handlers:
            h.setLevel(level)
    if not verbose:
        for name in ("urllib3", "curl_cffi", "httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.WARNING)


def fields_from_mapping(m: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    return {k: m.get(k) for k in keys if k in m}
