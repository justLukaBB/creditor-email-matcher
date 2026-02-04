# Phase 3: Multi-Format Document Extraction - Context

**Gathered:** 2026-02-04
**Status:** Ready for planning

<domain>
## Phase Boundary

Extract text and structured data from creditor email attachments (PDFs, DOCX, XLSX, images) and email bodies using PyMuPDF + Claude Vision fallback. Cost controls via token budgets. This phase builds extraction capability only — German-specific processing is Phase 4, multi-agent pipeline is Phase 5.

</domain>

<decisions>
## Implementation Decisions

### Extraction target
- Primary extraction goal: **Gesamtforderung** (total claim amount including principal + interest + costs)
- When no explicit Gesamtforderung label exists, **sum components** (Hauptforderung + Zinsen + Kosten) to compute it
- Also extract: **client_name** and **creditor_name** — needed for matching engine in Phase 6
- Extended fields from roadmap (Forderungsaufschluesselung, Bankdaten, Ratenzahlung) are not priority — focus on Forderungshoehe + names

### Source priority
- Email body commonly contains Forderungshoehe — must extract from body text, not just attachments
- When both email body AND attachment contain a Forderungshoehe: **highest amount wins**
- All format types appear in production: PDF, DOCX, XLSX, images — no format can be deprioritized

### Fallback amount
- When no Forderungshoehe is found in any source (body + all attachments): **default to 100 EUR**
- This is a fixed business rule, not a DB lookup — simply insert 100 EUR as the Forderungshoehe

### Long document handling
- PDFs exceeding 10-page limit: process **first 5 + last 5 pages**
- Rationale: key financial data and totals often appear at document end

### Unreadable attachments
- Password-protected or encrypted PDFs: **skip that attachment, continue processing remaining sources**
- Do not fail the entire job for one unreadable attachment

### PDF characteristics
- PDFs are mostly digitally generated (text-selectable) — PyMuPDF will handle the majority
- Claude Vision fallback needed primarily for scanned documents and images, not the common case

### Claude's Discretion
- PyMuPDF-to-Vision fallback detection logic (what constitutes "scanned" or "complex")
- DOCX/XLSX extraction library choice
- Token budget enforcement implementation
- GCS storage patterns and temp file cleanup
- Cost circuit breaker threshold and behavior
- Confidence scoring per extracted field

</decisions>

<specifics>
## Specific Ideas

- "For now we just need the newest Forderungshoehe" — keep extraction simple, don't over-extract
- The 100 EUR default is a business rule for cases where no amount can be determined
- Highest-amount-wins rule when multiple sources provide conflicting Forderungshoehe values

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 03-multi-format-document-extraction*
*Context gathered: 2026-02-04*
