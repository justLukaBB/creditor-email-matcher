# Render Deployment Guide

## Quick Deploy to Render

### Step 1: Push to GitHub
1. Create a new GitHub repository at https://github.com/new
2. Name it: `creditor-email-matcher` (or any name you prefer)
3. Push your code:
   ```bash
   cd "/Users/luka.s/Anayse Creditor Answers"
   git remote add origin https://github.com/YOUR_USERNAME/creditor-email-matcher.git
   git branch -M main
   git push -u origin main
   ```

### Step 2: Deploy on Render
1. Go to https://dashboard.render.com/
2. Click **"New +"** → **"Web Service"**
3. Connect your GitHub account if not already connected
4. Select your `creditor-email-matcher` repository
5. Configure:
   - **Name**: `creditor-email-matcher`
   - **Region**: `Frankfurt` (closest to you)
   - **Branch**: `main`
   - **Root Directory**: (leave blank)
   - **Runtime**: `Python 3`
   - **Build Command**: `./build.sh`
   - **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Plan**: `Free` (for testing) or `Starter` ($7/month for production)

6. Click **"Create Web Service"**

### Step 3: Add Environment Variables
In Render dashboard → Environment tab, add these variables:

#### Required Variables:
```bash
# MongoDB Atlas
MONGODB_URL=your_mongodb_connection_string_here
MONGODB_DATABASE=test

# Claude AI
ANTHROPIC_API_KEY=your_anthropic_api_key_here
ANTHROPIC_MODEL=claude-3-haiku-20240307

# LLM Provider
LLM_PROVIDER=claude

# Python Version
PYTHON_VERSION=3.11.0
```

#### Optional Variables (PostgreSQL logging):
```bash
# If you want PostgreSQL for logging (optional)
# Add a PostgreSQL database in Render, then it will provide DATABASE_URL automatically
DATABASE_URL=<automatically provided by Render if you add PostgreSQL>
```

#### Optional Variables (future features):
```bash
# Zendesk Integration (not needed for current workflow)
# ZENDESK_SUBDOMAIN=scuric
# ZENDESK_EMAIL=your-email@example.com
# ZENDESK_API_TOKEN=your_token

# SMTP Email Notifications
# SMTP_HOST=smtp.gmail.com
# SMTP_PORT=587
# SMTP_USER=your-email@gmail.com
# SMTP_PASSWORD=your-app-password
# SMTP_FROM_EMAIL=your-email@gmail.com

# Webhook Security
# WEBHOOK_SECRET=your-secret-key-for-zendesk-verification
```

### Step 4: Optional - Add PostgreSQL Database
If you want to track incoming emails in PostgreSQL:
1. In Render dashboard → **"New +"** → **"PostgreSQL"**
2. Name: `creditor-matcher-db`
3. Plan: **Free** (for testing)
4. Click **"Create Database"**
5. Copy the **Internal Database URL**
6. Add it as `DATABASE_URL` environment variable in your web service
7. The app will auto-run migrations on startup via `build.sh`

### Step 5: Get Your Deployment URL
- Render will provide a URL like: `https://creditor-email-matcher.onrender.com`
- **Webhook URL**: `https://creditor-email-matcher.onrender.com/api/v1/zendesk/webhook`

### Step 6: Update Zendesk Webhook
1. Go to Zendesk → Admin Center → Objects and rules → Business rules → Triggers
2. Find your creditor email trigger
3. Update webhook URL to your Render URL:
   ```
   https://creditor-email-matcher.onrender.com/api/v1/zendesk/webhook
   ```

---

## Post-Deployment Testing

### Test Health Endpoint
```bash
curl https://creditor-email-matcher.onrender.com/
```

Expected response:
```json
{
  "status": "healthy",
  "service": "Creditor Email Matcher",
  "version": "1.0.0",
  "environment": "production"
}
```

### Test Webhook Endpoint
```bash
curl -X POST https://creditor-email-matcher.onrender.com/api/v1/zendesk/webhook \
  -H "Content-Type: application/json" \
  -d @test_webhook.json
```

### Monitor Logs
- Go to Render dashboard → Your service → **Logs** tab
- Watch for incoming requests and processing

---

## Important Notes

### Free Tier Limitations
- **Spin down after 15 minutes of inactivity**
- **Cold start time**: ~30-60 seconds (first request after spin down)
- **RAM**: 512MB
- **Best for**: Testing and low-traffic use

### Production Recommendations
- **Upgrade to Starter plan** ($7/month) for:
  - No spin down (always active)
  - More RAM (512MB)
  - Better performance
- **Monitor**: Set up notifications in Render for deployment failures

### Handling Cold Starts
If using Free tier and Zendesk webhooks timeout:
1. Consider upgrading to Starter plan (no spin down)
2. Or set up a cron job to ping your service every 10 minutes:
   ```bash
   # Use a service like cron-job.org
   curl https://creditor-email-matcher.onrender.com/
   ```

---

## Troubleshooting

### Build Fails
- Check Render build logs
- Verify `requirements.txt` has all dependencies
- Check `build.sh` is executable: `chmod +x build.sh`

### App Crashes on Start
- Check Render logs (dashboard → Logs tab)
- Verify all environment variables are set correctly
- Check MongoDB connection string is correct

### Webhook Timeouts
- **Free tier issue**: App spins down after 15 minutes
- **Solution**: Upgrade to Starter plan ($7/month)
- **Or**: Use a keep-alive service to ping every 10 minutes

### MongoDB Connection Issues
- Verify MongoDB Atlas allows all IPs (0.0.0.0/0) or add Render's IPs
- Check connection string includes `?retryWrites=true&w=majority`
- Test with: `pymongo` connection in logs

---

## Cost Breakdown

### Free Tier (Good for Testing)
- **Web Service**: Free
- **PostgreSQL**: Free (1GB storage)
- **Limitations**: Spins down after inactivity

### Production Setup ($7-14/month)
- **Web Service Starter**: $7/month (no spin down, 512MB RAM)
- **PostgreSQL Starter**: $7/month (optional, 10GB storage)
- **Total**: $7-14/month depending on PostgreSQL need

---

## Updating Your Deployment

When you make code changes:

```bash
cd "/Users/luka.s/Anayse Creditor Answers"

# Make your changes, then:
git add .
git commit -m "Update: description of changes"
git push origin main
```

Render will automatically detect the push and redeploy! 🚀

---

## Security Checklist

- [x] `.env` not committed to git
- [ ] Enable webhook signature verification (set WEBHOOK_SECRET)
- [ ] Add rate limiting in production
- [ ] Monitor logs for suspicious activity
- [ ] Rotate API keys regularly
- [ ] Set up Render notifications for errors

---

## Next Steps

1. ✅ Push code to GitHub
2. ✅ Deploy on Render
3. ✅ Configure environment variables
4. ✅ Get deployment URL
5. ✅ Update Zendesk webhook
6. ✅ Test with real email
7. 🔧 Monitor logs
8. 📈 Upgrade to Starter if needed (no spin down)
