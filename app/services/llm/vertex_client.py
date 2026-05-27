"""
Google Vertex AI (Gemini) transport — the EU/Frankfurt target provider.

Mirrors the proven client setup from creditor-process-fastapi:
``genai.Client(vertexai=True, project, location)`` with ADC credentials
(GOOGLE_APPLICATION_CREDENTIALS). Requests go through the ported rate
limiter for RPM control + 429 backoff.
"""

from __future__ import annotations

from typing import List, Optional, Type

from pydantic import BaseModel

from app.config import settings
from app.services.llm.base import LLMClient, LLMResponse, MediaInput
from app.services.llm.gemini_rate_limiter import generate_content_with_retry_sync

# A single genai.Client is reused across calls (auth + channel setup is costly).
_genai_client = None


def _get_genai_client():
    global _genai_client
    if _genai_client is None:
        from google import genai

        project = settings.google_cloud_project
        if not project:
            raise RuntimeError(
                "GOOGLE_CLOUD_PROJECT is required when LLM_PROVIDER=vertex"
            )
        _genai_client = genai.Client(
            vertexai=True,
            project=project,
            location=settings.vertex_ai_region,
        )
    return _genai_client


class VertexClient(LLMClient):
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
        from google.genai import types

        client = _get_genai_client()

        if media:
            contents: list = [
                types.Part.from_bytes(data=m.data, mime_type=m.mime_type) for m in media
            ]
            contents.append(prompt)
        else:
            contents = [prompt]

        config_kwargs: dict = {"max_output_tokens": max_tokens}
        if temperature is not None:
            config_kwargs["temperature"] = temperature
        if system:
            config_kwargs["system_instruction"] = system
        if response_schema is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = response_schema
        elif json_output:
            config_kwargs["response_mime_type"] = "application/json"

        # gemini-2.5-pro emits mandatory internal "thinking" tokens that are
        # billed against max_output_tokens and CANNOT be disabled. Call sites
        # size max_tokens for the answer only (these values were tuned for
        # Claude, which has no thinking). Without headroom the thinking consumes
        # the whole budget and the structured answer truncates
        # (finish_reason=MAX_TOKENS) -> JSON parse fails. Bound the thinking
        # budget and add it on top so the answer always keeps its full
        # max_tokens. flash / flash-lite are left untouched (thinking off or
        # negligible there).
        if "pro" in self.model:
            thinking_budget = 2048  # measured pro thinking ~500-900; generous ceiling
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=thinking_budget
            )
            config_kwargs["max_output_tokens"] = max_tokens + thinking_budget

        config = types.GenerateContentConfig(**config_kwargs)

        response = generate_content_with_retry_sync(
            client=client,
            model_name=self.model,
            content=contents,
            config=config,
            operation_name=f"vertex:{self.model}",
        )

        usage = getattr(response, "usage_metadata", None)
        return LLMResponse(
            text=response.text or "",
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
            model=self.model,
        )
