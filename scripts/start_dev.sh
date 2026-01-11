#!/bin/bash
# Start Development Environment with ngrok

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

# Docker path - use full path if docker not in PATH
if ! command -v docker &> /dev/null; then
    export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
    DOCKER="/Applications/Docker.app/Contents/Resources/bin/docker"
else
    DOCKER="docker"
fi

echo -e "${BLUE}"
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║          Starting Local Development with ngrok                 ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo -e "${NC}\n"

# Check if ngrok is installed
if ! command -v ngrok &> /dev/null; then
    echo -e "${RED}❌ ngrok is not installed${NC}"
    echo ""
    echo "Install ngrok:"
    echo "  macOS:  brew install ngrok/ngrok/ngrok"
    echo "  Other:  https://ngrok.com/download"
    echo ""
    exit 1
fi

# Check if .env exists
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}⚠️  .env file not found${NC}"
    echo "Creating .env from .env.example..."
    cp .env.example .env
    echo -e "${GREEN}✅ .env created${NC}"
    echo ""
    echo -e "${RED}⚠️  IMPORTANT: You need to add your API credentials to .env${NC}"
    echo ""
    echo "For local testing, you can start with minimal config:"
    echo "  - DATABASE_URL is already set for Docker"
    echo "  - WEBHOOK_SECRET can be anything for now"
    echo ""
    echo "Optional (add later for full functionality):"
    echo "  - OPENAI_API_KEY (for entity extraction)"
    echo "  - ZENDESK credentials (for auto-assignment)"
    echo ""
    read -p "Press Enter to continue..."
fi

# Start database
echo -e "${YELLOW}Starting PostgreSQL...${NC}"
$DOCKER compose up -d postgres
sleep 2

# Wait for database
echo -e "${YELLOW}Waiting for database...${NC}"
for i in {1..10}; do
    if $DOCKER compose exec -T postgres pg_isready -U creditor_user -d creditor_matcher > /dev/null 2>&1; then
        echo -e "${GREEN}✅ Database ready${NC}"
        break
    fi
    sleep 1
done

# Run migrations
echo -e "${YELLOW}Checking database migrations...${NC}"
source venv/bin/activate
if ! alembic current > /dev/null 2>&1; then
    echo "Creating initial migration..."
    alembic revision --autogenerate -m "initial schema"
fi
alembic upgrade head > /dev/null 2>&1
echo -e "${GREEN}✅ Database migrations complete${NC}"

# Start the application in background
echo -e "${YELLOW}Starting FastAPI application...${NC}"
source venv/bin/activate
uvicorn app.main:app --reload --port 8000 > /dev/null 2>&1 &
APP_PID=$!
sleep 2

# Check if app started
if ps -p $APP_PID > /dev/null; then
    echo -e "${GREEN}✅ FastAPI running on http://localhost:8000${NC}"
else
    echo -e "${RED}❌ Failed to start FastAPI${NC}"
    exit 1
fi

# Start ngrok
echo -e "${YELLOW}Starting ngrok tunnel...${NC}"
ngrok http 8000 > /dev/null 2>&1 &
NGROK_PID=$!
sleep 3

# Get ngrok URL
NGROK_URL=$(curl -s http://localhost:4040/api/tunnels | grep -o '"public_url":"https://[^"]*' | grep -o 'https://[^"]*' | head -n 1)

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════════╗"
echo -e "║                    ✅ Development Environment Ready!           ║"
echo -e "╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${BLUE}🌍 Your Public Webhook URL:${NC}"
echo -e "${GREEN}   ${NGROK_URL}/api/v1/zendesk/webhook${NC}"
echo ""
echo -e "${BLUE}📊 Monitoring:${NC}"
echo "   FastAPI Docs:    http://localhost:8000/docs"
echo "   Health Check:    http://localhost:8000/health"
echo "   ngrok Inspector: http://localhost:4040"
echo ""
echo -e "${BLUE}🔍 Testing:${NC}"
echo "   Test webhook:"
echo -e "   ${YELLOW}curl -X POST \"${NGROK_URL}/api/v1/zendesk/webhook\" \\
     -H \"Content-Type: application/json\" \\
     -d '{
       \"ticket_id\": \"12345\",
       \"subject\": \"Test Email\",
       \"from_email\": \"test@example.com\",
       \"body_text\": \"Test content\",
       \"received_at\": \"2024-01-07T10:00:00Z\"
     }'${NC}"
echo ""
echo -e "${BLUE}📝 Configure Zendesk:${NC}"
echo "   1. Go to Zendesk Admin → Webhooks"
echo "   2. Create webhook with URL above"
echo "   3. Create trigger for gläubiger@ra-scuric.de emails"
echo ""
echo -e "${RED}⚠️  To stop all services: Press Ctrl+C${NC}"
echo ""

# Cleanup function
cleanup() {
    echo ""
    echo -e "${YELLOW}Stopping services...${NC}"
    kill $APP_PID 2>/dev/null || true
    kill $NGROK_PID 2>/dev/null || true
    echo -e "${GREEN}✅ Services stopped${NC}"
    exit 0
}

trap cleanup SIGINT SIGTERM

# Keep script running and show logs
echo -e "${BLUE}📋 Application Logs:${NC}"
echo "────────────────────────────────────────────────────────────────"
tail -f /dev/null & wait
