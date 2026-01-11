# 🚀 Quick Start - Get Running in 5 Minutes

## Current Status
✅ Dependencies installed
✅ .env file created
✅ ngrok installed

## Start Everything (3 Simple Steps)

### Option 1: Automated (Recommended)
```bash
./scripts/start_dev.sh
```

This will:
- Start PostgreSQL in Docker
- Run database migrations
- Start FastAPI on port 8000
- Start ngrok tunnel
- Show you the public webhook URL

### Option 2: Manual (3 Terminals)

#### Terminal 1: Database
```bash
docker-compose up postgres
```

#### Terminal 2: FastAPI Application
```bash
source venv/bin/activate
uvicorn app.main:app --reload
```

#### Terminal 3: ngrok Tunnel
```bash
ngrok http 8000
```

## Your Webhook URL

After starting ngrok, you'll see something like:
```
Forwarding   https://abc123xyz.ngrok-free.app -> http://localhost:8000
```

Your webhook URL is: `https://abc123xyz.ngrok-free.app/api/v1/zendesk/webhook`

## Test It Works

### 1. Check Health
```bash
curl http://localhost:8000/health
```

### 2. Test Webhook Locally
```bash
curl -X POST "http://localhost:8000/api/v1/zendesk/webhook" \
  -H "Content-Type: application/json" \
  -d '{
    "ticket_id": "12345",
    "subject": "Test Email",
    "from_email": "test@sparkasse.de",
    "from_name": "Test Bank",
    "body_text": "Test content for Max Mustermann. Amount: 1234.56 EUR. Reference: AZ-12345",
    "received_at": "2024-01-07T10:00:00Z",
    "webhook_id": "test-001"
  }'
```

Expected response:
```json
{
  "status": "accepted",
  "message": "Email queued for processing",
  "email_id": 1
}
```

### 3. Check Processing Status
```bash
curl http://localhost:8000/api/v1/zendesk/status/1
```

## Monitor Requests

- **FastAPI Docs**: http://localhost:8000/docs
- **ngrok Inspector**: http://localhost:4040
- **Application Logs**: Check Terminal 2

## Configure Zendesk

1. Copy your ngrok URL from Terminal 3
2. Go to Zendesk Admin → Webhooks → Create webhook
3. URL: `https://YOUR-NGROK-URL.ngrok-free.app/api/v1/zendesk/webhook`
4. Method: POST
5. Format: JSON

## What Works Now vs Later

### ✅ Working Now (No API Keys Needed)
- Webhook receives emails from Zendesk
- Email is stored in database
- Email parser cleans the content
- You can see all data via API docs

### 📝 Add Later (When You Have API Keys)
- **OpenAI Key**: Entity extraction (client name, amount, etc.)
- **Zendesk Credentials**: Auto-assignment to side conversations

## Next Steps

1. **Test webhook** with real Zendesk email
2. **Add OpenAI key** to `.env` for entity extraction
3. **Populate database** with existing inquiries
4. **Add Zendesk credentials** for auto-assignment

## Troubleshooting

### Can't connect to database
```bash
docker-compose up -d postgres
docker-compose exec postgres pg_isready -U creditor_user
```

### Port 8000 already in use
```bash
lsof -i :8000
kill -9 <PID>
```

### ngrok session expired
Just restart ngrok - it will give you a new URL:
```bash
ngrok http 8000
```

## Stop Everything

Press `Ctrl+C` in each terminal or:
```bash
docker-compose down
pkill -f uvicorn
pkill ngrok
```
