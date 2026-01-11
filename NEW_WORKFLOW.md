# New Matching Workflow

## Overview

When a creditor email is received and matched with HIGH confidence (≥80%), the system now:

1. ✅ **Updates MongoDB** - Updates the creditor's debt amount in your MongoDB database
2. ✅ **Sends Email Notification** - Sends notification to `glaubiger@scuric.zendesk.com`
3. ❌ **NO Zendesk Operations** - Does NOT merge tickets or manipulate side conversations

## Workflow Details

### High Confidence Match (≥80%)

```
Creditor Email Received
         ↓
   Claude Extraction
         ↓
   Fuzzy Matching (≥80%)
         ↓
┌────────────────────────┐
│ 1. Update MongoDB      │
│    - Update debt amount│
│    - Mark as replied   │
└────────────────────────┘
         ↓
┌────────────────────────┐
│ 2. Send Email          │
│    To: glaubiger@      │
│    scuric.zendesk.com  │
│                        │
│    Contains:           │
│    - Client name       │
│    - Creditor name     │
│    - Old/New amount    │
│    - Side Conv ID      │
│    - Ticket ID         │
│    - Confidence score  │
└────────────────────────┘
         ↓
┌────────────────────────┐
│ 3. Update PostgreSQL   │
│    - Mark inquiry as   │
│      replied           │
└────────────────────────┘
```

### Medium Confidence Match (60-79%)

- **Status**: `needs_review`
- **Action**: Logged only, no automation
- **Manual Review Required**: Yes

### Low Confidence Match (<60%)

- **Status**: `manual_queue`
- **Action**: Logged only, no automation
- **Manual Review Required**: Yes

### No Match Found

- **Status**: `no_match`
- **Action**: Logged only
- **Manual Review Required**: Yes

## MongoDB Update

### What Gets Updated

When a match is found, the system updates the creditor in `final_creditor_list[]`:

```javascript
{
  // Updated fields
  current_debt_amount: 899.00,              // New amount from creditor
  creditor_response_amount: 899.00,          // Same as above
  amount_source: "creditor_response",        // Source indicator
  response_received_at: ISODate("2024-01-08T12:00:00Z"),
  contact_status: "response_received",       // Status update
  creditor_response_text: "Summary from Claude..."  // Optional
}
```

### MongoDB Query Used

```javascript
db.clients.update_one(
  {
    'final_creditor_list': {
      '$elemMatch': {
        'main_zendesk_ticket_id': "22549",
        'side_conversation_id': "sc_abc123",
        'sender_email': "creditor@example.com"
      }
    }
  },
  {
    '$set': {
      'final_creditor_list.$.current_debt_amount': 899.00,
      'final_creditor_list.$.creditor_response_amount': 899.00,
      'final_creditor_list.$.amount_source': 'creditor_response',
      'final_creditor_list.$.response_received_at': new Date(),
      'final_creditor_list.$.contact_status': 'response_received'
    }
  }
)
```

## Email Notification

### Recipients

- **To**: `glaubiger@scuric.zendesk.com`

### Email Content

**Subject**: `Gläubiger-Antwort: {Client Name} - {Creditor Name}`

**Body** (both HTML and plain text):
- 👤 **Mandant**: Client name
- 🏦 **Gläubiger**: Creditor name and email
- 💰 **Forderungsbetrag**:
  - Previous amount (if known)
  - New amount from creditor response
  - Change amount (if different)
- 📋 **Zendesk Information**:
  - Main Ticket ID
  - Side Conversation ID
- 🔢 **Referenznummern**: All reference numbers extracted
- 🎯 **Matching-Qualität**: Confidence score (e.g., 100%)

### Email Example

```
Subject: Gläubiger-Antwort: Scuric, Luka - Test Gläubiger GmbH

MANDANT
  Name: Scuric, Luka

GLÄUBIGER
  Name: Test Gläubiger GmbH
  E-Mail: justlukax@gmail.com

FORDERUNGSBETRAG
  Vorheriger Betrag: Unbekannt
  Aktueller Betrag: 899.00 EUR

ZENDESK INFORMATIONEN
  Ticket-ID: 22549
  Side Conversation ID: sc_test_001

REFERENZNUMMERN
  232rf3

MATCHING
  Konfidenz: 100%

---
Der Forderungsbetrag wurde in der Datenbank aktualisiert.
```

## Configuration Required

### 1. MongoDB Connection

Update `.env` file:

```bash
MONGODB_URL=mongodb://your-host:27017
MONGODB_DATABASE=your_database_name
```

### 2. SMTP Configuration

Update `.env` file for email notifications:

```bash
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_FROM_EMAIL=your-email@gmail.com
```

**Note**: If SMTP is not configured, the system will skip email notifications but will still update MongoDB.

## Logs

### Successful Match & Update

```
INFO - Email 17 AUTO-MATCHED to inquiry 1 (confidence: 1.00)
INFO - ✅ MongoDB updated - Ticket: 22549, SC: sc_test_001, New amount: 899.0
INFO - ✅ Email notification sent - Client: Scuric, Luka, Creditor: Test Gläubiger GmbH, Amount: 899.0
INFO - ✅ MongoDB updated and notification sent - Client: Scuric, Luka, Amount: 899.0
INFO - Email 17 processing complete - Status: auto_matched
```

### MongoDB Not Available

```
WARNING - MongoDB not available - skipping debt amount update
```

### Email Notification Disabled

```
INFO - Email notifications disabled - skipping notification
```

## Testing the New Workflow

### 1. Ensure MongoDB is Running

```bash
# Check MongoDB connection
mongosh mongodb://localhost:27017/your_database_name
```

### 2. Configure .env

Update MongoDB URL and database name in `.env`

### 3. Send Test Email

Use the webhook endpoint or Zendesk to send a creditor reply.

### 4. Verify Results

**Check Logs:**
```bash
tail -f /tmp/fastapi.log | grep "Email 17"
```

**Check MongoDB:**
```javascript
db.clients.findOne(
  { 'final_creditor_list.main_zendesk_ticket_id': '22549' },
  { 'final_creditor_list.$': 1 }
)
```

**Check Email:**
Check inbox of `glaubiger@scuric.zendesk.com`

## API Endpoints

All existing endpoints remain unchanged:

- **Webhook**: `POST /api/v1/zendesk/webhook`
- **Email Status**: `GET /api/v1/zendesk/status/{email_id}`
- **Create Inquiry**: `POST /api/v1/inquiries/`
- **List Inquiries**: `GET /api/v1/inquiries/`

## Fallback Behavior

- **MongoDB unavailable**: System continues without MongoDB update, logs warning
- **SMTP not configured**: System continues without email notification, logs info
- **Both unavailable**: System still processes email and stores match results in PostgreSQL

## Benefits of New Workflow

1. ✅ **Simple & Clean** - No complex Zendesk ticket manipulation
2. ✅ **Data Integrity** - Updates source of truth (MongoDB) directly
3. ✅ **Notification** - Team gets notified via email
4. ✅ **Trackable** - Side conversation ID included for reference
5. ✅ **Non-intrusive** - Doesn't merge or move tickets around
6. ✅ **Flexible** - Can disable email/MongoDB independently

## Next Steps

1. ✅ Update MongoDB URL in `.env`
2. ✅ Configure SMTP credentials in `.env` (optional)
3. ✅ Test with real creditor email
4. ✅ Verify MongoDB update
5. ✅ Verify email received
