"""
Provider-agnostic LLM client interface.

The adapter is a deliberately thin transport: it normalizes a single
text-generation call across Anthropic (Claude) and Google Vertex AI (Gemini)
and returns the generated text plus token usage. Everything that is
provider-independent — JSON parsing, circuit breakers, metrics recording,
confidence handling — stays at the call-site so behavior (and the eval
baseline) is preserved exactly during the migration.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Type

from pydantic import BaseModel


@dataclass
class MediaInput:
    """A binary attachment (image or PDF) for a vision-capable model.

    ``data`` is always RAW bytes. The Claude client base64-encodes internally;
    the Vertex client passes the bytes straight to ``types.Part.from_bytes``.
    """

    data: bytes
    mime_type: str  # e.g. "image/png", "image/jpeg", "application/pdf"


@dataclass
class LLMResponse:
    """Normalized result of one generation call, independent of provider."""

    text: str
    input_tokens: int
    output_tokens: int
    model: str


class LLMClient(ABC):
    """Single-shot text/vision generation for one configured model."""

    def __init__(self, model: str):
        self.model = model

    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: Optional[float] = None,
        media: Optional[List[MediaInput]] = None,
        response_schema: Optional[Type[BaseModel]] = None,
        json_output: bool = False,
    ) -> LLMResponse:
        """Run one generation.

        Args:
            prompt: User prompt / instruction text.
            system: Optional system instruction (kept separate from the prompt).
            max_tokens: Output token cap.
            temperature: Sampling temperature. ``None`` leaves the provider
                default untouched (some vision call-sites rely on this).
            media: Optional image/PDF attachments for vision calls.
            response_schema: Optional Pydantic model. On Vertex this enables
                native structured JSON output; on Claude it is a no-op (Claude
                relies on prompt instructions, as it does today).
            json_output: Request JSON output without a strict schema. Vertex
                sets ``response_mime_type``; Claude treats it as a no-op.
        """
        raise NotImplementedError
