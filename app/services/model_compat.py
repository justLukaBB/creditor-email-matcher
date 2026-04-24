"""Safety net that rewrites retired Anthropic model IDs to current replacements.

The DB-backed prompt templates can point at model IDs that Anthropic has since
retired (e.g. a fresh install seeded long ago, or an admin-set value via the
prompt manager UI). Those calls return a 404 from the API, the extractor
returns None, and the downstream amount_update_guard skips the update — so the
email is routed correctly but the Forderung stays empty.

Rather than require a DB migration every time Anthropic deprecates a model,
all model_name reads from the DB flow through ``resolve_model_name`` which
rewrites retired IDs to their drop-in replacement. Mapping follows the
drop-in-replacement table in Anthropic's model migration guide.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Retired or deprecated -> current drop-in replacement.
# Source: platform.claude.com model-migration guide.
_RETIRED_MODEL_MAP: dict[str, str] = {
    # Haiku retirements
    "claude-3-haiku-20240307": "claude-haiku-4-5-20251001",
    "claude-3-5-haiku-20241022": "claude-haiku-4-5-20251001",
    "claude-haiku-4-20250514": "claude-haiku-4-5-20251001",  # never shipped — typo in old seeds
    # Sonnet retirements
    "claude-3-sonnet-20240229": "claude-sonnet-4-5-20250929",
    "claude-3-5-sonnet-20240620": "claude-sonnet-4-5-20250929",
    "claude-3-5-sonnet-20241022": "claude-sonnet-4-5-20250929",
    "claude-3-7-sonnet-20250219": "claude-sonnet-4-5-20250929",
    # Opus retirements
    "claude-3-opus-20240229": "claude-opus-4-5-20251101",
    # Legacy bare strings that break without a date suffix
    "claude-sonnet": "claude-sonnet-4-5-20250929",
    "claude-haiku": "claude-haiku-4-5-20251001",
    "claude-opus": "claude-opus-4-5-20251101",
}

_DEFAULT_FALLBACK = "claude-haiku-4-5-20251001"


def resolve_model_name(model_name: Optional[str]) -> str:
    """Return a currently-valid Anthropic model ID.

    Rewrites retired IDs to their drop-in replacement and logs once per rewrite
    so stale DB rows are observable. Falls back to Haiku 4.5 if ``model_name``
    is empty — callers should usually pass their own default, but this keeps
    the surface safe.
    """
    if not model_name:
        return _DEFAULT_FALLBACK

    replacement = _RETIRED_MODEL_MAP.get(model_name)
    if replacement is None:
        return model_name

    logger.warning(
        "model_id_rewrite",
        extra={
            "from": model_name,
            "to": replacement,
            "reason": "retired_or_deprecated_model_id",
        },
    )
    return replacement
