#!/bin/bash
# Database Management Script

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Docker path - use full path if docker not in PATH
if ! command -v docker &> /dev/null; then
    DOCKER="/Applications/Docker.app/Contents/Resources/bin/docker"
else
    DOCKER="docker"
fi

echo -e "${GREEN}=== Creditor Matcher Database Manager ===${NC}\n"

# Function to check if Docker is running
check_docker() {
    if ! $DOCKER info > /dev/null 2>&1; then
        echo -e "${RED}Error: Docker is not running${NC}"
        exit 1
    fi
}

# Function to start database
start_db() {
    echo -e "${YELLOW}Starting PostgreSQL database...${NC}"
    $DOCKER compose up -d postgres
    echo -e "${GREEN}Waiting for database to be ready...${NC}"
    sleep 5
    $DOCKER compose exec postgres pg_isready -U creditor_user -d creditor_matcher
    echo -e "${GREEN}Database is ready!${NC}"
}

# Function to stop database
stop_db() {
    echo -e "${YELLOW}Stopping database...${NC}"
    $DOCKER compose down
    echo -e "${GREEN}Database stopped${NC}"
}

# Function to create migration
create_migration() {
    local message=$1
    if [ -z "$message" ]; then
        echo -e "${RED}Error: Migration message required${NC}"
        echo "Usage: $0 migrate 'your migration message'"
        exit 1
    fi
    echo -e "${YELLOW}Creating migration: $message${NC}"
    alembic revision --autogenerate -m "$message"
    echo -e "${GREEN}Migration created${NC}"
}

# Function to run migrations
run_migrations() {
    echo -e "${YELLOW}Running database migrations...${NC}"
    alembic upgrade head
    echo -e "${GREEN}Migrations complete${NC}"
}

# Function to rollback migration
rollback_migration() {
    echo -e "${YELLOW}Rolling back last migration...${NC}"
    alembic downgrade -1
    echo -e "${GREEN}Rollback complete${NC}"
}

# Function to reset database
reset_db() {
    echo -e "${RED}WARNING: This will delete all data!${NC}"
    read -p "Are you sure? (yes/no): " confirm
    if [ "$confirm" = "yes" ]; then
        echo -e "${YELLOW}Resetting database...${NC}"
        $DOCKER compose down -v
        start_db
        run_migrations
        echo -e "${GREEN}Database reset complete${NC}"
    else
        echo -e "${YELLOW}Reset cancelled${NC}"
    fi
}

# Function to show status
show_status() {
    echo -e "${YELLOW}Database Status:${NC}"
    alembic current
    echo ""
    echo -e "${YELLOW}Migration History:${NC}"
    alembic history
}

# Main command handling
check_docker

case "$1" in
    start)
        start_db
        ;;
    stop)
        stop_db
        ;;
    migrate)
        create_migration "$2"
        ;;
    upgrade)
        run_migrations
        ;;
    downgrade)
        rollback_migration
        ;;
    reset)
        reset_db
        ;;
    status)
        show_status
        ;;
    init)
        start_db
        create_migration "initial database schema"
        run_migrations
        echo -e "${GREEN}Database initialized!${NC}"
        ;;
    *)
        echo "Usage: $0 {start|stop|migrate|upgrade|downgrade|reset|status|init}"
        echo ""
        echo "Commands:"
        echo "  start      - Start PostgreSQL container"
        echo "  stop       - Stop PostgreSQL container"
        echo "  migrate    - Create new migration (usage: migrate 'message')"
        echo "  upgrade    - Run pending migrations"
        echo "  downgrade  - Rollback last migration"
        echo "  reset      - Reset database (WARNING: deletes all data)"
        echo "  status     - Show current migration status"
        echo "  init       - Initialize database (start + create initial migration + upgrade)"
        exit 1
        ;;
esac
