---
phase: 08-database-backed-prompt-management
plan: 02
subsystem: services
tags: [jinja2, prompt-rendering, version-management, metrics-tracking, cost-calculation]

# Dependency graph
requires:
  - phase: 08-01
    provides: Database models for prompt templates and performance metrics
provides:
  - PromptRenderer service for Jinja2 template rendering with variable validation
  - PromptVersionManager service for activation/rollback of ANY historical version
  - PromptMetricsService for extraction-level performance tracking with cost calculation
  - Convenience function get_active_prompt for fast active prompt lookups
affects: [09-prompt-integration, extractors, cost-optimization, monitoring]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Jinja2 template rendering with structured logging"
    - "Explicit activation flow with ANY-version rollback capability"
    - "Claude API cost calculation per 1K tokens"
    - "Aggregated stats over recent days window"

key-files:
  created:
    - app/services/prompt_renderer.py
    - app/services/prompt_manager.py
    - app/services/prompt_metrics_service.py
  modified: []

key-decisions:
  - "Jinja2 Environment config: autoescape=False for LLM prompts, trim_blocks and lstrip_blocks for clean formatting"
  - "Template syntax validation without rendering via validate_template() to prevent runtime errors"
  - "Rollback implementation as activation of historical version (ANY version, not just previous)"
  - "API cost calculation with Decimal precision (6 decimal places) for accurate tracking"
  - "Default stats window of 7 days for get_version_stats()"

patterns-established:
  - "PromptRenderer: Jinja2 rendering with try/except for TemplateSyntaxError and UndefinedError"
  - "PromptVersionManager: Atomic activation (deactivate current + activate target in single transaction)"
  - "Structured logging: template_name, variables, rendered_length for all render operations"
  - "Cost calculation: (tokens/1000) * price_per_1k with fallback to default pricing"

# Metrics
duration: 3min
completed: 2026-02-06
---

# Phase 8 Plan 2: Prompt Management Services Summary

**Jinja2 template rendering with validation, version activation/rollback with ANY-version capability, and extraction-level metrics tracking with Claude API cost calculation**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-06T15:32:08Z
- **Completed:** 2026-02-06T16:35:01Z
- **Tasks:** 3
- **Files modified:** 3

## Accomplishments

- PromptRenderer service renders Jinja2 templates with variable interpolation and syntax validation
- PromptVersionManager service handles explicit activation, ANY-version rollback, and version creation
- PromptMetricsService records extraction-level metrics with Claude API cost calculation (Sonnet/Haiku pricing)
- Convenience function get_active_prompt() for fast active prompt lookups using partial index

## Task Commits

Each task was committed atomically:

1. **Task 1: Create PromptRenderer with Jinja2 templating** - `47e7084` (feat)
2. **Task 2: Create PromptVersionManager for activation/rollback** - `4ffb01a` (feat)
3. **Task 3: Create PromptMetricsService for performance tracking** - `e0606c4` (feat)

## Files Created/Modified

- `app/services/prompt_renderer.py` - Jinja2 template rendering with variable validation and structured logging
- `app/services/prompt_manager.py` - Version lifecycle management (create, activate, rollback, list) with explicit activation flow
- `app/services/prompt_metrics_service.py` - Extraction-level metrics recording with Claude API cost calculation and aggregated stats

## Decisions Made

**1. Jinja2 Environment configuration for LLM prompts**
- Rationale: LLM prompts don't need HTML escaping (autoescape=False). Clean formatting requires trim_blocks and lstrip_blocks to remove unwanted newlines/spaces around Jinja2 blocks.
- Implementation: Environment(autoescape=False, trim_blocks=True, lstrip_blocks=True)

**2. Template syntax validation without rendering**
- Rationale: Per RESEARCH.md Pitfall 5, validating syntax on template creation prevents runtime TemplateSyntaxError during production extractions.
- Implementation: validate_template() method parses template via env.from_string() and catches syntax errors, returning (is_valid, error_message) tuple.

**3. Rollback as activation of historical version**
- Rationale: User decision requires ANY-version rollback (not just previous). Simplest implementation: rollback_to_version() delegates to activate_version() with audit trail annotation.
- Implementation: rollback_to_version() calls activate_version() with activated_by="{user} (ROLLBACK)"

**4. API cost calculation with Decimal precision**
- Rationale: Financial calculations require exact precision. Python Decimal avoids floating-point rounding errors for cost tracking.
- Implementation: calculate_api_cost() returns Decimal with 6 decimal places (quantize to 0.000001)

**5. 7-day default window for version stats**
- Rationale: 7 days provides meaningful recent performance snapshot while keeping query fast on raw metrics table. Longer historical analysis should use PromptPerformanceDaily rollups (future implementation).
- Implementation: get_version_stats(days=7) queries PromptPerformanceMetrics with extracted_at >= cutoff_date

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None - all services implemented successfully following RESEARCH.md patterns.

## User Setup Required

None - no external service configuration required. Services use existing database models from 08-01.

## Next Phase Readiness

**Ready for Phase 8 Plan 3 (Migration Seeding):**
- Service layer complete for prompt management
- PromptRenderer ready for template rendering during extraction
- PromptVersionManager ready for prompt activation/rollback
- PromptMetricsService ready for extraction metrics recording

**Foundation provides:**
- Jinja2 template rendering with validation (PromptRenderer.render, validate_template)
- Explicit activation flow (PromptVersionManager.activate_version)
- ANY-version rollback capability (PromptVersionManager.rollback_to_version)
- Cost calculation for Claude API (Sonnet: $3/$15 per 1M tokens, Haiku: $0.25/$1.25 per 1M tokens)
- Aggregated stats over recent days (get_version_stats)

**Next steps:**
- Seed database with existing hardcoded prompts from codebase (intent_classifier.py, pdf_extractor.py, etc.)
- Migrate extractors to load prompts from database via get_active_prompt()
- Add metrics recording to extraction pipeline via PromptMetricsService.record()

---
*Phase: 08-database-backed-prompt-management*
*Completed: 2026-02-06*
