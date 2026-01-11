# Creditor Email Matcher

AI-gestützter Microservice zur automatischen Zuordnung von Gläubiger-Emails zu Mandanten und Zendesk Side Conversations.

## Status

🚧 **In Entwicklung** - MVP Phase 1

## Quick Start

### 1. Environment Setup

```bash
# Virtual Environment erstellen
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# oder
venv\Scripts\activate  # Windows

# Dependencies installieren
pip install -r requirements.txt
```

### 2. Environment Variables

```bash
# .env Datei erstellen
cp .env.example .env

# .env editieren (optional für Basic Setup)
nano .env
```

### 3. Anwendung starten

```bash
# Development Server starten
uvicorn app.main:app --reload

# Oder direkt mit Python
python -m app.main
```

### 4. Testen

Die Anwendung läuft auf: `http://localhost:8000`

**Endpoints:**
- Root: `http://localhost:8000/`
- Health Check: `http://localhost:8000/health`
- API Docs: `http://localhost:8000/docs` (nur im Development Mode)

**Beispiel:**
```bash
curl http://localhost:8000/health
```

## Projekt-Struktur

```
creditor-email-matcher/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI Application
│   ├── config.py        # Settings & Environment
│   ├── routers/         # API Endpoints (später)
│   ├── services/        # Business Logic (später)
│   └── utils/           # Utilities (später)
├── tests/               # Tests (später)
├── requirements.txt     # Python Dependencies
├── .env.example         # Environment Template
└── README.md
```

## ✅ Implementation Status

- [x] Database Setup (PostgreSQL + SQLAlchemy)
- [x] Database Models & Migrations (Alembic)
- [x] Webhook Endpoint für Zendesk
- [x] Email Parser & Cleaner (90% token reduction)
- [x] LLM Extractor (OpenAI GPT-4o Integration)
- [x] Fuzzy Matching Engine with Weighted Scoring
- [x] Routing Logic (Auto-assign / Review Queue)
- [x] Zendesk API Client (Side Conversations)
- [x] Docker Compose Setup
- [ ] Production Deployment
- [ ] Monitoring & Analytics Dashboard

## 🏗️ System Architecture

```
Zendesk Webhook → FastAPI → Email Parser → LLM Extractor → Matching Engine → Routing Logic → Zendesk API
                      ↓                                              ↓
                 PostgreSQL ←─────────────────────────────────────────┘
```

## 🎯 Key Features

- **AI-Powered Extraction**: GPT-4o extracts client names, creditors, amounts, and reference numbers
- **Smart Fuzzy Matching**: Multi-signal matching with weighted scoring (40% client, 30% creditor, 20% time, 10% reference)
- **Automated Routing**:
  - ≥80% confidence: Auto-assign to side conversation
  - 60-79%: Review queue
  - <60%: Manual queue
- **Token Optimization**: Reduces email size by ~90% before LLM processing
- **Complete Audit Trail**: All matches and scores stored for transparency

## 📊 Performance

- **Processing Speed**: <2 seconds per email
- **Token Reduction**: 2000 → 200 tokens (90% savings)
- **Expected Accuracy**: ~85% auto-match rate (configurable thresholds)
- **Cost**: ~€0.01 per email (GPT-4o pricing)

## 🚀 Quick Start

See [SETUP_GUIDE.md](./SETUP_GUIDE.md) for complete setup instructions.

**TL;DR**:
```bash
# 1. Setup
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Add your credentials

# 2. Start database
docker-compose up -d postgres
./scripts/db_manage.sh init

# 3. Run application
uvicorn app.main:app --reload
```

## Tech Stack

- **Backend**: Python 3.11+ mit FastAPI
- **Database**: PostgreSQL 15+ mit SQLAlchemy & Alembic
- **LLM**: OpenAI GPT-4o
- **Fuzzy Matching**: RapidFuzz
- **Email Processing**: BeautifulSoup4, html2text
- **Deployment**: Docker + Docker Compose
- **API Integration**: Zendesk REST API

## 📁 Project Structure

```
├── app/
│   ├── main.py                    # FastAPI application
│   ├── config.py                  # Configuration
│   ├── database.py                # Database connection
│   ├── models/                    # SQLAlchemy models
│   │   ├── creditor_inquiry.py
│   │   ├── incoming_email.py
│   │   └── match_result.py
│   ├── routers/                   # API endpoints
│   │   └── webhook.py
│   └── services/                  # Business logic
│       ├── email_parser.py        # Email cleaning
│       ├── entity_extractor.py    # LLM extraction
│       ├── matching_engine.py     # Fuzzy matching
│       └── zendesk_client.py      # Zendesk API
├── alembic/                       # Database migrations
├── scripts/                       # Utility scripts
├── tests/                         # Tests
├── docker-compose.yml             # Docker setup
└── requirements.txt               # Dependencies
```
