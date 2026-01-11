-- Database Initialization Script
-- This runs automatically when the PostgreSQL container is first created

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- For fuzzy text matching

-- Create indexes for better performance (will be added via Alembic migrations)
-- This is just a placeholder for any custom initialization
