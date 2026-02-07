# Testing Guide — Integration with MongoDB

*Created: 2026-02-06 after Phase 9 completion*

## Current Email Processing Flow

```
Zendesk Webhook
     │
     ▼
┌─────────────────────────────────────────────────────────────────┐
│ POST /api/webhook/zendesk                                        │
│   • Validates webhook payload                                    │
│   • Creates IncomingEmail record (PostgreSQL)                    │
│   • Captures correlation_id                                      │
│   • Enqueues to Dramatiq: process_email.send(email_id, corr_id) │
└─────────────────────────────────────────────────────────────────┘
     │
     ▼ (async via Redis + Dramatiq)
┌─────────────────────────────────────────────────────────────────┐
│ process_email actor                                              │
│                                                                  │
│ 1. AGENT 1: Intent Classification                               │
│    • Rule-based fast path (auto_reply, spam detection)          │
│    • Claude Haiku fallback for complex cases                    │
│    • Checkpoint saved to agent_checkpoints JSONB                │
│                                                                  │
│ 2. AGENT 2: Content Extraction                                  │
│    • Downloads attachments from GCS                             │
│    • PDF: PyMuPDF → Claude Vision fallback                      │
│    • DOCX, XLSX: python-docx, openpyxl                          │
│    • Images: Claude Vision                                      │
│    • German text preprocessing (Umlauts, locale parsing)        │
│    • Checkpoint saved                                            │
│                                                                  │
│ 3. AGENT 3: Consolidation                                       │
│    • Merges data from all sources                               │
│    • Conflict detection against existing MongoDB records        │
│    • Checkpoint saved                                            │
│                                                                  │
│ 4. MATCHING ENGINE V2                                           │
│    • Queries creditor_inquiries (30-day window)                 │
│    • RapidFuzz fuzzy matching on names + references             │
│    • Explainability JSONB logged                                │
│                                                                  │
│ 5. CONFIDENCE ROUTING                                           │
│    • HIGH (>0.85): Auto-write to MongoDB                        │
│    • MEDIUM (0.6-0.85): Write + notify review team              │
│    • LOW (<0.6): Route to manual review queue                   │
│                                                                  │
│ 6. DATABASE WRITE (if matched)                                  │
│    • DualDatabaseWriter: PostgreSQL first, MongoDB second       │
│    • Saga pattern with compensating transactions                │
│    • Processing report generated                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Required Environment Variables

Create a `.env` file (or set these in your environment):

```bash
# Required for basic functionality
DATABASE_URL=postgresql://user:pass@host:5432/creditor_matcher
MONGODB_URL=mongodb://user:pass@host:27017/your_db
REDIS_URL=redis://localhost:6379/0

# Required for Claude API extraction
ANTHROPIC_API_KEY=sk-ant-xxx

# Optional but recommended
ENVIRONMENT=development
GCS_BUCKET_NAME=your-attachments-bucket  # For attachment storage
ADMIN_EMAIL=admin@example.com            # For failure notifications

# Optional monitoring
SENTRY_DSN=https://xxx@sentry.io/xxx     # Error tracking
```

## To Test the Full Flow

**1. Install dependencies:**
```bash
pip install -r requirements.txt
```

**2. Run migrations (creates all tables):**
```bash
alembic upgrade head
```

**3. Seed prompts (one-time):**
```bash
python scripts/seed_prompts.py
```

**4. Start Redis (locally or use your hosted Redis):**
```bash
redis-server
# Or set REDIS_URL to your hosted Redis
```

**5. Start the worker (processes jobs):**
```bash
dramatiq app.actors.email_processor --processes 1 --threads 1
```

**6. Start the web server:**
```bash
uvicorn app.main:app --reload
```

**7. Test with a webhook call:**
```bash
curl -X POST http://localhost:8000/api/tickets/incoming \
  -H "Content-Type: application/json" \
  -d '{
    "ticket_id": "12345",
    "requester_email": "creditor@example.com",
    "requester_name": "Test Creditor",
    "subject": "Re: Your debt inquiry",
    "description": "Dear Sir, regarding client Max Müller, the outstanding amount is 1.234,56 EUR.",
    "attachments": []
  }'
```

## What Happens with MongoDB Integration

The system writes to MongoDB via `DualDatabaseWriter` in `app/services/dual_database.py`:

1. **PostgreSQL first** (source of truth) — IncomingEmail record updated
2. **MongoDB second** — Your existing collection updated with extracted debt data
3. **Hourly reconciliation** — Detects PostgreSQL/MongoDB mismatches and repairs

**MongoDB writes happen when:**
- Match confidence is HIGH or MEDIUM
- `DualDatabaseWriter.write_debt_update()` is called
- Uses your existing MongoDB schema (backward compatible)

## Key Files

| Component | File |
|-----------|------|
| Webhook endpoint | `app/routers/webhook.py` |
| Email processor actor | `app/actors/email_processor.py` |
| Intent classification | `app/services/intent_classifier.py` |
| Content extraction | `app/services/extraction/` |
| Matching engine | `app/services/matching/` |
| Dual database writer | `app/services/dual_database.py` |
| MongoDB client | `app/services/mongodb_client.py` |
| Confidence routing | `app/services/confidence/` |
| Manual review queue | `app/services/review_queue.py` |

## Phase 10 (Next)

Shadow mode validation and gradual traffic cutover:
- v2 processes same emails as v1 without writes for accuracy validation
- Gradual traffic cutover: 10% → 50% → 100%
- v1 remains as fallback for 30 days

**To continue:** `/gsd:discuss-phase 10` or `/gsd:plan-phase 10`
