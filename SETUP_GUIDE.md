# 🚀 Setup Guide - Creditor Email Matching System

Complete guide to set up and deploy the AI-powered creditor email matching system.

## 📋 Table of Contents

1. [Prerequisites](#prerequisites)
2. [Local Development Setup](#local-development-setup)
3. [Configuration](#configuration)
4. [Database Setup](#database-setup)
5. [Testing the System](#testing-the-system)
6. [Deployment](#deployment)
7. [Troubleshooting](#troubleshooting)

---

## 🔧 Prerequisites

### Required Software

- **Python 3.11+** (recommended: 3.11 or 3.12)
- **Docker & Docker Compose** (for PostgreSQL)
- **Git**

### Required API Credentials

- ✅ OpenAI API Key (for GPT-4o)
- ✅ Zendesk Account with API access
  - Subdomain (e.g., `rascuric`)
  - Email/token authentication
  - API token

---

## 💻 Local Development Setup

### Step 1: Create Virtual Environment

```bash
# Create virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate  # macOS/Linux
# or
venv\Scripts\activate  # Windows
```

### Step 2: Install Dependencies

```bash
# Install all Python dependencies
pip install -r requirements.txt
```

### Step 3: Environment Configuration

```bash
# Copy the example environment file
cp .env.example .env

# Edit .env with your actual credentials
nano .env  # or use your preferred editor
```

**Required `.env` variables:**

```env
# Application
ENVIRONMENT=development
LOG_LEVEL=INFO

# Database (uses Docker Compose defaults)
DATABASE_URL=postgresql://creditor_user:creditor_pass@localhost:5432/creditor_matcher

# OpenAI
OPENAI_API_KEY=sk-your-actual-openai-api-key-here
OPENAI_MODEL=gpt-4o

# Zendesk
ZENDESK_SUBDOMAIN=rascuric
ZENDESK_EMAIL=thomas@ra-scuric.de
ZENDESK_API_TOKEN=your-zendesk-api-token-here

# Security
WEBHOOK_SECRET=your-random-secure-string-here

# Matching Configuration (optional - defaults provided)
MATCH_THRESHOLD_HIGH=0.80
MATCH_THRESHOLD_MEDIUM=0.60
MATCH_LOOKBACK_DAYS=60
```

---

## 🗄️ Database Setup

### Step 1: Start PostgreSQL with Docker

```bash
# Start PostgreSQL container
docker-compose up -d postgres

# Verify it's running
docker-compose ps
```

### Step 2: Initialize Database Schema

```bash
# Make the database management script executable
chmod +x scripts/db_manage.sh

# Initialize database (creates tables)
./scripts/db_manage.sh init
```

This will:
- Start PostgreSQL if not running
- Create initial Alembic migration
- Apply migration to create all tables

### Alternative: Manual Database Setup

```bash
# Create migration
alembic revision --autogenerate -m "initial schema"

# Apply migration
alembic upgrade head

# Check status
alembic current
```

---

## 🧪 Testing the System

### Step 1: Start the Application

```bash
# Start the FastAPI server
uvicorn app.main:app --reload

# Or use Python directly
python -m app.main
```

The application will be available at:
- API: http://localhost:8000
- Interactive docs: http://localhost:8000/docs
- Health check: http://localhost:8000/health

### Step 2: Test Health Endpoint

```bash
curl http://localhost:8000/health
```

Expected response:
```json
{
  "status": "healthy",
  "environment": "development",
  "services": {
    "api": "running",
    "database": "connected",
    "openai": "configured"
  }
}
```

### Step 3: Test Webhook Endpoint (Mock)

Create a test file `test_webhook.json`:

```json
{
  "ticket_id": "12345",
  "subject": "Re: Anfrage - Max Mustermann",
  "from_email": "info@sparkasse-bochum.de",
  "from_name": "Sparkasse Bochum",
  "body_html": "<p>Sehr geehrte Damen und Herren,<br><br>bezüglich Ihres Mandanten Herrn Max Mustermann teilen wir Ihnen mit, dass die Forderung 1.234,56 EUR beträgt.<br><br>Aktenzeichen: AZ-12345<br><br>Mit freundlichen Grüßen<br>Sparkasse Bochum</p>",
  "body_text": "Sehr geehrte Damen und Herren,\n\nbezüglich Ihres Mandanten Herrn Max Mustermann teilen wir Ihnen mit, dass die Forderung 1.234,56 EUR beträgt.\n\nAktenzeichen: AZ-12345\n\nMit freundlichen Grüßen\nSparkasse Bochum",
  "received_at": "2024-01-07T10:30:00Z",
  "webhook_id": "test-webhook-001"
}
```

Send test request:

```bash
curl -X POST http://localhost:8000/api/v1/zendesk/webhook \
  -H "Content-Type: application/json" \
  -d @test_webhook.json
```

### Step 4: Check Processing Status

```bash
# Get status of email ID 1
curl http://localhost:8000/api/v1/zendesk/status/1
```

---

## 🚀 Deployment to Hetzner VPS

### Option 1: Docker Deployment (Recommended)

1. **Create production `.env`**:
```env
ENVIRONMENT=production
DATABASE_URL=postgresql://creditor_user:STRONG_PASSWORD@postgres:5432/creditor_matcher
# ... rest of production settings
```

2. **Create `docker-compose.prod.yml`**:
```yaml
version: '3.8'
services:
  app:
    build: .
    ports:
      - "8000:8000"
    env_file:
      - .env
    depends_on:
      - postgres

  postgres:
    image: postgres:15-alpine
    volumes:
      - postgres_data:/var/lib/postgresql/data
    env_file:
      - .env
```

3. **Deploy**:
```bash
# Build and start
docker-compose -f docker-compose.prod.yml up -d

# Run migrations
docker-compose exec app alembic upgrade head
```

### Option 2: Systemd Service

1. **Create service file** `/etc/systemd/system/creditor-matcher.service`:
```ini
[Unit]
Description=Creditor Email Matcher
After=network.target postgresql.service

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/creditor-matcher
Environment="PATH=/opt/creditor-matcher/venv/bin"
ExecStart=/opt/creditor-matcher/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

2. **Enable and start**:
```bash
sudo systemctl enable creditor-matcher
sudo systemctl start creditor-matcher
```

### Nginx Reverse Proxy

```nginx
server {
    listen 80;
    server_name creditor-matcher.ra-scuric.de;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## 🔍 Troubleshooting

### Database Connection Issues

```bash
# Check if PostgreSQL is running
docker-compose ps

# Check logs
docker-compose logs postgres

# Restart database
docker-compose restart postgres
```

### Migration Issues

```bash
# Check current migration
alembic current

# View migration history
alembic history

# Rollback one migration
alembic downgrade -1

# Upgrade to latest
alembic upgrade head
```

### OpenAI API Issues

- Verify API key is correct
- Check quota/billing: https://platform.openai.com/account/usage
- Test with simple request:
```bash
curl https://api.openai.com/v1/chat/completions \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}]}'
```

### Webhook Not Receiving Data

1. Check Zendesk webhook configuration
2. Verify webhook URL is publicly accessible
3. Check webhook secret matches
4. Review logs: `tail -f logs/app.log`

---

## 📊 Monitoring

### View Logs

```bash
# Application logs
tail -f logs/app.log

# Docker logs
docker-compose logs -f app
```

### Database Queries

```bash
# Connect to PostgreSQL
docker-compose exec postgres psql -U creditor_user -d creditor_matcher

# Example queries
SELECT COUNT(*) FROM incoming_emails;
SELECT COUNT(*) FROM creditor_inquiries;
SELECT match_status, COUNT(*) FROM incoming_emails GROUP BY match_status;
```

---

## 🎯 Next Steps

1. ✅ Configure Zendesk webhook to point to your API
2. ✅ Test with real creditor emails
3. ✅ Monitor matching accuracy
4. ✅ Adjust confidence thresholds if needed
5. ✅ Set up monitoring/alerting

---

## 📞 Support

For issues or questions:
- Check logs first
- Review this guide
- Contact your development team
