"""Tests for server-side Role-Based Access Control (Admin vs Agent) and IDOR prevention."""

import pytest
from app.config import get_settings
from app.schemas.auth import UserLogin, UserRegister
from app.schemas.ticket import TicketCreate
from app.schemas.user import UserRole
from app.services.auth_service import AuthService
from app.services.ticket_service import TicketService


@pytest.mark.asyncio
async def test_agent_and_admin_rbac_boundaries(async_client, fake_db_pool):
    settings = get_settings()
    auth_service = AuthService(settings, fake_db_pool)
    ticket_service = TicketService(fake_db_pool)

    # Create Admin and two Agents
    admin = await auth_service.register_user(
        UserRegister(email="admin@test.com", password="Password123!", full_name="Admin", role=UserRole.ADMIN)
    )
    agent1 = await auth_service.register_user(
        UserRegister(email="agent1@test.com", password="Password123!", full_name="Agent One", role=UserRole.AGENT)
    )
    agent2 = await auth_service.register_user(
        UserRegister(email="agent2@test.com", password="Password123!", full_name="Agent Two", role=UserRole.AGENT)
    )

    # Login both
    admin_tokens = await auth_service.login_user(UserLogin(email="admin@test.com", password="Password123!"))
    agent1_tokens = await auth_service.login_user(UserLogin(email="agent1@test.com", password="Password123!"))
    agent2_tokens = await auth_service.login_user(UserLogin(email="agent2@test.com", password="Password123!"))

    admin_headers = {"Authorization": f"Bearer {admin_tokens.access_token}"}
    agent1_headers = {"Authorization": f"Bearer {agent1_tokens.access_token}"}
    agent2_headers = {"Authorization": f"Bearer {agent2_tokens.access_token}"}

    # Agent 1 creates a ticket assigned to Agent 1
    ticket_data = TicketCreate(
        title="Agent 1 Ticket",
        description="Ticket assigned specifically to agent 1.",
        customer_email="customer1@example.com",
    )
    ticket1 = await ticket_service.create_ticket(ticket_data, agent1)

    # 1. Agent 1 can view their assigned ticket
    resp = await async_client.get(f"/api/v1/tickets/{ticket1.id}", headers=agent1_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["title"] == "Agent 1 Ticket"

    # 2. Agent 2 CANNOT view Agent 1's ticket (403 Forbidden - IDOR check)
    resp_agent2 = await async_client.get(f"/api/v1/tickets/{ticket1.id}", headers=agent2_headers)
    assert resp_agent2.status_code == 403
    assert resp_agent2.json()["success"] is False
    assert resp_agent2.json()["error"]["code"] == "FORBIDDEN"

    # 3. Admin CAN view Agent 1's ticket
    resp_admin = await async_client.get(f"/api/v1/tickets/{ticket1.id}", headers=admin_headers)
    assert resp_admin.status_code == 200
    assert resp_admin.json()["data"]["id"] == str(ticket1.id)

    # 4. Agent 2 CANNOT modify Agent 1's ticket
    patch_resp = await async_client.patch(
        f"/api/v1/tickets/{ticket1.id}",
        json={"priority": "high"},
        headers=agent2_headers,
    )
    assert patch_resp.status_code == 403

    # 5. Agent 2 CANNOT add notes to Agent 1's ticket
    note_resp = await async_client.post(
        f"/api/v1/tickets/{ticket1.id}/notes",
        json={"content": "Malicious unauthorized note"},
        headers=agent2_headers,
    )
    assert note_resp.status_code == 403


@pytest.mark.asyncio
async def test_role_middleware_rejections(async_client, fake_db_pool):
    # 1. Unauthenticated request to protected route -> 401 Unauthorized via Middleware
    resp_no_auth = await async_client.get("/api/v1/auth/me")
    assert resp_no_auth.status_code == 401
    assert resp_no_auth.json()["error"]["code"] == "UNAUTHORIZED"

    # 2. Invalid Token -> 401 Unauthorized via Middleware
    resp_invalid = await async_client.get("/api/v1/auth/me", headers={"Authorization": "Bearer invalid.jwt.token"})
    assert resp_invalid.status_code == 401
    assert resp_invalid.json()["error"]["code"] == "UNAUTHORIZED"

    # 3. Token with invalid role -> 403 Forbidden via Middleware
    import jwt
    import uuid6
    from datetime import datetime, timedelta, timezone
    from app.config import get_settings
    settings = get_settings()

    invalid_role_token = jwt.encode(
        {
            "sub": str(uuid6.uuid7()),
            "email": "hacker@test.com",
            "role": "superhacker", # Invalid role
            "type": "access",
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=30),
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    resp_bad_role = await async_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {invalid_role_token}"})
    assert resp_bad_role.status_code == 403
    assert resp_bad_role.json()["error"]["code"] == "FORBIDDEN"

