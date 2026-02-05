# Phase 7: Confidence Scoring & Calibration - Context

**Gathered:** 2026-02-05
**Status:** Ready for planning

<domain>
## Phase Boundary

Calibrated confidence scores across dimensions (extraction, matching) enable reliable automation decisions and human review routing. This phase separates confidence into dimensions, implements the weakest-link calculation, configures three-tier routing (high/medium/low), and establishes the foundation for threshold calibration from production data.

</domain>

<decisions>
## Implementation Decisions

### Routing actions
- HIGH confidence (>0.85): Write to database + log entry only, no notification
- MEDIUM confidence (0.6-0.85): Write immediately to database, notify dedicated review team for verification
- LOW confidence (<0.6): Route to manual review queue, expire after 7 days if not processed (flag for batch review)

### Calibration approach
- Build calibration dataset gradually from production (no historical labeling or manual sprint required)
- Use roadmap defaults (0.85/0.60) from day one as initial thresholds
- Labels captured implicitly from reviewer corrections (if reviewer changes data, original was wrong)
- Auto-adjust thresholds with guardrails based on accumulated calibration data

### Threshold configuration
- Global thresholds only — no per-creditor or per-category overrides
- Store threshold values in environment variables (CONFIDENCE_HIGH_THRESHOLD, CONFIDENCE_LOW_THRESHOLD)
- Guardrail: HIGH threshold can auto-adjust down to 0.75 minimum, never lower

### Claude's Discretion
- Document-type differentiation (native PDF vs scanned vs other formats) — determine reasonable approach
- Whether gap between top match and second-best should factor into match_confidence
- Whether intent classification confidence should be included in overall_confidence calculation

</decisions>

<specifics>
## Specific Ideas

- extraction_confidence should be document-level only (not field-level) for simplicity
- extraction_confidence combines source quality baseline + completeness adjustment
- Medium-confidence items write first, then notify — don't block on human review
- 7-day expiration for low-confidence items prevents queue buildup, flags for batch review

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 07-confidence-scoring-calibration*
*Context gathered: 2026-02-05*
