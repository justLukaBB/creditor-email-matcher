# Creditor Email Matcher v2

## What This Is

Ein Multi-Agent System zur automatischen Analyse von Gläubiger-Antworten für eine Schuldnerberatungs-Kanzlei. Eingehende Emails und Attachments von Gläubigern werden analysiert, die korrekten Forderungssummen extrahiert und den richtigen Mandanten/Gläubiger-Kombinationen in der Datenbank zugeordnet. Verarbeitet 200+ Emails pro Tag.

## Core Value

Gläubiger-Antworten werden zuverlässig dem richtigen Mandanten und Gläubiger zugeordnet und die Forderungsdaten korrekt in die Datenbank geschrieben — ohne manuellen Aufwand.

## Requirements

### Validated

- ✓ Zendesk Webhook empfängt Gläubiger-Emails — existing v1
- ✓ Email Body wird geparst und bereinigt (HTML → Text, Token-Reduktion) — existing v1
- ✓ Claude extrahiert Entitäten (client_name, creditor_name, debt_amount, reference_numbers) — existing v1
- ✓ MongoDB wird mit Forderungsdaten aktualisiert (final_creditor_list) — existing v1
- ✓ Email-Benachrichtigungen bei Auto-Match — existing v1
- ✓ Webhook Signature Verification und Deduplizierung — existing v1
- ✓ PostgreSQL speichert incoming_emails mit Verarbeitungsstatus — existing v1
- ✓ creditor_inquiries Tabelle wird vom Node.js Mandanten-Portal befüllt — existing

### Active

- [ ] Matching Engine reaktivieren und mit creditor_inquiries verbinden (aktuell übersprungen)
- [ ] Multi-Attachment Processing (PDF, DOCX, Images via Claude Vision)
- [ ] Intent-basierte Verarbeitung (debt_statement, payment_plan, rejection, inquiry, auto_reply, spam)
- [ ] Dynamisches Prompt Repository aus Datenbank statt hardcoded Prompts
- [ ] Consolidation Agent: Daten aus Email Body + Attachments zusammenführen mit Conflict Resolution
- [ ] Confidence-basiertes Routing (Auto-Update / Review / Manual)
- [ ] Job Queue (Celery + Redis) für zuverlässige Async-Verarbeitung bei 200+ Emails/Tag
- [ ] GCS-Integration für Attachment-Speicherung
- [ ] Erweiterte Datenextraktion: Forderungsaufschlüsselung, Bankdaten, Ratenzahlung
- [ ] Completeness Check: Alle Pflichtfelder vorhanden und über Confidence-Threshold
- [ ] Processing Reports pro Email (was extrahiert, was fehlt, Confidence per Field)
- [ ] Document Classification für Attachments (Rechnung, Mahnung, Vertrag, etc.)

### Out of Scope

- Node.js Mandanten-Portal ändern — separater Service, nur shared MongoDB
- Eigene Zendesk-App/UI — Zendesk wird nur als Email-Kanal genutzt
- Real-time Processing Dashboard — Reports reichen, kein Live-Dashboard
- Multi-Kanzlei Support — System ist für eine Kanzlei (Scuric)
- Eigenes OCR — Claude Vision kann PDFs und Images nativ verarbeiten

## Context

### Bestehende Architektur

- **Mandanten-Portal** (Node.js): Sendet Emails an Gläubiger via Zendesk, schreibt `creditor_inquiries` in PostgreSQL
- **Creditor Email Matcher** (Python/FastAPI): Analysiert Gläubiger-Antworten, aktuell v1 mit bekannten Problemen
- **Shared MongoDB**: Beide Services lesen/schreiben `clients` Collection mit `final_creditor_list`
- **PostgreSQL**: Speichert `incoming_emails`, `creditor_inquiries`, `match_results` (letztere aktuell leer)

### Bekannte Probleme in v1

1. **Matching Engine übersprungen**: `matching_engine.py` (357 Zeilen, RapidFuzz) existiert aber wird im Webhook komplett umgangen. Stattdessen geht der Code direkt auf MongoDB mit simplem Name/Aktenzeichen-Matching
2. **Kein Attachment-Handling**: Webhook-Schema hat keine Attachment-Felder, PDFs/Images werden ignoriert
3. **Hardcoded Werte**: Notification-Email, SMTP-Config via os.getenv statt Settings
4. **Kein Error Recovery**: MongoDB-Fehler → nur Log-Warning, kein Retry
5. **Keine Tests**: Test-Verzeichnis leer
6. **MatchResult-Tabelle nie befüllt**: Audit Trail fehlt komplett
7. **Inkonsistente Confidence-Werte**: Integer (0-100) in PostgreSQL, Float (0-1.0) in Extraction

### Technisches Umfeld

- **Hosting**: Render
- **Storage**: Google Cloud Storage (bereits im Einsatz)
- **LLM**: Claude (aktuell 3.5 Sonnet, Upgrade auf Claude Sonnet 4 geplant)
- **Datenbanken**: PostgreSQL (SQLAlchemy + Alembic) + MongoDB (PyMongo)
- **Email**: Zendesk als Eingangskanal, SMTP für Benachrichtigungen

### Existing Codebase

Repository: `github.com/justLukaBB/creditor-email-matcher`
Analysiert am 2026-02-04. Etwa 70% der Grundfunktionalität steht, aber kritische Architekturprobleme verhindern zuverlässigen Produktionsbetrieb.

## Constraints

- **Tech Stack**: Python/FastAPI — bestehende Codebasis, kein Rewrite in andere Sprache
- **Datenbank-Kompatibilität**: MongoDB-Schema muss kompatibel bleiben mit Node.js Mandanten-Portal
- **LLM Provider**: Anthropic Claude — bereits integriert, Vision-Fähigkeit für Attachments nötig
- **Hosting**: Render — muss mit Render-Einschränkungen funktionieren (Worker-Prozesse, Redis Add-on)
- **Storage**: GCS — bereits im Einsatz, kein neuer Storage-Provider
- **Zendesk**: Webhook-Format von Zendesk ist vorgegeben, muss Attachment-URLs unterstützen

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Celery + Redis statt FastAPI Background Tasks | 200+ Emails/Tag, Jobs müssen Crashes überleben | — Pending |
| Claude Vision für PDF/Image statt separatem OCR | Claude kann PDFs nativ, kein zusätzlicher Service nötig | — Pending |
| Intent-basierte Verarbeitung statt One-Size-Fits-All | Verschiedene Email-Typen brauchen verschiedene Extraktionsstrategien | — Pending |
| Prompt Repository in DB statt hardcoded | Prompts müssen ohne Deployment anpassbar sein | — Pending |
| Matching Engine reaktivieren statt neu bauen | 357 Zeilen bestehender Code, creditor_inquiries werden befüllt | — Pending |
| 3-Agent Architektur (Email Processing → Content Extraction → Consolidation) | Separation of Concerns, unabhängig skalierbar | — Pending |

---
*Last updated: 2026-02-04 after initialization*
