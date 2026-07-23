"""Free-usage classification — aligned with ywddd/grok-inspection."""
from __future__ import annotations

from grokreg.oauth.probe_classify import (
    CLASS_HEALTHY,
    CLASS_PERMISSION_DENIED,
    CLASS_PROBE_ERROR,
    CLASS_QUOTA_EXHAUSTED,
    CLASS_RATE_LIMITED,
    CLASS_REAUTH,
    classify_chat_probe,
    classify_from_http_body,
    extract_probe_error,
    is_free_usage_exhausted,
    parse_free_usage_token_pair,
)


def test_is_free_usage_exhausted_code_variants():
    assert is_free_usage_exhausted("free-usage-exhausted", "")
    assert is_free_usage_exhausted("subscription:free-usage-exhausted", "no")
    assert is_free_usage_exhausted(
        "", "You've used all the included free usage for model x"
    )
    assert is_free_usage_exhausted(
        "", "Included free usage has been exhausted"
    )
    assert not is_free_usage_exhausted("", "rate limit exceeded")
    assert not is_free_usage_exhausted("rate_limited", "too many requests")
    assert not is_free_usage_exhausted("", "")


def test_extract_nested_and_subscription_body():
    body = (
        '{"code":"subscription:free-usage-exhausted",'
        '"error":"You\'ve used all the included free usage for model '
        "grok-4.5-build-free for now. Usage resets over a rolling 24-hour "
        'window — tokens (actual/limit): 2012994/2000000. Upgrade..."}'
    )
    parsed = extract_probe_error(body)
    assert parsed["code"] == "subscription:free-usage-exhausted"
    assert "included free usage" in parsed["message"].lower()
    pair = parse_free_usage_token_pair(parsed["message"])
    assert pair == {"used": 2012994, "limit": 2000000}


def test_classify_free_usage_even_on_429():
    body = (
        '{"code":"subscription:free-usage-exhausted",'
        '"error":"You\'ve used all the included free usage for model x. '
        'tokens (actual/limit): 100/100"}'
    )
    got = classify_from_http_body(429, body)
    assert got["classification"] == CLASS_QUOTA_EXHAUSTED
    assert got["free_usage_exhausted"] is True
    assert got["usage_exhausted"] is True
    assert got["free_usage_tokens"] == {"used": 100, "limit": 100}


def test_classify_bare_429_is_rate_limited_not_quota():
    got = classify_chat_probe(429, code="", message="")
    assert got["classification"] == CLASS_RATE_LIMITED
    assert got["free_usage_exhausted"] is False
    assert got["usage_exhausted"] is False

    got2 = classify_from_http_body(429, '{"error":"rate limit exceeded"}')
    assert got2["classification"] == CLASS_RATE_LIMITED
    assert got2["free_usage_exhausted"] is False


def test_classify_healthy_2xx():
    got = classify_chat_probe(200)
    assert got["classification"] == CLASS_HEALTHY
    assert got["free_usage_exhausted"] is False
    assert got["usage_exhausted"] is False


def test_classify_reauth_401():
    got = classify_chat_probe(401, message="token is expired")
    assert got["classification"] == CLASS_REAUTH
    assert got["usage_exhausted"] is False


def test_classify_permission_403():
    got = classify_from_http_body(
        403, '{"code":"permission-denied","error":"Access denied"}'
    )
    assert got["classification"] == CLASS_PERMISSION_DENIED


def test_classify_request_error():
    got = classify_chat_probe(0, request_error="timeout/err: boom")
    assert got["classification"] == CLASS_PROBE_ERROR


def test_extract_error_object_shape():
    body = '{"error":{"code":"free-usage-exhausted","message":"used all the included free usage"}}'
    parsed = extract_probe_error(body)
    assert parsed["code"] == "free-usage-exhausted"
    assert is_free_usage_exhausted(parsed["code"], parsed["message"])
    got = classify_from_http_body(429, body)
    assert got["classification"] == CLASS_QUOTA_EXHAUSTED


def test_extract_ignores_huge_healthy_body_as_message():
    # healthy responses must not pollute error_message with full body
    huge = '{"id":"x","choices":[{"message":{"content":"' + ("a" * 500) + '"}}]}'
    parsed = extract_probe_error(huge)
    assert parsed["code"] == ""
    assert parsed["message"] == ""


def test_probe_quota_exports_classify():
    from grokreg.oauth import (
        classify_chat_probe as c1,
        is_free_usage_exhausted as c2,
        probe_quota,
    )

    assert callable(probe_quota) and callable(c1) and callable(c2)
