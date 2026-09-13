import logging
import pytest
from unittest.mock import AsyncMock, patch

from app.config import settings
from app.llm import (
    AllProvidersFailedError,
    BaseLLMProvider,
    LLMRouter,
)


class MockSuccessProvider(BaseLLMProvider):
    def __init__(self, name: str, response_text: str):
        super().__init__(name)
        self.response_text = response_text
        self.call_count = 0

    async def generate(self, prompt: str, system_prompt=None, response_format=None) -> str:
        self.call_count += 1
        return self.response_text


class MockFailingProvider(BaseLLMProvider):
    def __init__(self, name: str, error_to_raise: Exception):
        super().__init__(name)
        self.error_to_raise = error_to_raise
        self.call_count = 0

    async def generate(self, prompt: str, system_prompt=None, response_format=None) -> str:
        self.call_count += 1
        raise self.error_to_raise


@pytest.mark.asyncio
async def test_llm_router_fallback_on_rate_limit(caplog):
    """
    Test: Provider A fails with a rate limit error (429), Provider B succeeds.
    Confirms:
    1. Returns Provider B's response.
    2. Logs the fallback event.
    3. Provider B was actually invoked after Provider A failed.
    """
    router = LLMRouter()
    provider_a = MockFailingProvider("anthropic", RuntimeError("429 RateLimitError: Token quota exceeded"))
    provider_b = MockSuccessProvider("openai", "SELECT * FROM customers;")

    router.register_provider("anthropic", provider_a)
    router.register_provider("openai", provider_b)

    with patch.object(settings, "LLM_PROVIDER_ORDER", ["anthropic", "openai"]), \
         patch.object(settings, "ANTHROPIC_API_KEY", "test-anthropic-key"), \
         patch.object(settings, "OPENAI_API_KEY", "test-openai-key"), \
         caplog.at_level(logging.WARNING):

        result = await router.generate_with_fallback("List customers")

        assert result == "SELECT * FROM customers;"
        assert provider_a.call_count == 1
        assert provider_b.call_count == 1

        # Check fallback logging
        assert any("LLM Fallback Triggered" in record.message for record in caplog.records)
        assert any("anthropic" in record.message and "429" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_llm_router_all_providers_fail_raises_combined_error():
    """
    Test: All eligible providers in the chain fail.
    Confirms:
    1. Raises AllProvidersFailedError.
    2. Error message contains details from each failing provider, not just the last one.
    """
    router = LLMRouter()
    provider_a = MockFailingProvider("anthropic", RuntimeError("500 Internal Server Error"))
    provider_b = MockFailingProvider("openai", TimeoutError("Connection timed out"))
    provider_c = MockFailingProvider("groq", RuntimeError("429 Quota Exceeded"))

    router.register_provider("anthropic", provider_a)
    router.register_provider("openai", provider_b)
    router.register_provider("groq", provider_c)

    with patch.object(settings, "LLM_PROVIDER_ORDER", ["anthropic", "openai", "groq"]), \
         patch.object(settings, "ANTHROPIC_API_KEY", "key-a"), \
         patch.object(settings, "OPENAI_API_KEY", "key-b"), \
         patch.object(settings, "GROQ_API_KEY", "key-c"):

        with pytest.raises(AllProvidersFailedError) as exc_info:
            await router.generate_with_fallback("Calculate total revenue")

        err_msg = str(exc_info.value)
        assert "anthropic" in err_msg and "500 Internal Server Error" in err_msg
        assert "openai" in err_msg and "Connection timed out" in err_msg
        assert "groq" in err_msg and "429 Quota Exceeded" in err_msg


@pytest.mark.asyncio
async def test_llm_router_skips_providers_without_api_keys():
    """
    Test: Only providers with configured API keys are eligible in the chain.
    """
    router = LLMRouter()
    provider_a = MockSuccessProvider("anthropic", "Anthropic Response")
    provider_b = MockSuccessProvider("openai", "OpenAI Response")

    router.register_provider("anthropic", provider_a)
    router.register_provider("openai", provider_b)

    # Anthropic key is NOT configured, OpenAI key IS configured
    with patch.object(settings, "LLM_PROVIDER_ORDER", ["anthropic", "openai"]), \
         patch.object(settings, "ANTHROPIC_API_KEY", None), \
         patch.object(settings, "OPENAI_API_KEY", "test-key"):

        eligible = settings.get_eligible_providers()
        assert eligible == ["openai"]

        result = await router.generate_with_fallback("Hello")
        assert result == "OpenAI Response"
        assert provider_a.call_count == 0
        assert provider_b.call_count == 1


@pytest.mark.asyncio
async def test_llm_router_empty_response_fallback_and_warning(caplog):
    """
    Test: Provider A returns an empty or whitespace-only response, Provider B succeeds.
    Confirms:
    1. Returns Provider B's response.
    2. Logs a warning stating Provider A returned an empty response.
    3. Provider B is invoked.
    """
    router = LLMRouter()
    provider_a = MockSuccessProvider("anthropic", "   ")
    provider_b = MockSuccessProvider("openai", "SELECT COUNT(*) FROM orders;")

    router.register_provider("anthropic", provider_a)
    router.register_provider("openai", provider_b)

    with patch.object(settings, "LLM_PROVIDER_ORDER", ["anthropic", "openai"]), \
         patch.object(settings, "ANTHROPIC_API_KEY", "test-anthropic-key"), \
         patch.object(settings, "OPENAI_API_KEY", "test-openai-key"), \
         caplog.at_level(logging.WARNING):

        result = await router.generate_with_fallback("Count orders")

        assert result == "SELECT COUNT(*) FROM orders;"
        assert provider_a.call_count == 1
        assert provider_b.call_count == 1

        # Check warning log for empty response
        assert any(
            "anthropic" in record.message and "returned an empty response" in record.message
            for record in caplog.records
        )

