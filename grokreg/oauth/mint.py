"""Protocol device-code mint → RT/AT → auth.json."""
from __future__ import annotations

import logging
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

from ..errors import MintError
from .constants import ACCOUNTS, AUTH, BASE_URL, note_device_code_429, set_sso
from .device import _device_code, _poll_token
from .probe_support import _post_write_probe, _resolve_probe_mode


def _post_device_verify(session: Any, *, user_code: str, action: str, referer: str) -> Any:
    origin = AUTH if "auth.x.ai" in action else ACCOUNTS
    return session.post(
        action,
        data={"user_code": user_code},
        allow_redirects=False,
        timeout=45,
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "referer": referer,
            "origin": origin,
            "sec-fetch-site": "same-site",
            "sec-fetch-mode": "navigate",
            "sec-fetch-dest": "document",
        },
    )


def _loc(resp: Any) -> str:
    loc = resp.headers.get("location") or resp.headers.get("Location") or ""
    if loc.startswith("/"):
        loc = AUTH + loc
    return loc


def _page_title(html: str) -> str:
    title_m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.I | re.S)
    return re.sub(r"\s+", " ", (title_m.group(1) if title_m else "")).strip()[:80]


def _is_cf_challenge(html: str) -> bool:
    h = (html or "").lower()
    return (
        "attention required" in h
        or "cf-browser-verification" in h
        or ("cloudflare" in h and ("challenge" in h or "attention required" in h))
    )


def _post_device_approve(
    session: Any,
    *,
    user_code: str,
    referer: str,
    action_url: str | None = None,
    form_data: dict[str, str] | None = None,
) -> Any:
    """POST device approve on auth.x.ai (or scraped form action)."""
    url = (action_url or f"{AUTH}/oauth2/device/approve").strip()
    if url.startswith("/"):
        url = AUTH + url
    data = form_data or {
        "user_code": user_code,
        "action": "allow",
        "principal_type": "User",
        "principal_id": "",
    }
    if "action" not in data:
        data["action"] = "allow"
    if "user_code" not in data and user_code:
        data["user_code"] = user_code
    origin = AUTH if "auth.x.ai" in url else ACCOUNTS
    return session.post(
        url,
        data=data,
        allow_redirects=False,
        timeout=45,
        headers={
            "content-type": "application/x-www-form-urlencoded",
            "referer": referer,
            "origin": origin,
            "sec-fetch-site": "same-site",
            "sec-fetch-mode": "navigate",
            "sec-fetch-dest": "document",
        },
    )


def _approve_done_loc(resp: Any) -> str:
    """Return Location if approve redirected to device/done; else empty."""
    loc = resp.headers.get("location") or resp.headers.get("Location") or ""
    if loc and "done" in loc.lower():
        return loc
    return ""


def mint(
    email: str,
    sso: str,
    *,
    proxy: str,
    auth_path: Path | str | None = "auth.json",
    packs: list[str] | None = None,
    out_dir: Path | None = None,
    probe_mode: str = "models",
    probe_chat: bool | None = None,
) -> dict[str, Any]:
    """Protocol device-code mint → RT/AT → auth.json (ledger).

    Hot path (efficiency / CF-safe):
      device/code → POST device/verify → POST auth device/approve → token poll
    Fallback: GET consent HTML form only if direct approve misses done.
    Verify fallback: GET device page + form action if direct verify misses.
    """
    t0 = time.time()
    from curl_cffi import requests as cr

    logging.info("protocol mint start: %s", email)
    session = cr.Session(impersonate="chrome131", proxies={"http": proxy, "https": proxy})
    try:
        return _mint_with_session(
            session,
            email=email,
            sso=sso,
            proxy=proxy,
            auth_path=auth_path,
            packs=packs,
            out_dir=out_dir,
            probe_mode=probe_mode,
            probe_chat=probe_chat,
            t0=t0,
        )
    finally:
        try:
            session.close()
        except Exception:
            pass


