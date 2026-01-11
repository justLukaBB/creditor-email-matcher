#!/usr/bin/env bash
# Build script for Render

set -o errexit

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Run database migrations (if DATABASE_URL is set)
if [ -n "$DATABASE_URL" ]; then
  echo "Running database migrations..."
  alembic upgrade head
fi

echo "Build completed successfully!"
