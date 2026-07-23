"""最小 gRPC-web (+proto) 编解码 — 仅覆盖 AuthManagement string 字段。"""
from __future__ import annotations

import struct
from typing import Any, Dict, List, Tuple

WT_VARINT = 0
WT_FIXED64 = 1
WT_LEN = 2
WT_FIXED32 = 5


def encode_varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("varint must be non-negative")
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _tag(field_no: int, wire_type: int) -> bytes:
    return encode_varint((field_no << 3) | wire_type)


def encode_string(field_no: int, text: str) -> bytes:
    raw = text.encode("utf-8")
    return _tag(field_no, WT_LEN) + encode_varint(len(raw)) + raw


def encode_bytes(field_no: int, raw: bytes) -> bytes:
    """Length-delimited field (embedded message or opaque bytes)."""
    return _tag(field_no, WT_LEN) + encode_varint(len(raw)) + raw


def encode_create_session_request(
    email: str,
    password: str,
    *,
    turnstile_token: str,
    castle_request_token: str = "",
) -> bytes:
    """AuthManagement/CreateSession body (ref: xconsole oauth_protocol).

    Wire (2026 reverse-eng):
      credentials { email_and_password { email=1 password=2 } } = field 1
      anti_abuse_token { turnstile=1 castle=2 } = field 4
    """
    email_pw = encode_string(1, email) + encode_string(2, password)
    credentials = encode_bytes(1, email_pw)  # Credentials.emailAndPassword
    req = encode_bytes(1, credentials)  # CreateSession.credentials
    anti = encode_string(1, turnstile_token or "") + encode_string(
        2, castle_request_token or ""
    )
    req += encode_bytes(4, anti)
    return req


def encode_message(fields: List[Tuple[int, str]]) -> bytes:
    out = bytearray()
    for field_no, value in fields:
        out += encode_string(field_no, value)
    return bytes(out)


def _read_varint(data: bytes, i: int) -> Tuple[int, int]:
    result = 0
    shift = 0
    while True:
        b = data[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, i
        shift += 7


def decode_message(data: bytes) -> List[Dict[str, Any]]:
    fields: List[Dict[str, Any]] = []
    i = 0
    n = len(data)
    while i < n:
        tag, i = _read_varint(data, i)
        field_no = tag >> 3
        wt = tag & 0x07
        if wt == WT_VARINT:
            val, i = _read_varint(data, i)
            fields.append({"field": field_no, "type": "varint", "value": val})
        elif wt == WT_FIXED64:
            chunk = data[i : i + 8]
            i += 8
            fields.append(
                {
                    "field": field_no,
                    "type": "fixed64",
                    "hex": chunk.hex(),
                }
            )
        elif wt == WT_LEN:
            ln, i = _read_varint(data, i)
            chunk = data[i : i + ln]
            i += ln
            try:
                s = chunk.decode("utf-8")
                if s.isprintable():
                    fields.append({"field": field_no, "type": "string", "value": s})
                    continue
            except UnicodeDecodeError:
                pass
            fields.append({"field": field_no, "type": "bytes", "hex": chunk.hex(), "len": ln})
        elif wt == WT_FIXED32:
            chunk = data[i : i + 4]
            i += 4
            fields.append({"field": field_no, "type": "fixed32", "hex": chunk.hex()})
        elif wt in (3, 4):
            # Deprecated protobuf groups (start=3 / end=4). End-group has no payload.
            # Soft-skip so a stray group delimiter does not hard-kill the whole frame;
            # callers still treat empty/odd messages as business failure when needed.
            if wt == 3:
                # start-group: skip until matching end-group or give up cleanly
                depth = 1
                while i < n and depth:
                    tag2, i = _read_varint(data, i)
                    wt2 = tag2 & 0x07
                    if wt2 == 3:
                        depth += 1
                    elif wt2 == 4:
                        depth -= 1
                    elif wt2 == WT_VARINT:
                        _, i = _read_varint(data, i)
                    elif wt2 == WT_FIXED64:
                        i += 8
                    elif wt2 == WT_FIXED32:
                        i += 4
                    elif wt2 == WT_LEN:
                        ln, i = _read_varint(data, i)
                        i += ln
                    else:
                        raise ValueError(f"unsupported wire type {wt2} at offset {i}")
            # wt==4 end-group alone: nothing to consume
            continue
        else:
            raise ValueError(f"unsupported wire type {wt} at offset {i}")
    return fields


def frame_request(message: bytes) -> bytes:
    return b"\x00" + struct.pack(">I", len(message)) + message


def parse_response(body: bytes) -> Dict[str, Any]:
    messages: List[List[Dict[str, Any]]] = []
    trailers: Dict[str, str] = {}
    i = 0
    n = len(body)
    while i + 5 <= n:
        flag = body[i]
        length = struct.unpack(">I", body[i + 1 : i + 5])[0]
        payload = body[i + 5 : i + 5 + length]
        i += 5 + length
        if flag & 0x80:
            for line in payload.decode("utf-8", "replace").split("\r\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    trailers[k.strip().lower()] = v.strip()
        else:
            messages.append(decode_message(payload))
    grpc_status = int(trailers["grpc-status"]) if "grpc-status" in trailers else None
    return {"messages": messages, "trailers": trailers, "grpc_status": grpc_status}