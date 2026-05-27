"""
Anthropic (Claude) transport.

Legacy/rollback path. This wraps the exact ``messages.create`` call shape the
call-sites used before the adapter, so ``LLM_PROVIDER=claude`` reproduces
today's behavior byte-for-byte (and serves as the eval baseline).
"""

from __future__ import annotations

import base64
from typing import List, Optional, Type

from pydantic import BaseModel

from app.config import settings
from app.services.llm.base import LLMClient, LLMResponse, MediaInput

_PDF_MIME = "application/pdf"


class ClaudeClient(LLMClient):
    def __init__(self, model: str):
        super().__init__(model)
        # Lazy import keeps the SDK optional at import time.
        from anthropic import Anthropic

        self._client = Anthropic(api_key=settings.anthropic_api_key)

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
        # response_schema / json_output are intentionally ignored for Claude:
        # structured output is driven by the prompt, matching current behavior.
        if media:
            content: list = []
            for m in media:
                # PDFs go through Claude's native "document" block; images via "image".
                block_type = "document" if m.mime_type == _PDF_MIME else "image"
                content.append(
                    {
                        "type": block_type,
                        "source": {
                            "type": "base64",
                            "media_type": m.mime_type,
                            "data": base64.b64encode(m.data).decode("ascii"),
                        },
                    }
                )
            content.append({"type": "text", "text": prompt})
            message_content: object = content
        else:
            message_content = prompt

        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": message_content}],
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if system:
            kwargs["system"] = system

        message = self._client.messages.create(**kwargs)
        return LLMResponse(
            text=message.content[0].text,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            model=self.model,
        )
