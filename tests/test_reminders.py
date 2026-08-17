"""Tests for scheduled event reminders, Celery beat polling, and auto-cancellation on resolution."""

from datetime import datetime, timedelta, timezone
import pytest
from app.config import Settings
from app.schemas.auth import UserLogin, UserRegister
from app.schemas.reminder import ReminderCreate
from app.schemas.ticket import TicketCreate, TicketStatus, TicketUpdate
from app.schemas.user import UserRole
from app.services.auth_service import AuthService
from app.services.reminder_service import ReminderService
from app.services.ticket_service import TicketService


@pytest.mark.asyncio
async def test_reminder_lifecycle_and_auto_cancellation(async_client, fake_db_pool):
    settings = Settings()
    auth_service = AuthService(settings, fake_db_pool)
    ticket_service = TicketService(fake_db_pool)
    reminder_service = ReminderService(fake_db_pool)

    # Setup Agent
    agent = await auth_service.register_user(
        UserRegister(email="agent_rem@test.com", password="Password123!", full_name="Agent Rem", role=UserRole.AGENT)
    )
    tokens = await auth_service.login_user(UserLogin(email="agent_rem@test.com", password="Password123!"))
    headers = {"Authorization": f"Bearer {tokens.access_token}"}

    # Create Ticket
    ticket = await ticket_service.create_ticket(
        TicketCreate(
            title="Follow up on customer license",
            description="License expires in 48 hours.",
            customer_email="customer_lic@test.com",
        ),
        agent,
    )

    # 1. Schedule a reminder via API
    future_time = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    rem_resp = await async_client.post(
        f"/api/v1/tickets/{ticket.id}/reminders",
        json={"scheduled_for": future_time, "message": "Check license renewal status"},
        headers=headers,
    )
    assert rem_resp.status_code == 201
    assert rem_resp.json()["data"]["status"] == "pending"

    # 2. Schedule a past due reminder directly
    past_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    due_reminder = await reminder_service.create_reminder(
        ticket.id,
        ReminderCreate(scheduled_for=past_time, message="Follow up urgently"),
        agent,
    )

    # 3. Process due reminders while ticket is open -> fires reminder and logs notification
    fired_count = await reminder_service.process_due_reminders()
    assert fired_count == 1

    # Check notification was logged
    notifs = await reminder_service.list_notifications(agent)
    assert len(notifs) == 1
    assert "Follow up urgently" in notifs[0].message

    # Test GET /notifications API endpoint
    notifs_api_resp = await async_client.get("/api/v1/notifications", headers=headers)
    assert notifs_api_resp.status_code == 200
    assert len(notifs_api_resp.json()["data"]) == 1

    # 4. Resolve the ticket -> auto-cancels remaining pending reminders
    await ticket_service.update_ticket(
        ticket.id,
        TicketUpdate(status=TicketStatus.RESOLVED),
        agent,
    )

    # Verify pending reminders for this ticket were cancelled
    reminders = await reminder_service.list_reminders(ticket.id, agent)
    future_rems = [r for r in reminders if r.id != due_reminder.id]
    assert len(future_rems) == 1
    assert future_rems[0].status == "cancelled"
