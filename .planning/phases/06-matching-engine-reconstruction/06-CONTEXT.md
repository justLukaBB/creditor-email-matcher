# Phase 6: Matching Engine Reconstruction - Context

**Gathered:** 2026-02-05
**Status:** Ready for planning

<domain>
## Phase Boundary

Rebuild the bypassed v1 matching engine with fuzzy matching (RapidFuzz), creditor_inquiries integration, and explainability. The engine assigns incoming creditor emails to the correct client/creditor pair. Manual review queue integration and confidence scoring calibration are separate phases.

</domain>

<decisions>
## Implementation Decisions

### Matching Signal Priority
- Both reference number (Aktenzeichen) AND name similarity required for match — prevents false matches from reused references
- creditor_inquiries table filters candidates — only consider pairs where we sent an inquiry in the last 30 days
- Weighted average combines signals — configurable weights (e.g., 40% name, 60% reference)
- No auto-match without recent creditor_inquiries record

### Ambiguity Handling
- Top match wins if "clearly ahead" of second place — gap threshold TBD
- When gap threshold not met, route to manual review with top 3 candidates
- Reviewer sees all candidates with their match scores and signal breakdown
- Below-threshold candidates not shown to avoid information overload

### Explainability Format
- Primary audience: developers (for debugging and threshold tuning)
- Storage: PostgreSQL JSONB column on match result record
- Detail level: signal scores only (name_similarity, reference_match, final_score)
- Retention: prune explanations after 90 days to save storage

### Threshold Configuration
- Stored in PostgreSQL table for runtime changes without deployment
- Developers manage via direct database access (no admin API needed)

### Claude's Discretion
- Gap threshold for "clearly ahead" determination (calibrate from error analysis)
- Creditor category definitions (analyze patterns to determine useful groupings)
- Default vs override hierarchy (simplest effective approach)
- Signal weight defaults (based on existing v1 behavior and error patterns)

</decisions>

<specifics>
## Specific Ideas

- Use RapidFuzz for fuzzy name matching (already in requirements)
- Reference number matching should handle OCR errors (fuzzy, not just exact)
- creditor_inquiries filtering significantly narrows search space — this is the key optimization
- 30-day window for inquiry correlation matches business expectation of response times

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 06-matching-engine-reconstruction*
*Context gathered: 2026-02-05*
