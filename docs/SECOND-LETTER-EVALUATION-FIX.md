# Fix-Plan: 2.-Anschreiben-Auswertung

**Repo:** `creditor-email-matcher`
**Scope:** Fix A (Anhänge in Settlement-Extraction) + Fix D (Konsistenz-Checks)
**Zielzweig:** `fix/second-letter-evaluation`
**Status:** Approved — bereit zur Umsetzung
**Datum:** 2026-04-27

---

## Hintergrund

Der Matcher unterscheidet 1. Anschreiben (Forderungsbestätigung) und 2. Anschreiben (Schuldenbereinigungsplan / Settlement-Vorschlag). 2.-Anschreiben-Antworten laufen über einen **Spezialpfad** mit eigenem LLM-Extractor (`SettlementExtractor`, Claude Haiku) statt der regulären Multi-Agent-Pipeline.

Aktueller Flow für 2. Anschreiben:
```
Webhook → DeterministicRouter → letter_type='second' erkannt
  → _process_second_round (email_processor.py:1401)
    → settlement_extractor.extract()
    → MongoDB write
    → Portal-Webhook /settlement-response
```

Zwei konkrete Schwachstellen sind in der bisherigen Auswertung identifiziert worden.

---

## Fix A — Anhänge in Settlement-Extraction durchreichen

### Problem
`SettlementExtractor.extract()` akzeptiert bereits einen `attachment_texts`-Parameter (`app/services/settlement_extractor.py:63`), aber **keiner der zwei Caller in `email_processor.py` setzt ihn**. Vergleichsangebote in PDF-Anhängen werden daher ignoriert — der LLM sieht nur den Email-Body.

### Aufrufpfade

| Pfad | Caller | `extraction_result` verfügbar? |
|------|--------|--------------------------------|
| Deterministic Match | `email_processor.py:407` | ❌ content_extractor wird übersprungen |
| Actor Pipeline (V1-Fallback) | `email_processor.py:1054` | ✅ `extraction_result["attachment_texts"]` da |

### Änderungen

**1. Signature von `_process_second_round` erweitern** (`app/actors/email_processor.py:1401`):
```python
def _process_second_round(
    db,
    email,
    email_id: int,
    matched_inquiry,
    matching_result,
    client_name: Optional[str],
    client_aktenzeichen: Optional[str],
    creditor_email: str,
    creditor_name: str,
    email_body: str,
    subject: Optional[str],
    confidence_result,
    route,
    attachment_texts: Optional[List[str]] = None,   # NEU
):
```

**2. An Settlement-Extractor durchreichen** (`app/actors/email_processor.py:1432`):
```python
settlement_result = settlement_extractor.extract(
    email_body=email_body,
    from_email=creditor_email,
    subject=subject,
    attachment_texts=attachment_texts,   # NEU
)
```

**3. Caller V1-Pfad** (`app/actors/email_processor.py:1054`) — `attachment_texts` aus `extraction_result` durchreichen:
```python
_process_second_round(
    ...
    attachment_texts=extraction_result.get("attachment_texts"),
)
```

**4. Caller Deterministic-Pfad** (`app/actors/email_processor.py:407`) — Hier läuft der `content_extractor` aktuell **nicht**. Dadurch sind Anhänge gar nicht extrahiert verfügbar.

**Entscheidung:**
- **Option 1 (empfohlen, 80/20):** Lightweight-Extraction nur bei `letter_type=='second'` triggern. Vor `_process_second_round`-Aufruf den `ContentExtractor` mit Token-Budget aufrufen, falls `email.attachment_urls` nicht-leer.
- Option 2: Deterministic-Pfad immer durch `content_extractor` laufen lassen (höhere Kosten, konsistentere Daten, größerer Refactor).

**Empfehlung:** Option 1 — neue Helper-Funktion `_extract_attachments_for_second_round(email)` in `app/actors/email_processor.py`, die nur bei Second-Letter im Deterministic-Pfad aufgerufen wird. Wiederverwendung von `app/actors/content_extractor.py:_extract_attachment`.

### Tests
- `tests/test_settlement_extractor.py` — Fall mit Mock-PDF-Text, der "Wir bieten 3500 EUR statt 5000" enthält → erwartet `counter_offer_amount == 3500.0`.
- `tests/test_second_round_pipeline.py` — Email mit `attachment_urls` durchlaufen lassen, verifizieren dass `attachment_texts` an LLM übergeben wird (mock LLM, assert auf prompt content).

---

## Fix D — Konsistenz-Checks statt blindem `no_clear_response`-Override

### Problem
Aktuelle Logik (`app/actors/email_processor.py:1438-1442`):
```python
needs_review = (
    settlement_result.confidence < 0.70
    or settlement_result.settlement_decision == "no_clear_response"
)
```

`no_clear_response` IST eine valide Klassifikation und gehört zu Review — der Override ist OK. **Echtes Problem:** Es gibt keine **Sanity-Checks zwischen Decision und Feldern**. Beispiele aus realen Antworten:

- `decision=counter_offer` aber `counter_offer_amount=null` → unvollständige Extraktion, sollte Review.
- `decision=accepted` aber `conditions` enthält "nur wenn", "vorbehaltlich", "Ratenzahlung" → wahrscheinlich eigentlich `counter_offer`.
- `counter_offer_amount > 2× original_debt` → LLM-Halluzination, Review nötig.

