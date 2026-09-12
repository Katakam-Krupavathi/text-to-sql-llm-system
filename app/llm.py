import logging
from typing import Optional
from app.config import settings

logger = logging.getLogger(__name__)


class LLMClient:
    """Thin wrapper around LLM providers (OpenAI / Anthropic)."""

    def __init__(self):
        self.provider = settings.LLM_PROVIDER
        self.model = settings.LLM_MODEL
        self.temperature = settings.LLM_TEMPERATURE
        self.max_retries = settings.MAX_RETRIES

        self._openai_client = None
        self._anthropic_client = None

    def _get_openai_client(self):
        if self._openai_client is None:
            from openai import AsyncOpenAI

            self._openai_client = AsyncOpenAI(
                api_key=settings.OPENAI_API_KEY,
                max_retries=self.max_retries,
            )
        return self._openai_client

    def _get_anthropic_client(self):
        if self._anthropic_client is None:
            from anthropic import AsyncAnthropic

            self._anthropic_client = AsyncAnthropic(
                api_key=settings.ANTHROPIC_API_KEY,
                max_retries=self.max_retries,
            )
        return self._anthropic_client

    async def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Generates a text completion given a prompt and optional system prompt."""
        if self.provider == "openai":
            client = self._get_openai_client()
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            response = await client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
            )
            return response.choices[0].message.content or ""

        elif self.provider == "anthropic":
            client = self._get_anthropic_client()
            kwargs = {
                "model": self.model,
                "max_tokens": 4096,
                "temperature": self.temperature,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system_prompt:
                kwargs["system"] = system_prompt

            response = await client.messages.create(**kwargs)
            return response.content[0].text if response.content else ""

        else:
            raise ValueError(f"Unsupported LLM provider: {self.provider}")


# Global client instance
llm_client = LLMClient()
