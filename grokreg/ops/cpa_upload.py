"""Upload local cpa_export/xai-*.json to CLIProxy Management API.

"CPA" in names is historical slang for CLIProxy auth-file packs (not OAuth).

Official: POST {base}/v0/management/auth-files  (multipart file=@...)
Auth: Authorization: Bearer <secret_key>
Docs: https://help.router-for.me/cn/management/api
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin

from ..errors import ConfigError, GrokRegError
from .. import logutil

log = logging.getLogger(__name__)


def _normalize_base(base_url: str) -> str:
    b = (base_url or "").strip().rstrip("/")
    if not b:
        raise ConfigError("cpa.base_url 为空", code="cpa_config")
    # bare host → https (common when users paste cpa.example.com)
    if "://" not in b:
        b = "https://" + b
    if b.endswith("/v0/management"):
        b = b[: -len("/v0/management")]
    elif b.endswith("/v1"):
        b = b[: -len("/v1")]
    return b.rstrip("/")


def resolve_cpa_settings(cfg: dict) -> tuple[str, str, Path]:
    """Return (base_url, secret_key, local_auth_dir)."""
    cpa = cfg.get("cpa") or {}
    if not isinstance(cpa, dict):
        raise ConfigError("config.cpa 必须是 mapping", code="cpa_config")
    base = _normalize_base(str(cpa.get("base_url") or ""))
    secret = str(cpa.get("secret_key") or "").strip()
    if not secret:
        raise ConfigError(
            "cpa.secret_key 未设置（config.yaml 或环境变量 CPA_SECRET_KEY）",
            code="cpa_config",
        )
    root = Path(cfg.get("_root") or ".")
    auth_dir = Path(str(cpa.get("auth_dir") or "cpa_export"))
    if not auth_dir.is_absolute():
        auth_dir = root / auth_dir
    return base, secret, auth_dir


def list_local_auth_files(auth_dir: Path, only: Optional[str] = None) -> list[Path]:
    if not auth_dir.is_dir():
        raise ConfigError(f"本地 auth_dir 不存在: {auth_dir}", code="cpa_config")
    files = sorted(auth_dir.glob("xai-*.json"))
    if only and only.strip().lower() not in {"all", "*"}:
        q = only.strip().lower()
        # allow email, xai-email.json, or path basename
        if q.endswith(".json"):
            files = [p for p in files if p.name.lower() == q]
        else:
            files = [
                p
                for p in files
                if q in p.name.lower() or p.stem.lower() == f"xai-{q}"
            ]
        if not files:
            raise ConfigError(f"未找到匹配的本地凭证: {only}", code="cpa_config")
    return files


def _mgmt_url(base: str, path: str) -> str:
    # path like /auth-files
    return f"{base}/v0/management{path}"


def _request(
    method: str,
    url: str,
    *,
    secret: str,
    multipart: Any = None,
    timeout: float = 60.0,
) -> tuple[int, Any]:
    """HTTP via curl_cffi. multipart=CurlMime for uploads."""
    try:
        from curl_cffi import requests as crequests
    except ImportError as e:  # pragma: no cover
        raise GrokRegError("需要 curl_cffi", code="cpa_upload", retryable=False) from e

    headers = {"Authorization": f"Bearer {secret}"}
    try:
        if method.upper() == "GET":
            r = crequests.get(url, headers=headers, timeout=timeout, impersonate="chrome131")
        elif method.upper() == "POST":
            r = crequests.post(
                url,
                headers=headers,
                multipart=multipart,
                timeout=timeout,
                impersonate="chrome131",
            )
        elif method.upper() == "DELETE":
            r = crequests.delete(url, headers=headers, timeout=timeout, impersonate="chrome131")
        else:
            raise GrokRegError(f"unsupported method {method}", code="cpa_upload")
    except GrokRegError:
        raise
    except Exception as e:
        raise GrokRegError(str(e), code="cpa_upload", retryable=True, detail=type(e).__name__) from e
    finally:
        # CurlMime must be closed after request
        if multipart is not None:
            try:
                multipart.close()
            except Exception:
                pass

    body: Any
    text = r.text or ""
    try:
        body = r.json() if text else {}
    except Exception:
        body = text
    return int(r.status_code), body


def list_remote_auth_files(base_url: str, secret: str) -> dict[str, Any]:
    status, body = _request("GET", _mgmt_url(base_url, "/auth-files"), secret=secret)
    if status == 401:
        raise GrokRegError("管理密钥无效 (401)", code="cpa_auth", retryable=False, detail=body)
    if status == 403:
        raise GrokRegError(
            f"管理 API 拒绝 (403): {body}",
            code="cpa_forbidden",
            retryable=True,
            detail=body,
        )
    if status >= 400:
        raise GrokRegError(f"list auth-files HTTP {status}: {body}", code="cpa_upload", detail=body)
    return body if isinstance(body, dict) else {"raw": body}


def upload_auth_file(base_url: str, secret: str, path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"文件不存在: {path}", code="cpa_config")
    try:
        from curl_cffi import CurlMime
    except ImportError as e:  # pragma: no cover
        raise GrokRegError("需要 curl_cffi", code="cpa_upload", retryable=False) from e

    # Official: -F 'file=@/path/to/acc1.json'
    mp = CurlMime()
    mp.addpart(
        name="file",
        content_type="application/json",
        filename=path.name,
        local_path=str(path),
    )
    status, body = _request(
        "POST",
        _mgmt_url(base_url, "/auth-files"),
        secret=secret,
        multipart=mp,
    )
    if status == 401:
        raise GrokRegError("管理密钥无效 (401)", code="cpa_auth", retryable=False, detail=body)
    if status == 403:
        raise GrokRegError(
            f"管理 API 拒绝 (403): {body}",
            code="cpa_forbidden",
            retryable=True,
            detail=body,
        )
    if status >= 400:
        raise GrokRegError(
            f"upload {path.name} HTTP {status}: {body}",
            code="cpa_upload",
            retryable=status >= 500,
            detail=body,
        )
    return body if isinstance(body, dict) else {"status": status, "raw": body}



def remote_auth_name_set(body: Any) -> set[str]:
    """Normalize GET /auth-files payload into a set of file basenames."""
    names: set[str] = set()
    files = None
    if isinstance(body, dict):
        files = body.get("files")
        if files is None and isinstance(body.get("data"), list):
            files = body["data"]
    elif isinstance(body, list):
        files = body
    if not isinstance(files, list):
        return names
    for f in files:
        if isinstance(f, str):
            n = f.strip()
        elif isinstance(f, dict):
            n = str(f.get("name") or f.get("id") or f.get("filename") or "").strip()
        else:
            continue
        if not n:
            continue
        # accept path-like; keep basename
        n = Path(n).name
        names.add(n)
        # also bare email stem for loose match later
        if n.lower().startswith("xai-") and n.lower().endswith(".json"):
            names.add(n[4:-5])  # email without xai- / .json
        elif n.lower().endswith(".json"):
            names.add(Path(n).stem)
    return names


def local_files_missing_on_remote(
    local_files: list[Path],
    remote_names: set[str],
) -> list[Path]:
    """Local xai-*.json whose basename (or email stem) is not on remote."""
    remote_l = {x.lower() for x in remote_names}
    missing: list[Path] = []
    for p in local_files:
        name = p.name
        stem = p.stem  # xai-email
        email = stem[4:] if stem.lower().startswith("xai-") else stem
        keys = {name.lower(), stem.lower(), email.lower(), f"{email.lower()}.json"}
        if keys.isdisjoint(remote_l):
            missing.append(p)
    return missing


def upload_all(
    cfg: dict,
    *,
    only: Optional[str] = None,
    dry_run: bool = False,
    jobs: int = 1,
    missing_only: bool = False,
    limit: int | None = None,
    progress: bool = False,
) -> dict[str, Any]:
    base, secret, auth_dir = resolve_cpa_settings(cfg)
    files = list_local_auth_files(auth_dir, only=only)
    jobs = max(1, int(jobs or 1))
    result: dict[str, Any] = {
        "base_url": base,
        "auth_dir": str(auth_dir),
        "total": len(files),
        "local_total": len(files),
        "remote_total": None,
        "skipped_present": 0,
        "missing_only": bool(missing_only),
        "limit": None,
        "ok": 0,
        "failed": 0,
        "items": [],
        "dry_run": dry_run,
        "jobs": jobs,
    }

    if missing_only:
        remote_body = list_remote_auth_files(base, secret)
        remote_names = remote_auth_name_set(remote_body)
        json_names = {n for n in remote_names if str(n).lower().endswith(".json")}
        result["remote_total"] = len(json_names) if json_names else len(remote_names)
        before = len(files)
        files = local_files_missing_on_remote(files, remote_names)
        result["skipped_present"] = before - len(files)
        result["total"] = len(files)
        logutil.info(
            "cpa-upload",
            phase="missing-filter",
            local=before,
            remote=result["remote_total"],
            missing=len(files),
            skipped=result["skipped_present"],
        )

    if limit is not None and int(limit) > 0:
        before = len(files)
        files = files[: int(limit)]
        result["limit"] = int(limit)
        result["total"] = len(files)
        logutil.info(
            "cpa-upload",
            phase="limit",
            before=before,
            limit=int(limit),
            after=len(files),
        )

    import time as _time

    t0 = _time.time()
    ok_n = fail_n = 0
    total_files = len(files)
    if progress:
        print(
            f"{logutil.icon('start')} cpa-upload  run_id={logutil.get_run_id()}  "
            f"total={total_files} jobs={jobs} dry_run={int(dry_run)} "
            f"missing_only={int(missing_only)} base={base}",
            flush=True,
        )
        logutil.info(
            "cpa-upload",
            phase="start",
            total=total_files,
            j=jobs,
            dry_run=int(dry_run),
            icon="start",
        )
        result["_progress_emitted"] = True

    def _emit_progress(done_n: int, name: str, kind: str) -> None:
        if not progress or total_files <= 0:
            return
        elapsed = max(0.001, _time.time() - t0)
        rate = done_n / elapsed
        eta = max(0, total_files - done_n) / rate if rate > 0 else 0.0
        logutil.print_progress(
            done_n,
            total_files,
            kind=kind,
            email=name,
            counters={"ok": ok_n, "fail": fail_n},
            rate=rate,
            eta_s=eta,
            elapsed_s=elapsed,
            force=kind == "fail",
        )

    if dry_run:
        for i, p in enumerate(files, 1):
            result["items"].append({"file": p.name, "ok": True, "dry_run": True})
            result["ok"] += 1
            ok_n += 1
            _emit_progress(i, p.name, "upload_ok")
        return result

    def _one(p: Path) -> dict[str, Any]:
        item: dict[str, Any] = {"file": p.name}
        try:
            body = upload_auth_file(base, secret, p)
            item["ok"] = True
            if isinstance(body, dict):
                item["status"] = body.get("status") or body.get("message") or "ok"
            logutil.debug("cpa-upload", phase="file", ok=1, file=p.name)
        except Exception as e:
            item["ok"] = False
            item["error"] = str(e)
            logutil.error("cpa-upload", phase="file", ok=0, file=p.name, err=str(e)[:160])
        return item

    items: list[dict[str, Any]] = []
    if jobs == 1 or len(files) <= 1:
        for i, p in enumerate(files, 1):
            item = _one(p)
            items.append(item)
            if item.get("ok"):
                ok_n += 1
            else:
                fail_n += 1
            _emit_progress(
                i,
                p.name,
                "upload_ok" if item.get("ok") else "fail",
            )
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=min(jobs, len(files))) as ex:
            futs = {ex.submit(_one, p): p for p in files}
            done_n = 0
            for fut in as_completed(futs):
                p = futs[fut]
                item = fut.result()
                items.append(item)
                done_n += 1
                if item.get("ok"):
                    ok_n += 1
                else:
                    fail_n += 1
                _emit_progress(
                    done_n,
                    p.name,
                    "upload_ok" if item.get("ok") else "fail",
                )
        # stable order by filename for logs/summary
        items.sort(key=lambda it: str(it.get("file") or ""))

    for item in items:
        if item.get("ok"):
            result["ok"] += 1
        else:
            result["failed"] += 1
        result["items"].append(item)
    return result
