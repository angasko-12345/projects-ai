"""LLM providers. No silent fallback: unknown provider or HTTP error raises."""
import os
import re
from abc import ABC, abstractmethod

import requests

from .state import PipelineError

_SENTENCE_TEMPLATES = (
    "The story of {topic} begins with a decision that few people at the time understood.",
    "Pressure built slowly, and the people closest to the events disagreed about what it meant.",
    "A single turn changed the balance, and the consequences reached far beyond the capital.",
    "Records from the period are incomplete, so historians still argue about the details.",
    "The cost fell hardest on ordinary people, whose names rarely appear in the sources.",
    "What followed was not inevitable, and the alternatives matter for understanding the outcome.",
    "Later writers reshaped the account, sometimes to serve the politics of their own era.",
    "The legacy survives in institutions, borders, and stories that are still told today.",
    "Trade, weather, and distance shaped choices as much as the ambitions of leaders.",
    "By the end, the world that emerged looked different from the one that had existed before.",
)


class LLMProvider(ABC):
    @abstractmethod
    def complete(self, system, user):
        raise NotImplementedError


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, base_url, api_key, model, timeout=120,
                 temperature=None, max_tokens=None):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens

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
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.max_tokens:
            payload["max_tokens"] = self.max_tokens
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
    """Deterministic canned script that honors the prompt's word budget.

    The fake is not a writer, but it must respect the same length contract a
    real provider is held to. Otherwise tests pass on a script no real run
    would accept.
    """

    def complete(self, system, user):
        topic = "the topic"
        m = re.search(r"Topic:\s*(.+)", user)
        if m:
            topic = m.group(1).strip()
        ids = sorted(set(re.findall(r"\bS\d+\b", user)), key=lambda s: int(s[1:]))
        cites = ids[:2] if len(ids) >= 2 else ["S1", "S2"]
        cite = ", ".join(cites)
        target_m = re.search(r"Target length:\s*(\d+)\s*words", user)
        target = int(target_m.group(1)) if target_m else 120
        sections_m = re.search(r"Template sections:\s*(.+)", user)
        sections = [s.strip() for s in (sections_m.group(1) if sections_m else "").split(",")
                    if s.strip()] or ["story"]
        target = max(20, target)
        per_section = {s: [] for s in sections}
        per_words = {s: 0 for s in sections}
        total = 0
        template_index = 0
        while total < int(target * 0.95) and template_index < 400:
            section = min(sections, key=lambda s: (per_words[s], sections.index(s)))
            sentence = _SENTENCE_TEMPLATES[template_index % len(_SENTENCE_TEMPLATES)].format(
                topic=topic)
            per_section[section].append(sentence)
            words = len(sentence.split())
            per_words[section] += words
            total += words
            template_index += 1
        lines = [f"Title: The Story of {topic}"]
        claim_no = 0
        for section in sections:
            lines.append(f"## {section}")
            for sentence in per_section[section]:
                claim_no += 1
                lines.append(f"[C{claim_no}] {sentence} ({cite})")
        lines += [
            "Needs check:",
            "- Verify dates and names against sources",
            "- Confirm quoted passages",
        ]
        return "\n".join(lines) + "\n"


def make_provider(cfg, env=None):
    env = os.environ if env is None else env
    llm = (cfg or {}).get("llm") or {}
    provider = (llm.get("provider") or "fake").lower()
    temperature = llm.get("temperature")
    max_tokens = llm.get("max_tokens")

    def key_from(names):
        for name in names:
            if env.get(name):
                return env[name]
        return ""

    def build(base_url, key, model=None):
        return OpenAICompatibleProvider(
            base_url, key, model if model is not None else (llm.get("model") or ""),
            temperature=temperature, max_tokens=max_tokens)

    if provider == "fake":
        return FakeProvider()
    if provider == "google":
        return build("https://generativelanguage.googleapis.com/v1beta/openai/",
                     key_from(["GOOGLE_API_KEY", "HISTYT_LLM_API_KEY"]))
    if provider == "openrouter":
        return build("https://openrouter.ai/api/v1", key_from(["OPENROUTER_API_KEY"]))
    if provider == "local":
        return build(llm.get("base_url") or "http://127.0.0.1:8080/v1",
                     key_from(["HISTYT_LLM_API_KEY"]))
    if provider == "openai-compatible":
        return build(llm.get("base_url") or "",
                     key_from([llm.get("api_key_env") or "HISTYT_LLM_API_KEY"]))
    raise PipelineError(f"unknown LLM provider: {provider!r}")