def _mint_with_session(
    session: Any,
    *,
    email: str,
    sso: str,
    proxy: str,
    auth_path: Path | str | None,
    packs: list[str] | None,
    out_dir: Path | None,
    probe_mode: str,
    probe_chat: bool | None,
    t0: float,
) -> dict[str, Any]:
    set_sso(session, sso)

    last_verify_err: str | None = None
    device_code_str = ""
    poll_interval: float | None = None
    page_url = ""
    user_code = ""

    for mint_attempt in range(1, 4):
        # 1. device/code (prefer shared curl session — same TLS as verify/poll)
        dc = _device_code(proxy, session=session)
        user_code = dc["user_code"]
        device_code_str = dc["device_code"]
        # RFC 8628 interval (seconds) — drive token poll
        if dc.get("interval") is not None:
            try:
                poll_interval = max(0.2, float(dc["interval"]))
            except (TypeError, ValueError):
                poll_interval = None
        logging.debug("device code user_code=%s interval=%s", user_code, poll_interval)

        device_url = f"{ACCOUNTS}/oauth2/device?user_code={urllib.parse.quote(user_code)}"
        verify_url = f"{AUTH}/oauth2/device/verify"

        # 2a. HOT PATH: direct POST verify (skip HTML scrape GET)
        r2 = _post_device_verify(
            session, user_code=user_code, action=verify_url, referer=device_url
        )
        loc = _loc(r2)

        if loc and "rate_limited" in loc.lower():
            note_device_code_429()
            last_verify_err = f"device/verify rate_limited loc={loc[:120]!r}"
            logging.warning(
                "device/verify rate_limited attempt=%s/3; adaptive backoff",
                mint_attempt,
            )
            time.sleep(min(5.0, 1.0 * mint_attempt))
            continue

        if loc and "consent" in loc.lower():
            page_url = loc
            break

        # 2b. FALLBACK: GET device page → form action → POST
        logging.debug(
            "direct verify miss status=%s loc=%r; fallback GET device page",
            r2.status_code,
            loc[:120],
        )
        r = session.get(
            device_url,
            timeout=45,
            headers={
                "sec-fetch-site": "same-origin",
                "sec-fetch-mode": "navigate",
                "sec-fetch-dest": "document",
                "accept": "text/html,application/xhtml+xml",
            },
        )
        html = r.text or ""
        get_loc = r.headers.get("location") or r.headers.get("Location") or ""
        if "rate_limited" in (get_loc + html[:2000]).lower() or (
            getattr(r, "url", "") and "rate_limited" in str(getattr(r, "url", "")).lower()
        ):
            note_device_code_429()
            last_verify_err = f"device GET rate_limited status={r.status_code}"
            logging.warning(
                "device page rate_limited attempt=%s/3; adaptive backoff",
                mint_attempt,
            )
            time.sleep(min(5.0, 1.0 * mint_attempt))
            continue

        fm = re.search(
            r"""<form[^>]*action=["']([^"']*device/verify[^"']*)["']""",
            html,
            re.IGNORECASE,
        )
        if fm:
            verify_action = fm.group(1).strip()
            if verify_action.startswith("/"):
                verify_action = AUTH + verify_action
            elif verify_action.startswith("device/"):
                verify_action = f"{AUTH}/oauth2/{verify_action}"
        else:
            verify_action = verify_url

        r2 = _post_device_verify(
            session, user_code=user_code, action=verify_action, referer=device_url
        )
        loc = _loc(r2)
        if (not loc or "consent" not in loc.lower()) and verify_action.rstrip(
            "/"
        ) != verify_url.rstrip("/"):
            r2 = _post_device_verify(
                session, user_code=user_code, action=verify_url, referer=device_url
            )
            loc = _loc(r2)

        if loc and "rate_limited" in loc.lower():
            note_device_code_429()
            last_verify_err = f"device/verify rate_limited loc={loc[:120]!r}"
            logging.warning(
                "device/verify rate_limited attempt=%s/3; adaptive backoff",
                mint_attempt,
            )
            time.sleep(min(5.0, 1.0 * mint_attempt))
            continue

        if not loc or "consent" not in loc.lower():
            title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
            title = re.sub(r"\s+", " ", (title_m.group(1) if title_m else "")).strip()[:80]
            raise MintError(
                "device/verify did not redirect to consent: "
                f"status={r2.status_code} loc={loc[:160]!r} "
                f"page_status={r.status_code} title={title!r} html_len={len(html)}",
                code="mint_verify_redirect",
            )
        page_url = loc
        break
    else:
        raise MintError(
            f"device/verify rate_limited after retries: {last_verify_err}",
            code="mint_rate_limited",
        )

    # 3. Approve device — hot path: POST auth.x.ai/oauth2/device/approve (no consent HTML).
    #    accounts.x.ai consent GET is often Cloudflare 403; form scrape is fallback only.
    r4 = _post_device_approve(session, user_code=user_code, referer=page_url)
    loc4 = _approve_done_loc(r4) or (
        r4.headers.get("location") or r4.headers.get("Location") or ""
    )
    logging.debug(
        "device/approve direct: HTTP %s -> %s",
        r4.status_code,
        (loc4 or "(none)")[:120],
    )

    if "done" not in (loc4 or "").lower():
        # Fallback: GET consent page and POST scraped form (legacy / non-CF envs).
        logging.debug(
            "direct approve miss status=%s loc=%r; fallback GET consent form",
            r4.status_code,
            (loc4 or "")[:120],
        )
        r3 = session.get(
            page_url,
            allow_redirects=False,
            timeout=45,
            headers={
                "sec-fetch-site": "same-origin",
                "sec-fetch-mode": "navigate",
                "sec-fetch-dest": "document",
                "accept": "text/html,application/xhtml+xml",
            },
        )
        html = r3.text or ""
        form_match = re.search(r"<form[^>]*>.*?</form>", html, re.DOTALL | re.IGNORECASE)
        if not form_match:
            title = _page_title(html)
            cf = _is_cf_challenge(html)
            raise MintError(
                "device consent form not found in page HTML: "
                f"status={r3.status_code} title={title!r} html_len={len(html)} "
                f"cf={int(cf)} approve_status={r4.status_code} "
                f"approve_loc={(loc4 or '')[:120]!r}",
                code="mint_consent_form",
            )
        form = form_match.group(0)
        act_match = re.search(r'action\s*=\s*"([^"]*)"', form, re.IGNORECASE)
        form_action = act_match.group(1) if act_match else ""
        inputs = re.findall(r"<input[^>]*>", form, re.IGNORECASE)
        form_data: dict[str, str] = {}
        for inp in inputs:
            nm = re.search(r'name\s*=\s*"([^"]*)"', inp, re.IGNORECASE)
            vm = re.search(r'value\s*=\s*"([^"]*)"', inp, re.IGNORECASE)
            if nm:
                form_data[nm.group(1)] = vm.group(1) if vm else ""
        form_data["action"] = "allow"
        if "user_code" not in form_data and user_code:
            form_data["user_code"] = user_code
        logging.debug(
            "consent form keys=%s action=%s", list(form_data.keys()), form_action
        )
        r4 = _post_device_approve(
            session,
            user_code=user_code,
            referer=page_url,
            action_url=form_action or None,
            form_data=form_data,
        )
        loc4 = r4.headers.get("location") or r4.headers.get("Location") or ""
        logging.debug(
            "consent form POST: HTTP %s -> %s",
            r4.status_code,
            loc4[:120] if loc4 else "(none)",
        )

    if "done" not in (loc4 or "").lower():
        raise MintError(
            "device/approve did not redirect to done: "
            f"status={r4.status_code} loc={(loc4 or '')[:160]!r}",
            code="mint_approve_redirect",
        )

    # 4. Poll token — reuse mint Session (one TLS handshake per account)
    result = _poll_token(
        proxy,
        device_code_str,
        timeout_sec=90,
        interval_sec=poll_interval,
        session=session,
    )
    if not result.get("ok"):
        return {
            "ok": False,
            "email": email,
            "error": f"token_poll:{result.get('error')}",
            "elapsed_sec": round(time.time() - t0, 2),
        }

    token_data = result["token"]
    logging.info(
        "token ok access=%s refresh=%s expires=%s",
        len(token_data.get("access_token", "")),
        len(token_data.get("refresh_token", "")),
        token_data.get("expires_in"),
    )

    # 5. RT/AT → auth.json (default). Packs only if packs=...
    from ..backends.export.xai_pack.schema import build_xai_auth

    payload = build_xai_auth(
        email=email,
        access_token=token_data["access_token"],
        refresh_token=token_data["refresh_token"],
        id_token=token_data.get("id_token"),
        expires_in=token_data.get("expires_in"),
        base_url=BASE_URL,
    )
    from ..backends.export import publish_credentials

    auth_dir = out_dir or (Path.cwd() / "cpa_export")
    pack_list = list(packs or [])
    pub = publish_credentials(
        payload,
        auth_path=auth_path,
        packs=pack_list,
        auth_dir=auth_dir,
    )
    path = pub.get("path")
    logging.info(
        "mint published email=%s pool_key=%s packs=%s path=%s",
        email,
        (pub.get("pool_key") or "-")[:40],
        pack_list or "[]",
        path or "-",
    )

    mode = _resolve_probe_mode(probe_mode, probe_chat)
    probe = _post_write_probe(token_data["access_token"], proxy, mode)

    elapsed = round(time.time() - t0, 2)
    ok = True
    if auth_path is not None and not pub.get("pool_key"):
        ok = False
    return {
        "ok": ok,
        "email": email,
        "path": str(path) if path else None,
        "paths": pub.get("paths") or [],
        "pool_key": pub.get("pool_key"),
        "elapsed_sec": elapsed,
        "probe_mode": mode,
        "chat_ok": probe.get("ok"),
        "chat_model": probe.get("model"),
        "chat_text": probe.get("text"),
        "chat_error": probe.get("error") or "",
        "chat_status": probe.get("status"),
    }
