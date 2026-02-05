# Phase 5: Multi-Agent Pipeline with Validation - Context

**Gathered:** 2026-02-05
**Status:** Ready for planning

<domain>
## Phase Boundary

Three-agent orchestration system where:
- Agent 1 (Email Processing) classifies intent and routes to extraction strategy
- Agent 2 (Content Extraction) processes each source with per-source structured results
- Agent 3 (Consolidation) merges data from all sources, resolves conflicts, computes confidence

Matching engine, confidence calibration, and production hardening are separate phases.

</domain>

<decisions>
## Implementation Decisions

### Intent Classification
- 6 intent types: debt_statement, payment_plan, rejection, inquiry, auto_reply, spam
- Ambiguous intent defaults to debt_statement (most common type, let downstream confidence flag issues)
- Intent-specific extraction strategies: debt_statement extracts amounts; payment_plan extracts terms; rejection extracts reason codes
- Skip extraction entirely for auto_reply and spam intents (saves tokens, no useful data)

### Validation Behavior
- Low confidence (< 0.7): proceed with flag ('needs_review'), don't block pipeline
- Schema validation failure: null the failed field, preserve other fields (partial results OK)
- Uniform validation across intent types (no required fields per intent)
- Conflicting data with database: trust new extraction, overwrite existing (latest email is source of truth)

### Claude's Discretion
- Conflict resolution strategy when multiple sources (body + attachments) have different values
- Manual review queue implementation details (how flagged items are surfaced)
- Checkpoint system storage format and retention policy
- Per-source confidence calculation algorithm

</decisions>

<specifics>
## Specific Ideas

- Pipeline should be permissive: prefer proceeding with flags over blocking
- Auto-reply and spam detection should be cheap (no Claude API calls for classification)
- Existing Phase 3 extraction infrastructure (extractors, consolidator) should be reused by Agent 2

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 05-multi-agent-pipeline-validation*
*Context gathered: 2026-02-05*
