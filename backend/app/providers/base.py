from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    input_tokens: int
    output_tokens: int
    finish_reason: str = "stop"


class ModelProvider(Protocol):
    async def generate(self, *, system: str, prompt: str, model: str) -> ProviderResponse: ...

