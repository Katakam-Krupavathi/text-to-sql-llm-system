from abc import ABC, abstractmethod
import logging
from typing import Dict, List, Optional
import httpx
from app.config import settings

logger = logging.getLogger(__name__)


class AllProvidersFailedError(Exception):
    """Raised when every configured LLM provider in the fallback chain has failed."""
    pass


class BaseLLMProvider(ABC):
    """Abstract Base Class for LLM providers."""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        response_format: Optional[str] = None,
    ) -> str:
        """Generates completion text from the provider."""
        pass


class OpenAIProvider(BaseLLMProvider):
    def __init__(self):
        super().__init__("openai")
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(
                api_key=settings.OPENAI_API_KEY,
                max_retries=settings.MAX_RETRIES,
            )
        return self._client

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        response_format: Optional[str] = None,
    ) -> str:
        client = self._get_client()
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        kwargs = {
            "model": settings.OPENAI_MODEL or settings.LLM_MODEL,
            "messages": messages,
            "temperature": settings.LLM_TEMPERATURE,
        }
        if response_format == "json":
            kwargs["response_format"] = {"type": "json_object"}

        response = await client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""


class AnthropicProvider(BaseLLMProvider):
    def __init__(self):
        super().__init__("anthropic")
        self._client = None

    def _get_client(self):
        if self._client is None:
            from anthropic import AsyncAnthropic
            self._client = AsyncAnthropic(
                api_key=settings.ANTHROPIC_API_KEY,
                max_retries=settings.MAX_RETRIES,
            )
        return self._client

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        response_format: Optional[str] = None,
    ) -> str:
        client = self._get_client()
        kwargs = {
            "model": settings.ANTHROPIC_MODEL,
            "max_tokens": 4096,
            "temperature": settings.LLM_TEMPERATURE,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        response = await client.messages.create(**kwargs)
        return response.content[0].text if response.content else ""


class GeminiProvider(BaseLLMProvider):
    def __init__(self):
        super().__init__("gemini")

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        response_format: Optional[str] = None,
    ) -> str:
        if not settings.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is not configured.")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent?key={settings.GEMINI_API_KEY}"
        
        contents = []
        if system_prompt:
            contents.append({"role": "user", "parts": [{"text": f"SYSTEM INSTRUCTIONS:\n{system_prompt}\n\nUSER REQUEST:\n{prompt}"}]})
        else:
            contents.append({"role": "user", "parts": [{"text": prompt}]})

        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": settings.LLM_TEMPERATURE,
            }
        }
        if response_format == "json":
            payload["generationConfig"]["responseMimeType"] = "application/json"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"Gemini API error ({resp.status_code}): {resp.text}")
            data = resp.json()
            candidates = data.get("candidates", [])
            if candidates and "content" in candidates[0]:
                parts = candidates[0]["content"].get("parts", [])
                if parts:
                    return parts[0].get("text", "")
            return ""


class GroqProvider(BaseLLMProvider):
    def __init__(self):
        super().__init__("groq")

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        response_format: Optional[str] = None,
    ) -> str:
        if not settings.GROQ_API_KEY:
            raise ValueError("GROQ_API_KEY is not configured.")

        url = "https://api.groq.com/openai/v1/chat/completions"
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": settings.GROQ_MODEL,
            "messages": messages,
            "temperature": settings.LLM_TEMPERATURE,
        }
        if response_format == "json":
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {settings.GROQ_API_KEY}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"Groq API error ({resp.status_code}): {resp.text}")
            data = resp.json()
            return data["choices"][0]["message"]["content"] or ""


class LLMRouter:
    """Multi-provider LLM Router that handles priority ordering and automatic fallback."""

    def __init__(self):
        self._providers: Dict[str, BaseLLMProvider] = {
            "openai": OpenAIProvider(),
            "anthropic": AnthropicProvider(),
            "gemini": GeminiProvider(),
            "groq": GroqProvider(),
        }

    def register_provider(self, name: str, provider: BaseLLMProvider):
        self._providers[name.lower()] = provider

    def get_provider(self, name: str) -> Optional[BaseLLMProvider]:
        return self._providers.get(name.lower())

    async def generate_with_fallback(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        response_format: Optional[str] = None,
    ) -> str:
        """
        Attempts to generate text using eligible providers in priority order.
        If a provider encounters an error (rate limit, quota, timeout, 5xx), it logs the event
        and falls back to the next provider in the chain.
        """
        eligible_providers = settings.get_eligible_providers()
        errors = []

        for provider_name in eligible_providers:
            provider = self.get_provider(provider_name)
            if not provider:
                continue

            try:
                logger.info(f"Attempting LLM generation with provider: '{provider_name}'")
                response = await provider.generate(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    response_format=response_format,
                )
                if response:
                    return response
            except Exception as e:
                err_str = f"Provider '{provider_name}' failed: {type(e).__name__} - {str(e)}"
                logger.warning(f"LLM Fallback Triggered: {err_str}. Trying next provider in order...")
                errors.append(err_str)

        # If all configured providers have failed
        combined_error_msg = (
            f"All configured LLM providers ({eligible_providers}) in the fallback chain failed:\n"
            + "\n".join(f"  - {err}" for err in errors)
        )
        logger.error(combined_error_msg)
        raise AllProvidersFailedError(combined_error_msg)

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        response_format: Optional[str] = None,
    ) -> str:
        """Alias for generate_with_fallback."""
        return await self.generate_with_fallback(
            prompt=prompt,
            system_prompt=system_prompt,
            response_format=response_format,
        )


# Global instances
llm_router = LLMRouter()
llm_client = llm_router
