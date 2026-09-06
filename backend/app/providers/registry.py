from __future__ import annotations

import json
import os
from typing import Any

from app.config import settings
from app.providers.base import ModelProvider, ProviderResponse


class MockProvider:
    async def generate(self, *, system: str, prompt: str, model: str) -> ProviderResponse:
        try:
            packet = json.loads(prompt)
        except json.JSONDecodeError:
            packet = {"task": prompt, "observations": []}
        expected = packet.get("expected")
        if packet.get("mode") == "judge":
            reference = packet.get("reference_evaluation", {})
            judged = {
                "task_completion": reference.get("task_completion", 1.0),
                "answer_correctness": reference.get("answer_correctness", 1.0),
                "tool_selection_accuracy": reference.get("tool_selection_accuracy", 1.0),
                "groundedness": 1 - reference.get("hallucination_rate", 0.0),
                "failure_classification": reference.get("failure_classification"),
                "rationale": "Offline judge calibrated to the deterministic reference evaluator.",
            }
            text = json.dumps(judged, sort_keys=True)
        elif expected is not None:
            text = json.dumps(expected, sort_keys=True)
        else:
            observations = packet.get("observations", [])
            summary = "; ".join(
                f"{item.get('tool')}: {json.dumps(item.get('result'), sort_keys=True)}"
                for item in observations
            )
            text = (
                f"Completed: {packet.get('task', 'task')}. "
                f"Evidence: {summary or 'No tool evidence was required.'}"
            )
        return ProviderResponse(
            text=text,
            input_tokens=max(1, (len(system) + len(prompt)) // 4),
            output_tokens=max(1, len(text) // 4),
        )


class OpenAIProvider:
    async def generate(self, *, system: str, prompt: str, model: str) -> ProviderResponse:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        response = await client.responses.create(
            model=model,
            instructions=system,
            input=prompt,
        )
        usage = response.usage
        return ProviderResponse(
            text=response.output_text,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            finish_reason="stop",
        )


class AnthropicProvider:
    async def generate(self, *, system: str, prompt: str, model: str) -> ProviderResponse:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        response = await client.messages.create(
            model=model,
            system=system,
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text
            for block in response.content
            if getattr(block, "type", "") == "text"
        )
        return ProviderResponse(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            finish_reason=response.stop_reason or "stop",
        )


class GeminiProvider:
    async def generate(self, *, system: str, prompt: str, model: str) -> ProviderResponse:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.gemini_api_key)
        response = await client.aio.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system, temperature=0),
        )
        usage = response.usage_metadata
        return ProviderResponse(
            text=response.text or "",
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
            finish_reason="stop",
        )


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ModelProvider] = {
            "mock": MockProvider(),
            "openai": OpenAIProvider(),
            "anthropic": AnthropicProvider(),
            "gemini": GeminiProvider(),
        }

    def get(self, name: str) -> ModelProvider:
        if name not in self._providers:
            raise KeyError(f"Unknown provider: {name}")
        required_key = {
            "openai": settings.openai_api_key,
            "anthropic": settings.anthropic_api_key,
            "gemini": settings.gemini_api_key,
        }.get(name, "mock")
        if not required_key:
            raise RuntimeError(f"{name.upper()}_API_KEY is not configured")
        return self._providers[name]

    @staticmethod
    def default_model(name: str) -> str:
        return {
            "mock": "baseline-deterministic-v1",
            "openai": settings.openai_model,
            "anthropic": settings.anthropic_model,
            "gemini": settings.gemini_model,
        }[name]

    @staticmethod
    def cost(name: str, input_tokens: int, output_tokens: int) -> float:
        prices: dict[str, tuple[float, float]] = {
            "mock": (0.0, 0.0),
            "openai": (
                float(os.getenv("OPENAI_INPUT_PRICE", "2.00")),
                float(os.getenv("OPENAI_OUTPUT_PRICE", "12.00")),
            ),
            "anthropic": (
                float(os.getenv("ANTHROPIC_INPUT_PRICE", "3.00")),
                float(os.getenv("ANTHROPIC_OUTPUT_PRICE", "15.00")),
            ),
            "gemini": (
                float(os.getenv("GEMINI_INPUT_PRICE", "0.75")),
                float(os.getenv("GEMINI_OUTPUT_PRICE", "3.75")),
            ),
        }
        input_price, output_price = prices[name]
        return round((input_tokens * input_price + output_tokens * output_price) / 1_000_000, 6)

    def availability(self) -> list[dict[str, Any]]:
        return [
            {
                "provider": name,
                "model": self.default_model(name),
                "configured": name == "mock" or bool(
                    {
                        "openai": settings.openai_api_key,
                        "anthropic": settings.anthropic_api_key,
                        "gemini": settings.gemini_api_key,
                    }.get(name)
                ),
            }
            for name in self._providers
        ]


provider_registry = ProviderRegistry()
