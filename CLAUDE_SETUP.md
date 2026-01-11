# 🤖 Using Claude for Entity Extraction

Your system now supports **Anthropic Claude** as the LLM provider for entity extraction!

## ✨ Why Claude?

- **Better at structured extraction** - More reliable JSON output
- **Longer context** - Can handle more complex emails
- **German language** - Excellent German language understanding
- **Cost effective** - Competitive pricing
- **Fast** - Quick response times

## 🔑 Get Your Claude API Key (2 minutes)

### Step 1: Sign Up
Go to: https://console.anthropic.com/

### Step 2: Create API Key
1. After signing up, go to **API Keys** in the dashboard
2. Click **Create Key**
3. Give it a name (e.g., "Creditor Matcher Dev")
4. Copy the key (starts with `sk-ant-...`)

### Step 3: Add Credits (Optional)
- New accounts get $5 free credit
- Add more at: https://console.anthropic.com/settings/billing

## ⚙️ Configure Your System

Edit your `.env` file:

```bash
nano .env
```

Add your Claude API key:

```env
# LLM Configuration
LLM_PROVIDER=claude

# Anthropic Claude
ANTHROPIC_API_KEY=sk-ant-your-actual-key-here
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
```

Save and the app will auto-reload!

## 🧪 Test It

Send a test email:

```bash
curl -X POST "http://localhost:8000/api/v1/zendesk/webhook" \
  -H "Content-Type: application/json" \
  -d '{
    "ticket_id": "test-claude-001",
    "subject": "Re: Anfrage Max Mustermann",
    "from_email": "info@sparkasse-bochum.de",
    "from_name": "Sparkasse Bochum",
    "body_text": "Sehr geehrte Damen und Herren,\n\nbezüglich Ihres Mandanten Herrn Max Mustermann teilen wir mit, dass die Forderung EUR 1.234,56 beträgt.\n\nAktenzeichen: AZ-2024-12345\n\nMit freundlichen Grüßen\nSparkasse Bochum\nKundenservice",
    "received_at": "2024-01-07T10:00:00Z",
    "webhook_id": "test-claude-001"
  }'
```

Check the result:

```bash
curl http://localhost:8000/api/v1/zendesk/status/2 | python3 -m json.tool
```

You should see extracted entities like:

```json
{
  "extracted_data": {
    "is_creditor_reply": true,
    "client_name": "Mustermann, Max",
    "creditor_name": "Sparkasse Bochum",
    "debt_amount": 1234.56,
    "reference_numbers": ["AZ-2024-12345"],
    "confidence": 0.95,
    "summary": "Sparkasse Bochum bestätigt Forderung von 1.234,56 EUR für Max Mustermann"
  }
}
```

## 🔄 Switch Between Claude and OpenAI

You can easily switch providers by changing the `.env` file:

### Use Claude (Recommended):
```env
LLM_PROVIDER=claude
ANTHROPIC_API_KEY=sk-ant-your-key
```

### Use OpenAI:
```env
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-your-key
```

## 💰 Pricing Comparison

### Claude 3.5 Sonnet (Current Default)
- **Input**: $3.00 per 1M tokens
- **Output**: $15.00 per 1M tokens
- **Average cost per email**: ~$0.003 (0.3 cents)

### Claude 3.5 Haiku (Budget Option)
- **Input**: $0.80 per 1M tokens
- **Output**: $4.00 per 1M tokens
- **Average cost per email**: ~$0.0008 (0.08 cents)

To use Haiku (cheaper, slightly less accurate):
```env
ANTHROPIC_MODEL=claude-3-5-haiku-20241022
```

### OpenAI GPT-4o (Alternative)
- **Input**: $2.50 per 1M tokens
- **Output**: $10.00 per 1M tokens
- **Average cost per email**: ~$0.002 (0.2 cents)

## 📊 Expected Performance

With **200-300 tokens** per cleaned email:

- **Processing time**: ~1-2 seconds
- **Extraction accuracy**: ~95%+
- **Cost per 1000 emails**: ~$3 (Claude Sonnet) or ~$0.80 (Claude Haiku)

## 🔍 Monitor Usage

Check your Claude usage at:
https://console.anthropic.com/settings/usage

## ⚠️ Troubleshooting

### "Anthropic API key not configured"
- Make sure you added `ANTHROPIC_API_KEY` to `.env`
- Restart the app or wait for auto-reload

### "429 Rate Limit Error"
- You've hit the rate limit
- Wait a minute or upgrade your plan
- Consider adding retry logic

### "Invalid API key"
- Check your key starts with `sk-ant-`
- Verify it's active in the Anthropic console
- Regenerate if needed

### Still getting empty extraction
- Check logs: `tail -f /tmp/fastapi.log`
- Verify LLM_PROVIDER is set to "claude"
- Test the API key directly via curl

## 📝 Custom Prompts

Want to adjust the extraction? Edit the prompts in:
```
app/services/entity_extractor_claude.py
```

Look for `_get_system_prompt()` and `_build_extraction_prompt()` methods.

## 🎯 Next Steps

1. ✅ Get Claude API key
2. ✅ Add to `.env`
3. ✅ Test extraction
4. ✅ Send real creditor email
5. ✅ Monitor accuracy

---

**You're all set! Claude will now handle entity extraction from creditor emails.** 🚀
