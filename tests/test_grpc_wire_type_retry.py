"""gRPC-web parse flake: unsupported wire type must not hard-kill register."""
from __future__ import annotations

from grokreg import grpcweb
from grokreg.errors import classify
from grokreg.client import GrokAuthClient, GrpcResult


def test_decode_skips_group_wire_types():
    """Protobuf wt 3/4 are deprecated groups; skip instead of hard fail when possible."""
    # end-group tag for field 1: (1<<3)|4 = 12 = 0x0c
    fields = grpcweb.decode_message(bytes([0x0C]))
    assert fields == []


def test_parse_response_invalid_wire_type_raises_clear_error():
    """Truly invalid wire type (6) still raises ValueError with wire type message."""
    # gRPC-web data frame: flag=0, length=1, payload wt=6 → unsupported
    body = b"\x00" + (1).to_bytes(4, "big") + bytes([(1 << 3) | 6])
    try:
        grpcweb.parse_response(body)
        raise AssertionError("expected ValueError for wire type 6")
    except ValueError as e:
        assert "wire type" in str(e).lower()


def test_classify_wire_type_is_retryable():
    e = classify(ValueError("unsupported wire type 4 at offset 1"))
    assert e.retryable is True
    assert e.code in {"proxy", "protocol", "create", "grpc_parse"}


def test_classify_parse_error_prefix_retryable():
    e = classify(RuntimeError("parse_error:unsupported wire type 4 at offset 1"))
    assert e.retryable is True


def test_grpc_call_parse_error_returns_result_not_raise():
    """_grpc_call must not raise on parse failure — return ok=False parse_error + retry (up to 3 attempts)."""
    client = GrokAuthClient.__new__(GrokAuthClient)
    client.accounts_origin = "https://accounts.x.ai"
    client.timeout = 5
    client.s = type("S", (), {})()

    # wt=6 invalid → parse raises; client retries then returns parse_error
    bad = b"\x00" + (1).to_bytes(4, "big") + bytes([(1 << 3) | 6])

    class FakeResp:
        status_code = 200
        content = bad

    posts: list[int] = []

    def fake_post(*a, **k):
        posts.append(1)
        return FakeResp()

    client.s.post = fake_post  # type: ignore[attr-defined]
    client._grpc_headers = lambda: {}  # type: ignore[method-assign]

    res = client._grpc_call("CreateEmailValidationCode", [(1, "a@b.com")])
    assert isinstance(res, GrpcResult)
    assert res.ok is False
    assert "parse_error" in (res.error or "").lower() or "wire" in (res.error or "").lower()
    assert len(posts) == 3, f"should retry up to 3 attempts on parse_error, got {len(posts)}"


def test_prod_proxy_retryable_matches_wire_type():
    from scripts.prod_cloudmail_batch import _is_proxy_retryable

    assert _is_proxy_retryable(
        {"ok": False, "error": "exception:unsupported wire type 4 at offset 1"}
    )
    assert _is_proxy_retryable(
        {"ok": False, "error": "create_code:parse_error:unsupported wire type 4"}
    )
    assert not _is_proxy_retryable({"ok": True, "error": None})
    assert not _is_proxy_retryable(
        {"ok": False, "error": "captcha_balance: no keys"}
    )
