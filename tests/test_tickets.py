"""Tests for Ticket CRUD, Trigram fuzzy search, filters, pagination, and internal notes."""

import pytest
from app.config import get_settings
from app.schemas.auth import UserLogin, UserRegister
from app.schemas.user import UserRole
from app.services.auth_service import AuthService


@pytest.mark.asyncio
async def test_ticket_crud_and_search(async_client, fake_db_pool):
    settings = get_settings()
    auth_service = AuthService(settings, fake_db_pool)

    # Setup Admin user
    admin = await auth_service.register_user(
        UserRegister(email="admin_user@test.com", password="Password123!", full_name="Admin User", role=UserRole.ADMIN)
    )
    tokens = await auth_service.login_user(UserLogin(email="admin_user@test.com", password="Password123!"))
    headers = {"Authorization": f"Bearer {tokens.access_token}"}

    # 1. Create Ticket 1 (Billing issue)
    ticket1_payload = {
        "title": "Cannot download billing invoice for August",
        "description": "The PDF invoice download link gives a 404 error on the payment receipt page.",
        "customer_email": "customer@company.com",
        "priority": "medium",
    }
    t1_resp = await async_client.post("/api/v1/tickets", json=ticket1_payload, headers=headers)
    assert t1_resp.status_code == 201
    t1_data = t1_resp.json()["data"]
    ticket1_id = t1_data["id"]
    assert t1_data["title"] == ticket1_payload["title"]

    # 2. Create Ticket 2 (Technical crash)
    ticket2_payload = {
        "title": "Server crash with 500 internal server error",
        "description": "Production database connection times out when fetching analytics.",
        "customer_email": "dev@company.com",
        "priority": "high",
    }
    t2_resp = await async_client.post("/api/v1/tickets", json=ticket2_payload, headers=headers)
    assert t2_resp.status_code == 201
    ticket2_id = t2_resp.json()["data"]["id"]

    # 3. List all tickets with pagination
    list_resp = await async_client.get("/api/v1/tickets?page=1&page_size=10", headers=headers)
    assert list_resp.status_code == 200
    list_json = list_resp.json()
    assert len(list_json["data"]) == 2
    assert list_json["meta"]["pagination"]["totalRecords"] == 2
    assert list_json["meta"]["pagination"]["totalPages"] == 1

    # 4. Search by keyword "billing"
    search_resp = await async_client.get("/api/v1/tickets?q=billing", headers=headers)
    assert search_resp.status_code == 200
    search_json = search_resp.json()
    assert len(search_json["data"]) == 1
    assert search_json["data"][0]["id"] == ticket1_id

    # 5. Search by keyword "crash"
    search_resp2 = await async_client.get("/api/v1/tickets?q=crash", headers=headers)
    assert search_resp2.status_code == 200
    assert len(search_resp2.json()["data"]) == 1
    assert search_resp2.json()["data"][0]["id"] == ticket2_id

    # 6. Filter by priority "high"
    prio_resp = await async_client.get("/api/v1/tickets?priority=high", headers=headers)
    assert prio_resp.status_code == 200
    assert len(prio_resp.json()["data"]) == 1
    assert prio_resp.json()["data"][0]["id"] == ticket2_id

    # 7. Update ticket status to in_progress
    update_resp = await async_client.patch(
        f"/api/v1/tickets/{ticket1_id}",
        json={"status": "in_progress", "priority": "high"},
        headers=headers,
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["data"]["status"] == "in_progress"
    assert update_resp.json()["data"]["priority"] == "high"

    # 8. Add internal note to ticket
    note_resp = await async_client.post(
        f"/api/v1/tickets/{ticket1_id}/notes",
        json={"content": "Investigating payment gateway logs."},
        headers=headers,
    )
    assert note_resp.status_code == 201
    assert note_resp.json()["data"]["content"] == "Investigating payment gateway logs."

    # 9. List notes for ticket
    notes_list_resp = await async_client.get(f"/api/v1/tickets/{ticket1_id}/notes", headers=headers)
    assert notes_list_resp.status_code == 200
    assert len(notes_list_resp.json()["data"]) == 1
