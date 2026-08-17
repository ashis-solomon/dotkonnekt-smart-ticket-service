"""LLM Adapter Pattern for automated ticket triage and classification."""

from abc import ABC, abstractmethod
import json
import logging
from typing import Literal, Optional
import httpx
from pydantic import BaseModel, Field
from app.config import Settings, get_settings

logger = logging.getLogger("app.services.llm")


class TriageResult(BaseModel):
    summary: str = Field(..., description="Short summary of the customer ticket issue.")
    category: Literal["billing", "technical", "account", "other"] = Field(
        ..., description="Detected category for the ticket."
    )
    priority: Literal["low", "medium", "high"] = Field(
        ..., description="Suggested priority for resolving the ticket."
    )


class BaseLLMAdapter(ABC):
    """Abstract Base Class for LLM Auto-Triage Providers."""

    @abstractmethod
    async def triage(self, title: str, description: str) -> TriageResult:
        """Processes ticket title and description, returning structured classification."""
        pass


class OllamaAdapter(BaseLLMAdapter):
    """Adapter for locally hosted Ollama LLM models (e.g. llama3.2, mistral)."""

    def __init__(self, base_url: str, model: str, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    async def triage(self, title: str, description: str) -> TriageResult:
        prompt = f"""You are an automated support ticket triage assistant.
Classify the customer support ticket into EXACTLY ONE category and ONE priority.

Classification Guidelines:
- "billing": Invoices, charges, receipts, payments, refunds, card issues, subscription pricing.
- "account": Login, passwords, 2FA, profile, registration, permissions, user access.
- "technical": Software bugs, crashes, 500 errors, system outages, broken features, API errors.
- "other": General inquiries, greetings (e.g. "hello", "hi", "test"), feedback, or anything that does not clearly belong to billing, account, or technical.

Priority Guidelines:
- "low": General inquiries, greetings, minor questions, non-blocking requests.
- "medium": Standard user issues.
- "high": Critical blockers, security vulnerabilities, payment processing failures, system-wide outages.

Ticket Title: {title}
Ticket Description: {description}

Respond with a single JSON object. Example:
{{"summary": "Customer sent a general greeting inquiry.", "category": "other", "priority": "low"}}

Do NOT include multiple options or pipe characters. Choose only ONE category and ONE priority.
"""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/api/generate", json=payload)
            if response.status_code != 200:
                try:
                    err_msg = response.json().get("error", response.text)
                except Exception:
                    err_msg = response.text
                raise RuntimeError(f"Ollama error ({response.status_code}): {err_msg}")

            data = response.json()
            raw_response = data.get("response", "{}")
            logger.info("Ollama raw response received: %s", raw_response)

            try:
                parsed = json.loads(raw_response)
            except Exception as e:
                logger.error("Could not parse Ollama response as JSON: %s. Raw was: %s", str(e), raw_response)
                raise ValueError(f"Could not parse Ollama response as JSON: {raw_response}") from e

            # Extract category & priority
            raw_category = str(parsed.get("category", "")).lower().strip()
            raw_priority = str(parsed.get("priority", "")).lower().strip()
            summary = str(parsed.get("summary", "")).strip() or f"{title}: {description[:120]}"

            valid_categories = {"billing", "technical", "account", "other"}
            valid_priorities = {"low", "medium", "high"}

            if raw_category not in valid_categories:
                logger.error("LLM returned invalid category '%s'. Failing triage for manual review.", raw_category)
                raise ValueError(f"LLM returned invalid category '{raw_category}'. Expected one of {valid_categories}")

            if raw_priority not in valid_priorities:
                logger.warning("LLM returned unrecognized priority '%s'. Defaulting to medium.", raw_priority)
                raw_priority = "medium"

            category: Literal["billing", "technical", "account", "other"] = raw_category
            priority: Literal["low", "medium", "high"] = raw_priority

            logger.info("Triage result finalized: category=%s, priority=%s, summary=%s", category, priority, summary)
            return TriageResult(summary=summary, category=category, priority=priority)


class MockLLMAdapter(BaseLLMAdapter):
    """Deterministic, fast mock adapter for offline testing and CI/CD."""

    async def triage(self, title: str, description: str) -> TriageResult:
        combined = f"{title} {description}".lower()

        # Category Detection
        if any(w in combined for w in ["invoice", "bill", "billing", "charge", "refund", "receipt", "payment", "card", "cost"]):
            category: Literal["billing", "technical", "account", "other"] = "billing"
        elif any(w in combined for w in ["login", "password", "auth", "mfa", "2fa", "reset", "email", "profile", "permission", "access", "signup", "account"]):
            category = "account"
        elif any(w in combined for w in ["crash", "bug", "error", "exception", "broken", "fail", "500", "502", "api", "stack", "server"]):
            category = "technical"
        else:
            category = "other"

        # Priority Detection
        if any(w in combined for w in ["urgent", "critical", "outage", "emergency", "immediately", "blocker", "down", "fatal"]):
            priority: Literal["low", "medium", "high"] = "high"
        elif any(w in combined for w in ["slow", "problem", "issue", "warning", "incorrect", "trouble"]):
            priority = "medium"
        else:
            priority = "low"

        first_sentence = description.split(".")[0].strip()
        summary = f"Summary: {title} - {first_sentence}"
        if len(summary) > 200:
            summary = summary[:197] + "..."

        return TriageResult(summary=summary, category=category, priority=priority)


def get_llm_adapter(settings: Optional[Settings] = None) -> BaseLLMAdapter:
    """Factory creating LLM adapter instance based on settings."""
    if settings is None:
        settings = get_settings()

    if settings.LLM_PROVIDER.lower() == "ollama":
        return OllamaAdapter(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.OLLAMA_MODEL,
            timeout=settings.LLM_TIMEOUT_SECONDS,
        )
    return MockLLMAdapter()
