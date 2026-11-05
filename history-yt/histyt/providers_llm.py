"""LLM providers. No silent fallback: unknown provider or HTTP error raises."""
import os
import re
from abc import ABC, abstractmethod

import requests

from .state import PipelineError


class LLMProvider(ABC):
    @abstractmethod
    def complete(self, system, user):
        raise NotImplementedError


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, base_url, api_key, model, timeout=60):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""
        self.timeout = timeout

    def complete(self, system, user):
        if not self.base_url:
            raise PipelineError("LLM base_url is empty")
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise PipelineError(f"LLM request failed: {exc}") from exc
        if resp.status_code >= 400:
            # Redact any API key from echoed error text.
            text = resp.text[:500].replace(self.api_key, "[redacted]") if self.api_key else resp.text[:500]
            raise PipelineError(f"LLM HTTP {resp.status_code}: {text}")
        try:
            return resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise PipelineError(f"unexpected LLM response shape: {exc}") from exc


class FakeProvider(LLMProvider):
    """Deterministic canned script; encodes source citations per claim."""

    def complete(self, system, user):
        topic = "the topic"
        m = re.search(r"Topic:\s*(.+)", user)
        if m:
            topic = m.group(1).strip()
        ids = sorted(set(re.findall(r"\bS\d+\b", user)), key=lambda s: int(s[1:]))
        cites = ids[:2] if len(ids) >= 2 else ["S1", "S2"]
        cite = ", ".join(cites)
        text = (
            f"Title: The Story of {topic}\n"
            f"[C1] The origins of {topic} trace back to early records and events. ({cite})\n"
            f"[C2] A turning point reshaped {topic} and affected everyone involved. ({cite})\n"
            f"[C3] The legacy of {topic} still shapes how we understand the period. ({cite})\n"
            "Needs check:\n"
            "- Verify dates and names against sources\n"
            "- Confirm quoted passages\n"
        )
        return text


def make_provider(cfg, env=None):
    env = os.environ if env is None else env
    llm = (cfg or {}).get("llm") or {}
    provider = (llm.get("provider") or "fake").lower()

    def key_from(names):
        for name in names:
            if env.get(name):
                return env[name]
        return ""

    if provider == "fake":
        return FakeProvider()
    if provider == "google":
        return OpenAICompatibleProvider(
            "https://generativelanguage.googleapis.com/v1beta/openai/",
            key_from(["GOOGLE_API_KEY", "HISTYT_LLM_API_KEY"]),
            llm.get("model") or "",
        )
    if provider == "openrouter":
        return OpenAICompatibleProvider(
            "https://openrouter.ai/api/v1",
            key_from(["OPENROUTER_API_KEY"]),
            llm.get("model") or "",
        )
    if provider == "local":
        return OpenAICompatibleProvider(
            llm.get("base_url") or "http://127.0.0.1:8080/v1",
            key_from(["HISTYT_LLM_API_KEY"]),
            llm.get("model") or "",
        )
    if provider == "openai-compatible":
        return OpenAICompatibleProvider(
            llm.get("base_url") or "",
            key_from([llm.get("api_key_env") or "HISTYT_LLM_API_KEY"]),
            llm.get("model") or "",
        )
    raise PipelineError(f"unknown LLM provider: {provider!r}")
