# Feature Landscape

**Domain:** Creditor Email Analysis / Legal Document Processing
**Researched:** 2026-02-04
**Confidence:** MEDIUM (based on domain knowledge, cannot verify current sources)

## Table Stakes

Features users expect. Missing = product feels incomplete or unreliable.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Email Body Text Extraction** | Core functionality - every email has a body | Low | Already implemented in existing system |
| **PDF Attachment Processing** | Forderungsaufstellungen typically come as PDFs | Medium | Not yet implemented - critical gap |
| **Basic Intent Classification** | Must route emails to correct workflow | Medium | Need categories: Forderungsaufstellung, Ratenzahlungsvereinbarung, Ablehnung, Rückfrage |
| **Key Entity Extraction** | Client name, creditor name, reference numbers are minimum viable data | Medium | Core business value - without this, manual review required for everything |
| **Structured Data Output** | Extracted data must be machine-readable for downstream systems | Low | JSON/database format for integration |
| **Batch Processing** | 200+ emails/day cannot be processed one-at-a-time | Medium | Queue-based processing architecture required |
| **Error Handling & Logging** | Must track processing failures for investigation | Low | Critical for production reliability |
| **Basic Confidence Scoring** | Need to know when extraction is uncertain | Medium | Per-field confidence scores (e.g., "90% confident this is the debt amount") |
| **Human Review Queue** | Low-confidence items must route to human review | Low | Simple queue/flag system - prevents bad data entering system |
| **Multi-page Document Support** | Legal documents often span multiple pages | Medium | Must extract across page boundaries |

## Differentiators

Features that set product apart. Not expected, but provide significant value.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Multi-document Consolidation** | One client case may have multiple emails with partial information - merge intelligently | High | Example: First email has client name, second has payment plan, third has bank details. System recognizes same case and merges. |
| **Smart Matching to Existing Records** | Automatically match incoming creditor reply to existing client/case in database | High | Prevents duplicate entries, reduces manual reconciliation. Fuzzy matching on names, reference numbers. |
| **Conflict Detection** | Identify when new information contradicts existing data | Medium | Example: Previous email said debt was 5000 EUR, new email says 5500 EUR - flag for review |
| **German Legal Document Understanding** | Recognize German legal terminology, formats, conventions | High | Specific to domain - generic OCR misses context like "Ratenzahlungsvereinbarung" vs "Ratenplan" |
| **Creditor-specific Template Recognition** | Different creditors format documents differently - learn patterns | High | Example: Bank A always puts reference number in top-right, Bank B puts it in footer. Template library improves accuracy. |
| **Attachment Type Classification** | Identify document type from PDF structure, not just filename | Medium | Example: Even if PDF is named "scan.pdf", recognize it's a Forderungsaufstellung from structure |
| **Payment Plan Table Extraction** | Extract structured payment schedules (dates, amounts) from tables | High | Common in Ratenzahlungsvereinbarung documents - complex table structures |
| **Bank Detail Extraction (IBAN/BIC)** | Automatically extract payment information with validation | Medium | Format validation ensures data quality - catch OCR errors |
| **Multi-language Support (German/English)** | Handle correspondence in multiple languages | Medium | Some creditors use English, especially international banks |
| **Historical Accuracy Tracking** | Track which creditors/document types extract reliably vs problematic | Medium | Feedback loop - surfaces patterns like "Creditor X documents always fail" |
| **Duplicate Detection** | Identify when same email/document processed multiple times | Low | Prevents data duplication from re-forwarding or CC situations |
| **Context-aware Extraction** | Use email context (subject, sender) to improve PDF extraction | Medium | Example: If subject says "Re: Client Müller", prioritize "Müller" as likely client name in PDF |

## Anti-Features

Features to explicitly NOT build. Common mistakes in this domain.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **Fully Automated Processing (No Human Review)** | Legal domain requires human accountability - 100% automation is risky and creates liability | Always provide human review queue for low-confidence items. Goal is augmentation, not replacement. |
| **Single ML Model for All Document Types** | Different document types have different structures - one model fits none | Build document-type-specific extraction logic or models. Classification first, then specialized extraction. |
| **Real-time Synchronous Processing** | 200+ emails/day with PDF processing = performance bottleneck, UI hangs | Use async queue architecture. Process in background, surface results when ready. |
| **Training Data from Production Without Validation** | Feeding incorrect extractions back as training data creates drift | Require human validation before adding to training set. Feedback loop must be curated. |
| **Complex Custom ML Models Without Fallback** | Custom models are fragile - what if they break? | Use rule-based fallbacks. Hybrid approach: ML for hard cases, rules for obvious patterns. |
| **Overly Granular Confidence Scores** | "This field is 87.3% confident" gives false precision | Use simple tiers: HIGH (>90%), MEDIUM (70-90%), LOW (<70%). Actionable thresholds. |
| **Automatic Data Overwriting** | New extraction shouldn't silently overwrite existing database records | Flag conflicts, require human decision. Preserve data lineage. |
| **Generic OCR Without Post-processing** | Raw OCR output is noisy (e.g., "5OOO EUR" instead of "5000 EUR") | Apply domain-specific validation and correction (e.g., currency amount patterns, German name patterns) |
| **Client-specific Customization in Core Logic** | Hardcoding "If creditor == XYZ then..." creates unmaintainable mess | Use configuration/template system. Keep core logic generic, variation in config. |
| **Processing Emails Individually Without Case Context** | Email 1: partial info. Email 2: more info. Treating separately loses connection. | Implement case/client matching - link related emails, consolidate information. |

## Feature Dependencies

