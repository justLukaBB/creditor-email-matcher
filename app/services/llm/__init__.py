"""
Provider-agnostic LLM adapter layer.

Part of the Vertex AI migration (docs/EMAIL-MATCHER-VERTEX-MIGRATION-PLAN.md).
Call-sites obtain a client via ``get_llm_client(use_case)`` and call
``.generate(...)``; the concrete provider (Claude or Vertex/Gemini) is chosen
by the ``LLM_PROVIDER`` env switch, which makes rollback a one-flag change.
"""

from app.services.llm.base import LLMClient, LLMResponse, MediaInput
from app.services.llm.factory import get_llm_client

__all__ = ["LLMClient", "LLMResponse", "MediaInput", "get_llm_client"]
