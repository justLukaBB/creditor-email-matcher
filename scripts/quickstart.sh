#!/bin/bash
# Quick Start Script - Creditor Email Matching System

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}"
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║   Creditor Email Matching System - Quick Start                ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo -e "${NC}\n"

# Check if .env exists
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}⚠️  .env file not found${NC}"
    echo "Creating .env from .env.example..."
    cp .env.example .env
    echo -e "${GREEN}✅ .env created${NC}"
    echo ""
    echo -e "${RED}IMPORTANT: Edit .env and add your API credentials:${NC}"
    echo "  - OPENAI_API_KEY"
    echo "  - ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, ZENDESK_API_TOKEN"
    echo "  - WEBHOOK_SECRET"
    echo ""
    echo "Press Enter when ready to continue..."
    read
fi

# Check Python version
echo -e "${YELLOW}Checking Python version...${NC}"
python3 --version

# Check if venv exists
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    python3 -m venv venv
    echo -e "${GREEN}✅ Virtual environment created${NC}"
fi

# Activate venv and install dependencies
echo -e "${YELLOW}Installing dependencies...${NC}"
source venv/bin/activate
pip install --upgrade pip > /dev/null 2>&1
pip install -r requirements.txt > /dev/null 2>&1
echo -e "${GREEN}✅ Dependencies installed${NC}"

# Check Docker
echo -e "${YELLOW}Checking Docker...${NC}"
if ! docker info > /dev/null 2>&1; then
    echo -e "${RED}❌ Docker is not running${NC}"
    echo "Please start Docker and run this script again."
    exit 1
fi
echo -e "${GREEN}✅ Docker is running${NC}"

# Start PostgreSQL
echo -e "${YELLOW}Starting PostgreSQL...${NC}"
docker-compose up -d postgres
sleep 3
echo -e "${GREEN}✅ PostgreSQL started${NC}"

# Wait for database to be ready
echo -e "${YELLOW}Waiting for database to be ready...${NC}"
for i in {1..10}; do
    if docker-compose exec -T postgres pg_isready -U creditor_user -d creditor_matcher > /dev/null 2>&1; then
        echo -e "${GREEN}✅ Database is ready${NC}"
        break
    fi
    sleep 1
done

# Run migrations
echo -e "${YELLOW}Running database migrations...${NC}"
if alembic current > /dev/null 2>&1; then
    echo "Database already initialized"
else
    echo "Creating initial migration..."
    alembic revision --autogenerate -m "initial schema"
fi
alembic upgrade head
echo -e "${GREEN}✅ Migrations complete${NC}"

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════════╗"
echo -e "║                    🎉 Setup Complete! 🎉                       ║"
echo -e "╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${BLUE}Next steps:${NC}"
echo ""
echo "1. Start the application:"
echo -e "   ${YELLOW}source venv/bin/activate && uvicorn app.main:app --reload${NC}"
echo ""
echo "2. Open your browser:"
echo -e "   ${YELLOW}http://localhost:8000/docs${NC} (API documentation)"
echo -e "   ${YELLOW}http://localhost:8000/health${NC} (Health check)"
echo ""
echo "3. Test the webhook endpoint:"
echo -e "   ${YELLOW}curl http://localhost:8000/api/v1/zendesk/webhook${NC}"
echo ""
echo "4. See PROJECT_SUMMARY.md for detailed next steps"
echo ""
echo -e "${GREEN}Happy coding! 🚀${NC}"