```
Document Processing Pipeline:
1. Email Receipt → Intent Classification
2. Intent Classification → Attachment Type Detection
3. Attachment Type Detection → Specialized Extraction
4. Extraction → Confidence Scoring
5. Confidence Scoring → Human Review Queue (if LOW)
6. Extraction + Confidence → Matching to Existing Records
7. Matching → Conflict Detection
8. Final Data → Structured Output

Critical Path:
- Cannot extract specialized data without document type classification
- Cannot route to review queue without confidence scoring
- Cannot consolidate without matching logic
- Cannot detect conflicts without existing data comparison

Parallel Capabilities (can be built independently):
- Bank detail validation
- Duplicate detection
- Historical accuracy tracking
- Multi-language support
```

## Feature Complexity Analysis

### Quick Wins (Low Complexity, High Value)
1. **Error logging** - Essential for debugging production issues
2. **Duplicate detection** - Hash-based, straightforward implementation
3. **Basic confidence scoring** - Simple heuristics (field populated vs empty)
4. **Human review queue** - Flag + UI list

### Medium Effort, High Value
1. **PDF attachment processing** - Existing libraries (PyPDF2, pdfplumber), need integration
2. **Intent classification** - 4-5 categories, supervised ML or rule-based
3. **Key entity extraction** - NER models or regex patterns
4. **Bank detail extraction + validation** - IBAN has checksum validation

### Complex, High Value (Differentiators)
1. **Multi-document consolidation** - Requires entity resolution, data merging logic
2. **Smart matching to existing records** - Fuzzy matching, scoring system
3. **German legal document understanding** - Domain-specific NER, potentially custom training
4. **Creditor-specific template recognition** - Template library, pattern matching

### Complex, Lower Priority
1. **Payment plan table extraction** - Complex table structures, OCR challenges
2. **Historical accuracy tracking** - Requires feedback collection, metrics pipeline
3. **Context-aware extraction** - Multi-modal processing (email + PDF together)

## German Debt Collection Document Types

Based on domain context, standard intent categories:

| Category | German Term | Expected Content | Frequency Estimate |
|----------|-------------|------------------|-------------------|
| **Claim Statement** | Forderungsaufstellung | Itemized list of debts, amounts, dates, reference numbers | High (40%) |
| **Payment Agreement** | Ratenzahlungsvereinbarung / Ratenplan | Payment schedule, installment amounts, bank details | Medium (25%) |
| **Rejection** | Ablehnung | Refusal to negotiate, reasons, possibly counter-claims | Medium (20%) |
| **Inquiry/Question** | Rückfrage | Request for more information, missing documents | Low (10%) |
| **Status Update** | Statusmitteilung | Case status, next steps, procedural updates | Low (5%) |

**Note:** Frequencies are estimates based on typical debt counseling workflows. Actual distribution should be measured from production data.

## MVP Recommendation

For MVP (Milestone), prioritize this sequence:

### Phase 1: Core Pipeline (Unblocks current bottleneck)
1. **PDF attachment processing** - Critical gap, blocks all other attachment features
2. **Basic intent classification** - Route documents to correct handling logic
3. **Key entity extraction** - Client name, creditor name, amounts, reference numbers
4. **Confidence scoring** - Per-field confidence to enable review queue
5. **Human review queue** - Low-confidence items don't enter system automatically

**Why this order:** Current system only processes email body. PDF attachments are where most critical data lives (Forderungsaufstellungen). Without PDF processing, system cannot handle primary document type.

### Phase 2: Data Quality (Prevents bad data accumulation)
1. **Matching to existing records** - Prevent duplicate entries
2. **Conflict detection** - Flag contradicting information
3. **Bank detail validation** - IBAN/BIC format checking
4. **Batch processing optimization** - Handle 200+ emails/day efficiently

**Why this order:** Once extraction works, data quality becomes critical. Bad data accumulating in system is worse than no automation.

### Phase 3: Specialized Features (Differentiators)
1. **Payment plan table extraction** - High-value for Ratenzahlungsvereinbarung
2. **Creditor-specific templates** - Improve accuracy for high-volume creditors
3. **Multi-document consolidation** - Handle complex cases with multiple emails
4. **Historical accuracy tracking** - Continuous improvement feedback loop

**Why defer:** These provide quality improvements but are not blockers. Build on solid foundation first.

## Defer to Post-MVP

- **Multi-language support**: Current volume is German-only, add when English correspondence appears
- **Context-aware extraction**: Complex, marginal improvement over simpler approaches
- **Advanced ML models**: Start with rule-based + simple ML, upgrade when hit accuracy ceiling
- **Client-specific customization**: Handle via configuration when pattern emerges, not prematurely

## Open Questions (Validation Needed)

- **Q: What is actual distribution of document types?** Measure from production email logs to validate intent classification categories.
- **Q: What creditors represent 80% of volume?** Prioritize template recognition for high-volume creditors first.
- **Q: What is acceptable error rate for automatic processing?** Define threshold for routing to human review (e.g., <90% confidence = manual review).
- **Q: How are emails currently received?** Email forwarding? API integration? Affects architecture.
- **Q: What is downstream system integration?** Where does extracted data go? Database? CRM? Determines output format requirements.

## Sources

**Confidence: MEDIUM**
- Domain knowledge of document processing systems (LOW confidence - cannot verify current SOTA without web access)
- Legal automation patterns from training data (LOW confidence - may be outdated)
- German debt collection workflow understanding (MEDIUM confidence - based on project context provided)
- Email classification systems architecture (MEDIUM confidence - established patterns, but cannot verify current implementations)

**Recommendation:** Validate these findings with:
1. German legal tech providers (e.g., LegalTech.de research)
2. Document processing platforms (e.g., Azure Form Recognizer, AWS Textract capabilities)
3. Debt counseling software vendors (if accessible)
4. Production data analysis from current system (most authoritative for this specific use case)
