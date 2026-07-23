"""Shared xAI OTP parsing + Outlook mail-line format (mail backends only)."""
from __future__ import annotations

import re
from typing import Optional

# 主题: "5TT-GLT xAI confirmation code"
XAI_SUBJECT_RE = re.compile(
    r"\b([A-Z0-9]{3}-[A-Z0-9]{3})\b.*xai confirmation code",
    re.I,
)
XAI_CODE_RE = re.compile(r"\b([A-Z0-9]{3}-[A-Z0-9]{3})\b")
XAI_HINTS = ("x.ai", "xai", "grok", "confirmation code", "validate your email")


def parse_mail_line(line: str) -> dict[str, str]:
    """email----password----client_id----refresh_token"""
    raw = (line or "").strip()
    parts = raw.split("----")
    if len(parts) < 4:
        raise ValueError("邮箱格式应为 email----password----client_id----refresh_token")
    email, password, client_id, refresh_token = (p.strip() for p in parts[:4])
    refresh_token = refresh_token.rstrip("$").rstrip()
    if not email or "@" not in email or not refresh_token or not client_id:
        raise ValueError("邮箱行字段不完整")
    return {
        "email": email,
        "password": password,
        "client_id": client_id,
        "refresh_token": refresh_token,
    }


def normalize_xai_code(code: str) -> str:
    """统一成 5TT-GLT 形态；无连字符则尽量插入。"""
    c = (code or "").strip().upper().replace(" ", "")
    if re.fullmatch(r"[A-Z0-9]{3}-[A-Z0-9]{3}", c):
        return c
    bare = c.replace("-", "")
    if re.fullmatch(r"[A-Z0-9]{6}", bare):
        return f"{bare[:3]}-{bare[3:]}"
    return c


def extract_xai_code(subject: str = "", text: str = "") -> Optional[str]:
    blob = f"{subject}\n{text}"
    m = XAI_SUBJECT_RE.search(blob)
    if m:
        return normalize_xai_code(m.group(1))
    m2 = re.match(r"^\s*([A-Z0-9]{3}-[A-Z0-9]{3})\b", subject or "", re.I)
    if m2 and "xai" in (subject or "").lower():
        return normalize_xai_code(m2.group(1))
    low = blob.lower()
    if any(h in low for h in XAI_HINTS):
        plain = re.sub(r"<[^>]+>", " ", blob)
        for m3 in XAI_CODE_RE.finditer(plain.upper()):
            code = m3.group(1)
            if code in {"CREATE", "PLEASE", "THANKS", "IGNORE", "OFFICE"}:
                continue
            return normalize_xai_code(code)
    return None
