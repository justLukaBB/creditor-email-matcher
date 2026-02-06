# Phase 8: Database-Backed Prompt Management - Context

**Gathered:** 2026-02-06
**Status:** Ready for planning

<domain>
## Phase Boundary

Move Claude prompts from hardcoded strings to PostgreSQL storage with version tracking, audit trails, and performance metrics. Enables prompt updates without redeployment, rollback on regression, and data-driven prompt optimization.

</domain>

<decisions>
## Implementation Decisions

### Performance Tracking
- Track BOTH cost metrics (token usage input/output, API cost per extraction) AND quality metrics (extraction success rate, confidence scores, manual review rate)
- Dual aggregation: raw extraction-level data for recent period + daily rollups for historical
- 30-day retention for raw metrics, then aggregate to daily summaries
- Alerting on performance degradation: Claude's discretion based on system needs

### Template Structure
- Organize prompts by task type (classification, extraction, validation) not by agent
- Free-form human-readable names — no strict naming convention enforced
- Variable types and templating complexity: Claude's discretion based on actual prompt needs in codebase
- System/user prompt separation: Claude's discretion based on how prompts are currently structured

### Version Lifecycle
- Version creation mechanism: Claude's discretion based on typical prompt management patterns
- Activation: Explicit manual 'activate' action required — no auto-activation of latest version
- Rollback: Ability to select and activate ANY historical version (not just previous)
- Retention: Archive/delete old inactive versions after retention period (not kept forever)

### Claude's Discretion
- Templating engine choice (simple placeholders vs Jinja2 with conditionals)
- Prompt structure (combined vs separated system/user templates)
- Version creation flow (copy-on-edit vs explicit versioning)
- Performance alerting implementation
- Archive retention period for old versions

</decisions>

<specifics>
## Specific Ideas

- Explicit activation ensures intentional deployments — safe for production
- Historical version selection provides flexibility beyond simple rollback
- Task-type organization aligns with multi-agent pipeline (Agent 1 classification, Agent 2 extraction, etc.)

</specifics>

<deferred>
## Deferred Ideas

- A/B testing mechanics (traffic splitting, winner selection, auto-promotion) — user did not select for discussion, can be added as COULD-have or future enhancement
- Prompt comparison UI/dashboard — belongs in Phase 9 (monitoring) or separate tooling phase

</deferred>

---

*Phase: 08-database-backed-prompt-management*
*Context gathered: 2026-02-06*
