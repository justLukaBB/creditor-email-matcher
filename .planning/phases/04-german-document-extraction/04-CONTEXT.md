# Phase 4: German Document Extraction & Validation - Context

**Gathered:** 2026-02-05
**Status:** Ready for planning

<domain>
## Phase Boundary

German-specific text processing that handles Umlauts, locale formats (1.234,56 EUR), and legal terminology correctly — preventing parsing errors and mismatches in extracted data. This phase optimizes the Phase 3 extraction for German documents specifically.

Out of scope: IBAN/BIC extraction (Ratenzahlung handling not relevant to this system).

</domain>

<decisions>
## Implementation Decisions

### Validation Strictness
- **Amount parsing:** Try German format first (1.234,56), fall back to US format (1,234.56) — accept whichever parses
- **Name cleanup:** Auto-correct obvious OCR errors before matching (3→e, 0→o, 1→l in names)
- **IBAN/BIC:** Not extracted — payment plan details handled manually, out of scope for automated extraction

### Mixed-Language Handling
- **Document language:** Assume German always — all creditor replies are German
- **English emails:** Process anyway with German rules — rare edge case not worth special handling
- **Prompt language:** Use German prompts with German examples for Claude extraction
- **Legal terminology:** Accept equivalents (Schulden, offener Betrag, Gesamtsumme) not just formal terms (Gesamtforderung, Hauptforderung)

### OCR Correction Scope
- **Umlaut restoration:** Context-based — correct known German words (Muller→Müller), leave unknown words unchanged
- **Character substitutions:** Fix digit→letter errors in name/address fields only, not in amounts or reference numbers
- **Correction logging:** Log errors only — when correction fails or seems uncertain
- **Confidence impact:** No reduction from corrections — they fix the problem, confidence based on final extraction result

### Claude's Discretion
- Specific regex patterns for German format validation
- Unicode normalization implementation (NFKC)
- German word dictionary for context-based correction
- Prompt wording and example selection

</decisions>

<specifics>
## Specific Ideas

- German prompts should use realistic creditor response examples
- Accept informal synonyms alongside legal terminology for broader extraction coverage
- OCR correction should be conservative — better to miss a correction than introduce errors

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 04-german-document-extraction*
*Context gathered: 2026-02-05*
