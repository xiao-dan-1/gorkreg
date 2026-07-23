"""Verify failure classification: TLS/request_error must NOT trigger 90s re-mail."""
from __future__ import annotations

from grokreg.pipeline.register import (
    _is_invalid_code_verify_error,
    _is_verify_transport_error,
)


def test_openssl_invalid_library_is_transport_not_invalid_code():
    err = (
        "request_error:Failed to perform, curl: (35) TLS connect error: "
        "error:00000000:invalid library (0):OPENSSL_internal:invalid library (0)."
    )
    assert _is_verify_transport_error(err) is True
    assert _is_invalid_code_verify_error(err) is False


def test_true_invalid_code_still_matches():
    assert _is_invalid_code_verify_error("invalid_code") is True
    assert _is_invalid_code_verify_error("invalid") is True
    assert _is_invalid_code_verify_error("empty_body") is True
    assert _is_invalid_code_verify_error("http=200 grpc=3 invalid code") is True
    assert _is_verify_transport_error("invalid_code") is False


def test_curl56_and_timeout_are_transport():
    assert _is_verify_transport_error(
        "request_error:Failed to perform, curl: (56) Connection closed abruptly."
    )
    assert _is_verify_transport_error(
        "request_error:Failed to perform, curl: (28) Connection timed out after 60007 milliseconds."
    )
    assert _is_invalid_code_verify_error(
        "request_error:Failed to perform, curl: (56) Connection closed abruptly."
    ) is False


def test_wire_parse_is_transport_for_verify_retry():
    err = "parse_error:unsupported wire type 6 at offset 73"
    assert _is_verify_transport_error(err) is True
    assert _is_invalid_code_verify_error(err) is False
