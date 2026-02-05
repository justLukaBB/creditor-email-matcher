# Phase 4: German Document Extraction & Validation - Context

**Gathered:** 2026-02-05
**Status:** Ready for planning

<domain>
## Phase Boundary

German-specific text processing for creditor documents: Umlaut handling, locale-aware number parsing (1.234,56 EUR), OCR error correction, and German extraction prompts. Does NOT add new extraction fields — applies German processing to existing Phase 3 extraction pipeline.

</domain>

<decisions>
## Implementation Decisions

### Extraction Scope
- **Primary field:** Forderungshöhe (debt amount) — this is what matters
- **For matching only:** Client name, creditor name, reference numbers
- **Skip entirely:** Bank details (IBAN, BIC), Ratenzahlung, addresses, extended Forderungsaufschlüsselung
- This simplifies Phase 4 significantly — focus German optimization on the fields that matter

### OCR Error Correction
- **Moderate correction** — correct common patterns where context supports, skip ambiguous cases
- **Ambiguous patterns stay unchanged** — if 'ss' could be ß or legitimately ss, leave as-is
- **Confidence impact only for names** — correcting proper nouns (client/creditor names) reduces confidence slightly
- **Log summary only** — count of corrections per document, not individual changes

### Prompt Localization
- **Full German prompts** — instructions, examples, and field names all in German
- **Treat mixed-language docs as German** — don't attempt per-section language detection
- **Minimal terminology hints** — include key amount field terms (Gesamtforderung, Hauptforderung) but not full glossary
- **2-3 few-shot examples** — from different creditor types (bank, utility, collection agency)

### Reference Number Formats
- **Flexible extraction** — extract anything that looks like a reference number
- **Only labeled references** — must have a label (Aktenzeichen:, Unser Zeichen:, etc.) to be extracted
- **Preserve original formatting** — no normalization, keep 'AB 123-456' as-is
- Matching engine (Phase 6) handles comparison

### Validation Strictness
- **Accept data anyway** — validation failures are informational, don't reject or reduce confidence
- **Accept all amounts** — no outlier flagging, even implausible values (0.01€ or 10M€)
- Keep extraction simple, let downstream processing handle validation if needed

### Claude's Discretion
- Which labels indicate reference numbers (Aktenzeichen, Geschäftszeichen, Vorgangsnummer, etc.)
- Specific OCR correction patterns and word lists
- Few-shot example selection and formatting

</decisions>

<specifics>
## Specific Ideas

- Forderungshöhe is the critical field — everything else supports matching
- Don't over-engineer validation — extraction should be permissive
- German prompts should feel native, not translated English

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 04-german-document-extraction*
*Context gathered: 2026-02-05*
