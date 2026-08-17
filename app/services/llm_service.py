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
Analyze the following customer support ticket and return a JSON object with:
- "summary": A concise 1-2 sentence summary of the issue.
- "category": One of "billing", "technical", "account", or "other".
- "priority": One of "low", "medium", or "high".

Ticket Title: {title}
Ticket Description: {description}

Return ONLY valid JSON matching this schema:
{{"summary": "string", "category": "billing|technical|account|other", "priority": "low|medium|high"}}
"""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(f"{self.base_url}/api/generate", json=payload)
            response.raise_for_status()
            data = response.json()
            raw_response = data.get("response", "{}")

            parsed = json.loads(raw_response)
            return TriageResult(**parsed)


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
