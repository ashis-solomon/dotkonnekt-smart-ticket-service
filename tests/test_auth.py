"""Comprehensive tests for Argon2id hashing, Dual-Token JWT flow, and auth endpoints."""

import pytest
from app.config import get_settings
from app.schemas.auth import UserLogin, UserRegister
from app.schemas.user import UserRole
from app.services.auth_service import AuthService


@pytest.mark.asyncio
async def test_password_hashing(fake_db_pool):
    settings = get_settings()
    service = AuthService(settings, fake_db_pool)

    password = "SuperSecretPassword123!"
    hashed = service.hash_password(password)

    assert hashed != password
    assert service.verify_password(password, hashed) is True
    assert service.verify_password("WrongPassword!", hashed) is False


@pytest.mark.asyncio
async def test_dual_token_flow_and_revocation(fake_db_pool):
    settings = get_settings()
    service = AuthService(settings, fake_db_pool)

    # Register user
    user = await service.register_user(
        UserRegister(
            email="agent@support.com",
            password="SecurePassword123!",
            full_name="Support Agent",
            role=UserRole.AGENT,
        )
    )
    assert user.email == "agent@support.com"

    # Login
    tokens = await service.login_user(
        UserLogin(email="agent@support.com", password="SecurePassword123!")
    )
    assert tokens.access_token is not None
    assert tokens.refresh_token is not None

    # Verify Access Token decode
    payload = service.decode_access_token(tokens.access_token)
    assert payload["sub"] == str(user.id)
    assert payload["email"] == "agent@support.com"
    assert payload["role"] == "agent"

    # Refresh Token Rotation
    new_access, new_refresh, _ = await service.rotate_refresh_token(tokens.refresh_token)
    assert new_access != tokens.access_token
    assert new_refresh != tokens.refresh_token

    # Old refresh token is now revoked
    with pytest.raises(ValueError, match="revoked"):
        await service.rotate_refresh_token(tokens.refresh_token)

    # Logout / Revoke new refresh token
    await service.revoke_refresh_token(new_refresh)
    with pytest.raises(ValueError, match="revoked"):
        await service.rotate_refresh_token(new_refresh)


@pytest.mark.asyncio
async def test_auth_api_endpoints(async_client):
    # 1. Register
    reg_payload = {
        "email": "alice@support.com",
        "password": "Password12345!",
        "full_name": "Alice Support",
        "role": "agent",
    }
    reg_resp = await async_client.post("/api/v1/auth/register", json=reg_payload)
    assert reg_resp.status_code == 201
    data = reg_resp.json()
    assert data["success"] is True
    assert data["data"]["email"] == "alice@support.com"
    assert "requestId" in data["meta"]

    # Duplicate registration should return 409 Conflict
    dup_resp = await async_client.post("/api/v1/auth/register", json=reg_payload)
    assert dup_resp.status_code == 409
    dup_data = dup_resp.json()
    assert dup_data["success"] is False
    assert dup_data["error"]["code"] == "CONFLICT"

    # 2. Login
    login_resp = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "alice@support.com", "password": "Password12345!"},
    )
    assert login_resp.status_code == 200
    login_data = login_resp.json()
    access_token = login_data["data"]["access_token"]
    refresh_token = login_data["data"]["refresh_token"]

    # 3. GET /me with Bearer token
    headers = {"Authorization": f"Bearer {access_token}"}
    me_resp = await async_client.get("/api/v1/auth/me", headers=headers)
    assert me_resp.status_code == 200
    assert me_resp.json()["data"]["email"] == "alice@support.com"

    # 4. Refresh Token
    refresh_resp = await async_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert refresh_resp.status_code == 200
    new_tokens = refresh_resp.json()["data"]
    assert new_tokens["access_token"] is not None
    assert new_tokens["refresh_token"] != refresh_token

    # 5. Logout
    logout_resp = await async_client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": new_tokens["refresh_token"]},
    )
    assert logout_resp.status_code == 200
    assert logout_resp.json()["success"] is True

    # 6. Attempt to use revoked refresh token
    revoked_resp = await async_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": new_tokens["refresh_token"]},
    )
    assert revoked_resp.status_code == 401
    assert revoked_resp.json()["error"]["code"] == "UNAUTHORIZED"

    # 7. Attempt accessing /me with invalid Bearer token
    bad_token_resp = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer invalid.malformed.token"},
    )
    assert bad_token_resp.status_code == 401
    assert bad_token_resp.json()["error"]["code"] == "UNAUTHORIZED"
