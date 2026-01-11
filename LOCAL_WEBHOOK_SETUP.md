# Local Webhook Development Setup with ngrok

## Why ngrok?

ngrok creates a secure tunnel from a public URL to your localhost, allowing Zendesk to send webhooks to your local development machine.

## Step 1: Install ngrok

### Option A: Homebrew (macOS - Recommended)
```bash
brew install ngrok/ngrok/ngrok
```

### Option B: Direct Download
1. Go to https://ngrok.com/download
2. Download for macOS
3. Unzip and move to your PATH:
```bash
unzip ~/Downloads/ngrok-*.zip -d /usr/local/bin
```

### Option C: npm
```bash
npm install -g ngrok
```

## Step 2: Sign Up & Get Auth Token (Optional but recommended)

1. Create free account: https://dashboard.ngrok.com/signup
2. Get your auth token: https://dashboard.ngrok.com/get-started/your-authtoken
3. Configure ngrok:
```bash
ngrok config add-authtoken YOUR_AUTH_TOKEN
```

**Benefits of auth token:**
- Longer session times
- Custom subdomains (paid plans)
- More concurrent tunnels

## Step 3: Start Your Application

In Terminal 1:
```bash
source venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

## Step 4: Start ngrok Tunnel

In Terminal 2:
```bash
ngrok http 8000
```

You'll see output like:
```
ngrok

Session Status                online
Account                       your-email@example.com
Version                       3.x.x
Region                        Europe (eu)
Latency                       -
Web Interface                 http://127.0.0.1:4040
Forwarding                    https://abc123xyz.ngrok-free.app -> http://localhost:8000

Connections                   ttl     opn     rt1     rt5     p50     p90
                              0       0       0.00    0.00    0.00    0.00
```

**Your public webhook URL** is: `https://abc123xyz.ngrok-free.app`

## Step 5: Configure Zendesk Webhook

### In Zendesk Admin Panel:

1. Go to **Admin Center** → **Apps and integrations** → **Webhooks**

2. Click **Create webhook**

3. Configure:
   - **Name**: Creditor Email Webhook (Local Dev)
   - **Endpoint URL**: `https://YOUR-NGROK-URL.ngrok-free.app/api/v1/zendesk/webhook`
   - **Request method**: POST
   - **Request format**: JSON

4. **Authentication**: None (for now - we'll add webhook secret later)

5. **Test webhook** with sample payload

### Create Trigger for Creditor Emails

1. Go to **Admin Center** → **Objects and rules** → **Business rules** → **Triggers**

2. Click **Add trigger**

3. Configure:
   - **Name**: Forward Creditor Emails to Matcher
   - **Conditions**:
     - Ticket | Is | Created
     - Ticket | Recipient | Contains | gläubiger@ra-scuric.de
   - **Actions**:
     - Notifications | Notify webhook | Creditor Email Webhook (Local Dev)

## Step 6: Monitor Requests

### ngrok Web Interface
Visit http://127.0.0.1:4040 to see:
- All incoming requests
- Request/response details
- Replay requests
- Inspect payloads

### Your Application Logs
Watch your FastAPI logs in Terminal 1 to see webhook processing

## Step 7: Test the Webhook

### Option A: Send Test Email
Send an email to your Zendesk (simulating a creditor reply)

### Option B: Manual Test with curl
```bash
curl -X POST "https://YOUR-NGROK-URL.ngrok-free.app/api/v1/zendesk/webhook" \
  -H "Content-Type: application/json" \
  -d '{
    "ticket_id": "12345",
    "subject": "Re: Anfrage Max Mustermann",
    "from_email": "info@sparkasse-bochum.de",
    "from_name": "Sparkasse Bochum",
    "body_text": "Sehr geehrte Damen und Herren,\n\nbezüglich Herrn Max Mustermann können wir Ihnen mitteilen, dass die Forderung 1.234,56 EUR beträgt.\n\nAktenzeichen: AZ-12345\n\nMit freundlichen Grüßen",
    "received_at": "2024-01-07T10:30:00Z",
    "webhook_id": "test-001"
  }'
```

### Option C: Use ngrok Inspector
1. Go to http://127.0.0.1:4040
2. Click **Replay** on any request to resend it

## Troubleshooting

### ngrok tunnel not starting
```bash
# Check if port 8000 is already in use
lsof -i :8000

# Kill existing process if needed
kill -9 PID
```

### Webhook returns 404
- Check your ngrok URL includes the full path: `/api/v1/zendesk/webhook`
- Verify your FastAPI app is running

### Webhook returns 500
- Check your application logs
- Ensure database is running: `docker-compose ps`
- Check `.env` is configured

### ngrok session expires
Free accounts have 2-hour sessions. Just restart ngrok:
```bash
ngrok http 8000
```

**Tip**: Get the new URL and update Zendesk webhook configuration

## Production Setup (Later)

For production, you'll:
1. Deploy to Hetzner VPS with static domain
2. Use real domain: `https://creditor-matcher.ra-scuric.de`
3. Enable webhook signature validation
4. Use HTTPS with Let's Encrypt

But for development, ngrok is perfect! 🎉
