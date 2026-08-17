import pytest
from app.config import Settings
from app.schemas.auth import UserRegister
from app.schemas.ticket import TicketCreate
from app.schemas.user import UserRole
from app.services.auth_service import AuthService
from app.services.llm_service import MockLLMAdapter, get_llm_adapter
from app.services.ticket_service import TicketService
from app.tasks.triage import _mark_manual_triage_required


@pytest.mark.asyncio
async def test_mock_llm_adapter_classification():
    adapter = MockLLMAdapter()

    # Billing test
    res1 = await adapter.triage(
        title="Payment refund requested",
        description="I was charged twice on my credit card invoice.",
    )
    assert res1.category == "billing"

    # Technical test
    res2 = await adapter.triage(
        title="Critical server crash",
        description="The backend service is down and throwing 500 error exceptions.",
    )
    assert res2.category == "technical"
    assert res2.priority == "high"

    # Account test
    res3 = await adapter.triage(
        title="Password reset not working",
        description="Cannot login to my account, 2fa authentication token failed.",
    )
    assert res3.category == "account"


@pytest.mark.asyncio
async def test_llm_factory():
    s_mock = Settings(LLM_PROVIDER="mock")
    assert isinstance(get_llm_adapter(s_mock), MockLLMAdapter)

    s_ollama = Settings(LLM_PROVIDER="ollama")
    assert get_llm_adapter(s_ollama).base_url == "http://localhost:11434"


@pytest.mark.asyncio
async def test_manual_triage_fallback(fake_db_pool):
    auth_service = AuthService(Settings(), fake_db_pool)
    ticket_service = TicketService(fake_db_pool)

    user = await auth_service.register_user(
        UserRegister(email="agent@test.com", password="Password123!", full_name="Agent", role=UserRole.ADMIN)
    )
    ticket = await ticket_service.create_ticket(
        TicketCreate(
            title="Complex issue",
            description="System is behaving weirdly.",
            customer_email="user@test.com",
        ),
        user,
    )

    # Trigger manual fallback helper
    await _mark_manual_triage_required(str(ticket.id), pool=fake_db_pool)

    updated_ticket = await ticket_service.get_ticket(ticket.id, user)
    assert updated_ticket.manual_triage_required is True
