"""
LLM client factory — honors the LLM_PROVIDER switch for clean rollback.

Call-sites ask for a client by use-case (``intent``, ``entity``,
``settlement``, ``pdf``, ``image``). On ``vertex`` the model comes from the
GEMINI_MODEL_* env per use-case; on ``claude`` the call-site may pass the model
it already resolved (DB prompt template etc.) so the legacy path — and the eval
baseline — stays byte-for-byte identical.
"""

from __future__ import annotations

from typing import Optional

from app.config import settings
from app.services.llm.base import LLMClient

# Claude model per use-case, used only when the call-site supplies no explicit
# model on the claude path. Mirrors the hardcoded model IDs that were inline
# at each call-site before the adapter.
_CLAUDE_DEFAULTS = {
    "intent": "claude-haiku-4-5-20251001",
    "entity": settings.anthropic_model,
    "settlement": "claude-haiku-4-5-20251001",
    "pdf": "claude-sonnet-4-5-20250929",
    "image": "claude-sonnet-4-5-20250929",
}


def _vertex_model(use_case: str) -> str:
    mapping = {
        "intent": settings.gemini_model_intent,
        "entity": settings.gemini_model_entity,
        "settlement": settings.gemini_model_settlement,
        "pdf": settings.gemini_model_pdf,
        "image": settings.gemini_model_image,
    }
    try:
        return mapping[use_case]
    except KeyError:
        raise ValueError(f"Unknown LLM use_case: {use_case!r}")


def get_llm_client(use_case: str, *, claude_model: Optional[str] = None) -> LLMClient:
    """Return an LLM client for one call-site, per the LLM_PROVIDER switch.

    Args:
        use_case: intent | entity | settlement | pdf | image.
        claude_model: model the call-site already resolved; used only on the
            claude path (ignored for vertex, which uses GEMINI_MODEL_* env).
    """
    provider = (settings.llm_provider or "claude").lower()

    if provider == "vertex":
        from app.services.llm.vertex_client import VertexClient

        return VertexClient(model=_vertex_model(use_case))

    if provider == "claude":
        from app.services.llm.claude_client import ClaudeClient

        model = claude_model or _CLAUDE_DEFAULTS.get(use_case, settings.anthropic_model)
        return ClaudeClient(model=model)

    raise ValueError(
        f"Unknown LLM_PROVIDER: {provider!r} (expected 'claude' or 'vertex')"
    )
