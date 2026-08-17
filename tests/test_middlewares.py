"""Tests for Request ID, structured logging, and Rate Limiting middlewares."""

import pytest


@pytest.mark.asyncio
async def test_request_id_middleware(async_client):
    # Test without client header -> auto-generates req_<uuidv7>
    resp = await async_client.get("/health")
    assert resp.status_code == 200
    req_id = resp.headers.get("X-Request-ID")
    assert req_id is not None
    assert req_id.startswith("req_")
    assert resp.json()["meta"]["requestId"] == req_id

    # Test with custom valid client header
    custom_id = "req_019154a2-7b3e-7a1b-9e45-123456789abc"
    resp2 = await async_client.get("/health", headers={"X-Request-ID": custom_id})
    assert resp2.status_code == 200
    assert resp2.headers.get("X-Request-ID") == custom_id
    assert resp2.json()["meta"]["requestId"] == custom_id


@pytest.mark.asyncio
async def test_context_logging_with_request_id(async_client, caplog):
    import logging
    custom_id = "req_019154a2-7b3e-7a1b-9e45-testlogging123"

    with caplog.at_level(logging.INFO):
        resp = await async_client.get("/health", headers={"X-Request-ID": custom_id})
        assert resp.status_code == 200

        # Verify that log records created during the request have the request_id attached
        access_records = [r for r in caplog.records if r.name == "app.api.access"]
        assert len(access_records) > 0
        assert getattr(access_records[0], "request_id", None) == custom_id


@pytest.mark.asyncio
async def test_rate_limiting_on_login(async_client):
    # Attempt 5 logins (limit is 5 requests per 60 seconds)
    for _ in range(5):
        await async_client.post(
            "/api/v1/auth/login",
            json={"email": "wrong@example.com", "password": "WrongPassword!"},
        )

    # 6th login attempt should be blocked with 429 Too Many Requests
    resp_blocked = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "wrong@example.com", "password": "WrongPassword!"},
    )
    assert resp_blocked.status_code == 429
    data = resp_blocked.json()
    assert data["success"] is False
    assert data["error"]["code"] == "RATE_LIMIT_EXCEEDED"