### Änderung

**1. Neue Funktion `validate_consistency` in `app/services/settlement_extractor.py`:**
```python
def validate_consistency(
    result: SettlementExtractionResult,
    original_debt: Optional[float],
) -> tuple[bool, list[str]]:
    """Returns (inconsistent, warnings_list)."""
    warnings: list[str] = []

    # counter_offer ohne Betrag
    if result.settlement_decision == "counter_offer" and not result.counter_offer_amount:
        warnings.append("counter_offer_without_amount")

    # accepted mit Bedingungen die nach Vorbehalt klingen
    if result.settlement_decision == "accepted" and result.conditions:
        suspicious = ["nur wenn", "vorbehaltlich", "sofern", "rate", "einmalzahlung"]
        cond_lower = result.conditions.lower()
        if any(s in cond_lower for s in suspicious):
            warnings.append("accepted_with_conditional_phrasing")

    # Plausibilitätsprüfung Betrag
    if result.counter_offer_amount is not None and original_debt:
        if result.counter_offer_amount > original_debt * 2:
            warnings.append("counter_offer_exceeds_2x_original")
        if result.counter_offer_amount < 0:
            warnings.append("negative_counter_offer")

    return (len(warnings) > 0, warnings)
```

**2. Hook in `_process_second_round`** (`app/actors/email_processor.py:1438`):
```python
from app.services.settlement_extractor import settlement_extractor, validate_consistency

inconsistent, warnings = validate_consistency(
    settlement_result,
    original_debt=getattr(matched_inquiry, 'claim_amount', None),
)

needs_review = (
    settlement_result.confidence < 0.70
    or settlement_result.settlement_decision == "no_clear_response"
    or inconsistent
)

if warnings:
    logger.info("settlement_consistency_warnings", extra={
        "email_id": email_id, "warnings": warnings,
    })
    checkpoints["settlement_extraction"]["consistency_warnings"] = warnings
```

### Tests
Neue Datei `tests/test_settlement_consistency.py` mit Fixtures pro Inkonsistenz-Typ:
- `counter_offer` ohne `counter_offer_amount` → inconsistent
- `accepted` + `conditions="nur wenn Einmalzahlung"` → inconsistent
- `counter_offer_amount=20000` bei `original_debt=5000` → inconsistent
- Saubere `accepted`-Antwort → not inconsistent

---

## Out-of-Scope (bewusst)

- Neuer `partial_payment` Intent — Schema-Change, separates Ticket.
- Settlement-Result zusätzlich in `email.extracted_data` schreiben — Portal-Side-Konsistenz, nicht Matcher.
- Cross-Validation Routing-Confidence × Settlement-Confidence — eigenes Ticket.
- Strukturierte Ratendaten (`proposed_monthly_amount`, `proposed_installments`) — Schema-Erweiterung, separates Ticket.

---

## Reihenfolge & Aufwand

| # | Schritt | Aufwand | Risiko |
|---|---------|---------|--------|
| 1 | Caller V1 fixen (`email_processor.py:1054`) — `attachment_texts` durchreichen | 15 min | Sehr niedrig |
| 2 | Signatur `_process_second_round` + Forwarding an Extractor | 30 min | Niedrig |
| 3 | Helper für Deterministic-Pfad Attachment-Extract (Option 1) | 2-3h | Mittel (Token-Budget, Kostenkontrolle) |
| 4 | `validate_consistency` + Hook in `_process_second_round` | 1h | Niedrig |
| 5 | Tests (Unit + Integration) | 2h | — |
| | **Total** | **~6h** | |

**Quick-Win-Subset (Schritte 1, 2, 4 — ~1.5h):**
- Bringt ca. 70% des Werts ohne `content_extractor`-Refactor im Deterministic-Pfad.
- Anhänge funktionieren dann zumindest im Actor-Pipeline-Pfad (V1-Fallback).
- Konsistenz-Checks greifen in beiden Pfaden.
- Schritt 3 kann als Follow-Up nachgezogen werden.

---

## Acceptance-Kriterien

- [ ] `_process_second_round` akzeptiert `attachment_texts`.
- [ ] V1-Caller (`email_processor.py:1054`) übergibt `extraction_result["attachment_texts"]`.
- [ ] Deterministic-Caller (`email_processor.py:407`) extrahiert Anhänge bei Second-Letter (Schritt 3).
- [ ] `validate_consistency` deckt alle 4 dokumentierten Inkonsistenz-Fälle ab.
- [ ] `needs_review` reagiert auf Inkonsistenzen.
- [ ] `consistency_warnings` werden in `agent_checkpoints` persistiert und geloggt.
- [ ] Unit-Tests für Konsistenz-Checks bestehen.
- [ ] Integration-Test: PDF-Anhang mit Counter-Offer wird korrekt extrahiert.

---

## Referenzen

- `app/services/settlement_extractor.py` — Settlement-LLM-Extraktor
- `app/actors/email_processor.py:1401` — `_process_second_round`
- `app/actors/email_processor.py:407` — Deterministic-Pfad Caller
- `app/actors/email_processor.py:1054` — V1-Pfad Caller
- `app/actors/content_extractor.py:177` — `_extract_attachment` zur Wiederverwendung
- `app/models/intent_classification.py:50-69` — `SettlementExtractionResult`
