# Railway Deployment Guide

## Prerequisites
- Railway account (sign up at https://railway.app with GitHub)
- Railway CLI installed (optional but recommended)

## Method 1: Deploy via Railway Dashboard (Easiest)

### Step 1: Create Railway Project
1. Go to https://railway.app/new
2. Click "Deploy from GitHub repo"
3. Connect your GitHub account if not already connected
4. Push this repository to GitHub first:
   ```bash
   # Create a new repo on GitHub, then:
   git remote add origin https://github.com/YOUR_USERNAME/creditor-matcher.git
   git push -u origin main
   ```
5. Select your repository from Railway
6. Railway will auto-detect the Python app and deploy

### Step 2: Configure Environment Variables
In Railway dashboard → Variables tab, add:

```bash
# Database (PostgreSQL - use Railway's built-in PostgreSQL)
DATABASE_URL=postgresql://user:pass@host:port/dbname  # Railway will provide this

# MongoDB Atlas
MONGODB_URL=your_mongodb_connection_string_here
MONGODB_DATABASE=test

# Claude AI
ANTHROPIC_API_KEY=your_anthropic_api_key_here
ANTHROPIC_MODEL=claude-3-haiku-20240307

# LLM Provider
LLM_PROVIDER=claude

# Optional: Zendesk (for future features)
# ZENDESK_SUBDOMAIN=scuric
# ZENDESK_EMAIL=your-email@example.com
# ZENDESK_API_TOKEN=your_token

# Optional: SMTP Email Notifications
# SMTP_HOST=smtp.gmail.com
# SMTP_PORT=587
# SMTP_USER=your-email@gmail.com
# SMTP_PASSWORD=your-app-password
# SMTP_FROM_EMAIL=your-email@gmail.com

# Optional: Webhook Security
# WEBHOOK_SECRET=your-secret-key
```

### Step 3: Add PostgreSQL Database
1. In Railway project → New → Database → PostgreSQL
2. Railway will automatically provide DATABASE_URL
3. Database will be created and migrations will run on deployment

### Step 4: Get Your Deployment URL
- Railway will provide a URL like: `https://your-app.railway.app`
- Use this for your Zendesk webhook: `https://your-app.railway.app/api/v1/zendesk/webhook`

---

## Method 2: Deploy via Railway CLI (Advanced)

### Install Railway CLI
```bash
# macOS
brew install railway

# Or use npm
npm i -g @railway/cli
```

### Deploy Commands
```bash
cd "/Users/luka.s/Anayse Creditor Answers"

# Login to Railway
railway login

# Create new project
railway init

# Add PostgreSQL
railway add --plugin postgresql

# Set environment variables (copy from .env file)
railway variables set MONGODB_URL="mongodb+srv://..."
railway variables set ANTHROPIC_API_KEY="sk-ant-..."
railway variables set ANTHROPIC_MODEL="claude-3-haiku-20240307"
railway variables set LLM_PROVIDER="claude"

# Deploy
railway up

# View logs
railway logs

# Open in browser
railway open
```

---

## Post-Deployment Steps

### 1. Run Database Migrations
Railway should run migrations automatically via Procfile, but if needed:
```bash
railway run alembic upgrade head
```

### 2. Test Your Deployment
```bash
# Get your Railway URL
railway status

# Test health endpoint
curl https://your-app.railway.app/

# Expected response:
# {"status":"healthy","service":"Creditor Email Matcher",...}
```

### 3. Update Zendesk Webhook
- Go to Zendesk → Admin → Triggers
- Update webhook URL to: `https://your-app.railway.app/api/v1/zendesk/webhook`

### 4. Monitor Logs
```bash
# View real-time logs
railway logs --follow
```

---

## Troubleshooting

### Build Fails
- Check Railway build logs
- Ensure `requirements.txt` has all dependencies
- Check Python version in `runtime.txt`

### App Crashes on Start
- Check logs: `railway logs`
- Verify all required environment variables are set
- Ensure DATABASE_URL is provided by Railway PostgreSQL

### Database Connection Issues
- Verify MongoDB URL is correct
- Check PostgreSQL is added to project
- Run migrations: `railway run alembic upgrade head`

### High Memory Usage
- Railway free tier: 512MB RAM
- Consider upgrading plan if needed
- Monitor with: `railway status`

---

## Cost Estimate
- **Free Tier**: $5/month credit (enough for development/testing)
- **Hobby Plan**: $5/month (500 hours, should be sufficient)
- **Pro Plan**: $20/month (unlimited, if scaling needed)

---

## Security Notes
- Never commit `.env` file to git (already in .gitignore)
- Use Railway's secret management for sensitive data
- Enable webhook signature verification in production
- Consider adding rate limiting for production use

---

## Next Steps After Deployment
1. Get deployment URL from Railway
2. Configure Zendesk webhook with new URL
3. Test with a real creditor email
4. Monitor logs for any issues
5. Set up SMTP for email notifications (optional)
