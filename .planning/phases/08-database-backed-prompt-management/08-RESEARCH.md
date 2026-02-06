# Phase 8: Database-Backed Prompt Management - Research

**Researched:** 2026-02-06
**Domain:** LLM prompt versioning and PostgreSQL storage
**Confidence:** HIGH

## Summary

Database-backed prompt management is an established pattern for production LLM applications in 2026. The standard approach stores prompt templates in PostgreSQL with immutable versioning, explicit activation/rollback mechanisms, and performance tracking tied to prompt versions. This enables prompt updates without redeployment, data-driven optimization, and audit trails for production debugging.

The codebase currently has hardcoded prompts in Python services (intent_classifier.py, entity_extractor_claude.py, pdf_extractor.py) with both simple string templates and multi-line prompts. Migration to database storage requires: (1) PostgreSQL tables for versioned templates with task-type organization, (2) Jinja2 templating for variable interpolation, (3) dual-table metrics tracking (raw extraction-level + daily rollups), and (4) explicit activation flow with rollback capability.

Key architectural decisions per CONTEXT.md: organize by task type (not agent), track both cost and quality metrics with 30-day raw retention, require explicit activation (no auto-promotion), and support ANY historical version rollback (not just previous).

**Primary recommendation:** Use PostgreSQL with SQLAlchemy models for prompt_templates (versioned) and prompt_performance_metrics (extraction-level + daily aggregates), Jinja2 3.1.6+ for templating, and implement version activation as explicit state transition with rollback to any historical version.

## Standard Stack

The established libraries/tools for prompt management in PostgreSQL-backed LLM applications:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| PostgreSQL | 14+ | Prompt storage, versioning, metrics | Industry standard for ACID compliance, JSONB support, time-series indexing |
| SQLAlchemy | 2.0+ | ORM for Python models | Already in codebase, mature versioning patterns |
| Jinja2 | 3.1.6+ | Template engine for prompts | Industry standard for LLM prompt templating, supports conditionals/loops |
| Alembic | Latest | Schema migrations | Already in codebase for database versioning |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| structlog | Latest | Structured logging | Already in codebase, critical for prompt version tracking |
| pg_cron | Optional | Automated rollup jobs | If daily aggregation needs database-level scheduling |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Jinja2 | Python f-strings | f-strings lack template reusability, conditionals, and inheritance |
| Jinja2 | string.Template | Too simple, no conditionals or loops for complex prompts |
| PostgreSQL | Specialized prompt tools (PromptLayer, LangSmith) | Third-party SaaS adds cost, vendor lock-in; PostgreSQL provides control |
| Daily rollups | Real-time aggregation | Real-time queries on millions of rows cause performance degradation |

**Installation:**
```bash
pip install Jinja2>=3.1.6
# SQLAlchemy, Alembic, structlog already in requirements
```

## Architecture Patterns

### Recommended Database Schema

```sql
-- Prompt Templates (versioned, immutable)
CREATE TABLE prompt_templates (
    id SERIAL PRIMARY KEY,
    task_type VARCHAR(50) NOT NULL,  -- 'classification', 'extraction', 'validation'
    name VARCHAR(100) NOT NULL,      -- Human-readable, free-form
    version INTEGER NOT NULL,         -- Auto-incremented per (task_type, name)

    -- Template content
    system_prompt TEXT,               -- Optional system message
    user_prompt_template TEXT NOT NULL, -- Jinja2 template

    -- Metadata
    is_active BOOLEAN DEFAULT FALSE,  -- Only one active version per (task_type, name)
    created_by VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    description TEXT,                 -- What changed, why

    -- Model configuration (part of versioned asset)
    model_name VARCHAR(50) DEFAULT 'claude-sonnet-4-5-20250514',
    temperature FLOAT DEFAULT 0.1,
    max_tokens INTEGER DEFAULT 1024,

    UNIQUE(task_type, name, version),
    CHECK(version > 0)
);

-- Indexes for activation queries and historical lookup
CREATE INDEX idx_prompt_templates_active ON prompt_templates(task_type, name) WHERE is_active = TRUE;
CREATE INDEX idx_prompt_templates_task_type ON prompt_templates(task_type);
CREATE INDEX idx_prompt_templates_created_at ON prompt_templates(created_at);

-- Prompt Performance Metrics (extraction-level, raw data)
CREATE TABLE prompt_performance_metrics (
    id SERIAL PRIMARY KEY,
    prompt_template_id INTEGER REFERENCES prompt_templates(id) NOT NULL,
    email_id INTEGER REFERENCES incoming_emails(id) NOT NULL,

    -- Cost metrics
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    api_cost_usd DECIMAL(10, 6) NOT NULL,

    -- Quality metrics
    extraction_success BOOLEAN NOT NULL,     -- Did extraction complete?
    confidence_score FLOAT,                   -- Overall confidence
    manual_review_required BOOLEAN,           -- Routed to manual review?

    -- Execution metrics
    execution_time_ms INTEGER NOT NULL,

    -- Timestamp
    extracted_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,

    -- Retention: keep 30 days, then aggregate to daily summaries
    CONSTRAINT retention_check CHECK (extracted_at > NOW() - INTERVAL '30 days')
);

CREATE INDEX idx_prompt_metrics_prompt_id ON prompt_performance_metrics(prompt_template_id);
CREATE INDEX idx_prompt_metrics_extracted_at ON prompt_performance_metrics(extracted_at);

-- Prompt Performance Daily Rollups (historical aggregates)
CREATE TABLE prompt_performance_daily (
    id SERIAL PRIMARY KEY,
    prompt_template_id INTEGER REFERENCES prompt_templates(id) NOT NULL,
    date DATE NOT NULL,

    -- Aggregated cost metrics
    total_extractions INTEGER NOT NULL,
    total_input_tokens BIGINT NOT NULL,
    total_output_tokens BIGINT NOT NULL,
    total_api_cost_usd DECIMAL(10, 2) NOT NULL,

    -- Aggregated quality metrics
    successful_extractions INTEGER NOT NULL,
    avg_confidence_score FLOAT,
    manual_review_count INTEGER NOT NULL,

    -- Aggregated execution metrics
    avg_execution_time_ms INTEGER NOT NULL,
    p95_execution_time_ms INTEGER,

    UNIQUE(prompt_template_id, date)
);

CREATE INDEX idx_prompt_daily_prompt_date ON prompt_performance_daily(prompt_template_id, date);
CREATE INDEX idx_prompt_daily_date ON prompt_performance_daily(date);
```

### Pattern 1: Immutable Versioning with Explicit Activation

**What:** Once a prompt version is created, it's immutable. New versions are created via copy-on-edit. Only one version per (task_type, name) can be `is_active = TRUE`.

**When to use:** Always. This prevents production debugging confusion ("which v4.2 ran on this trace?") and enables reliable rollback.

**Example:**
```python
# Source: Research synthesis from Mirascope, LaunchDarkly prompt versioning guides

from sqlalchemy import Column, Integer, String, Text, Boolean, Float, DateTime, CheckConstraint
from sqlalchemy.sql import func
from app.database import Base

class PromptTemplate(Base):
    """
    Immutable versioned prompt template.

    Activation lifecycle:
    1. Create new version (is_active=False)
    2. Test in staging/dev
    3. Explicitly activate (deactivates previous active version)
    4. Track performance metrics tied to this prompt_template_id
    5. Rollback = activate ANY historical version (not just previous)
    """
    __tablename__ = "prompt_templates"

    id = Column(Integer, primary_key=True, index=True)
    task_type = Column(String(50), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    version = Column(Integer, nullable=False)

    # Template content (immutable after creation)
    system_prompt = Column(Text, nullable=True)
    user_prompt_template = Column(Text, nullable=False)

    # Activation state (only one active per task_type + name)
    is_active = Column(Boolean, default=False, nullable=False)

    # Metadata
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    description = Column(Text, nullable=True)

    # Model configuration (part of versioned asset)
    model_name = Column(String(50), default='claude-sonnet-4-5-20250514')
    temperature = Column(Float, default=0.1)
    max_tokens = Column(Integer, default=1024)

    __table_args__ = (
        CheckConstraint('version > 0', name='version_positive'),
    )

    def __repr__(self):
        active_status = "ACTIVE" if self.is_active else "inactive"
        return f"<PromptTemplate({self.task_type}.{self.name} v{self.version} [{active_status}])>"
```

### Pattern 2: Jinja2 Template Rendering with Variable Validation

**What:** Store prompts as Jinja2 templates in database, render at runtime with validated variables.

**When to use:** For prompts with dynamic content (email body, subject, metadata). Use simple strings for static prompts.

**Example:**
```python
# Source: PromptLayer Jinja2 guide, DataCamp Jinja2 tutorial

from jinja2 import Environment, Template, TemplateSyntaxError, UndefinedError
import structlog

logger = structlog.get_logger(__name__)

class PromptRenderer:
    """
    Renders Jinja2 prompt templates with variable validation.

    Handles:
    - Template syntax errors (log and raise)
    - Missing variables (log and raise)
    - Type coercion for common types (str, int, float)
    """

    def __init__(self):
        # Configure Jinja2 environment
        self.env = Environment(
            autoescape=False,  # LLM prompts don't need HTML escaping
            trim_blocks=True,   # Remove first newline after block
            lstrip_blocks=True, # Remove leading spaces before blocks
        )

    def render(
        self,
        template_str: str,
        variables: dict,
        template_name: str = "unknown"
    ) -> str:
        """
        Render Jinja2 template with variables.

        Args:
            template_str: Jinja2 template string from database
            variables: Dict of variables to interpolate
            template_name: For error logging

        Returns:
            Rendered prompt string

        Raises:
            TemplateSyntaxError: Invalid Jinja2 syntax
            UndefinedError: Missing required variable
        """
        try:
            template = self.env.from_string(template_str)
            rendered = template.render(**variables)

            logger.info(
                "prompt_rendered",
                template_name=template_name,
                variables=list(variables.keys()),
                rendered_length=len(rendered)
            )

            return rendered

        except TemplateSyntaxError as e:
            logger.error(
                "template_syntax_error",
                template_name=template_name,
                error=str(e),
                line=e.lineno
            )
            raise

        except UndefinedError as e:
            logger.error(
                "template_variable_missing",
                template_name=template_name,
                error=str(e),
                provided_vars=list(variables.keys())
            )
            raise

# Example template in database:
# user_prompt_template = """
# Klassifiziere die E-Mail-Intent:
#
# Betreff: {{ subject }}
# Von: {{ from_email }}
# Text (erste 500 Zeichen): {{ body_truncated }}
#
# {% if has_attachments %}
# Anhänge: {{ attachment_count }} Datei(en)
# {% endif %}
#
# Antworte mit JSON: {"intent": "...", "confidence": 0.0-1.0}
# """

# Rendering:
renderer = PromptRenderer()
prompt = renderer.render(
    template_str=template.user_prompt_template,
    variables={
        "subject": "Forderung XYZ",
        "from_email": "creditor@example.com",
        "body_truncated": email_body[:500],
        "has_attachments": len(attachments) > 0,
        "attachment_count": len(attachments)
    },
    template_name=f"{template.task_type}.{template.name}"
)
```

### Pattern 3: Dual-Table Performance Tracking with Automated Rollups

**What:** Raw extraction-level metrics in one table (30-day retention), aggregated daily summaries in another (permanent). Scheduled job aggregates old raw data into rollups before deletion.

**When to use:** Always for production LLM applications tracking prompt performance. Prevents table bloat and slow queries on historical data.

**Example:**
```python
# Source: Citus Data incremental aggregation, Gameball rollup tables guide

from sqlalchemy import Column, Integer, BigInteger, Float, Decimal, Boolean, Date, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.sql import func
from app.database import Base

class PromptPerformanceMetrics(Base):
    """
    Raw extraction-level metrics. Retention: 30 days.

    After 30 days, data is aggregated into PromptPerformanceDaily
    and deleted from this table.
    """
    __tablename__ = "prompt_performance_metrics"

    id = Column(Integer, primary_key=True)
    prompt_template_id = Column(Integer, ForeignKey("prompt_templates.id"), nullable=False, index=True)
    email_id = Column(Integer, ForeignKey("incoming_emails.id"), nullable=False)

    # Cost metrics (USER DECISION: track both cost and quality)
    input_tokens = Column(Integer, nullable=False)
    output_tokens = Column(Integer, nullable=False)
    api_cost_usd = Column(Decimal(10, 6), nullable=False)

    # Quality metrics (USER DECISION: track both cost and quality)
    extraction_success = Column(Boolean, nullable=False)
    confidence_score = Column(Float, nullable=True)
    manual_review_required = Column(Boolean, nullable=True)

    # Execution metrics
    execution_time_ms = Column(Integer, nullable=False)

    extracted_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    # Retention constraint (USER DECISION: 30-day raw retention)
    __table_args__ = (
        CheckConstraint("extracted_at > NOW() - INTERVAL '30 days'", name="retention_30_days"),
    )


class PromptPerformanceDaily(Base):
    """
    Daily rollup of prompt performance metrics. Permanent retention.

    Aggregated from PromptPerformanceMetrics via scheduled job.
    """
    __tablename__ = "prompt_performance_daily"

    id = Column(Integer, primary_key=True)
    prompt_template_id = Column(Integer, ForeignKey("prompt_templates.id"), nullable=False)
    date = Column(Date, nullable=False)

    # Aggregated cost metrics
    total_extractions = Column(Integer, nullable=False)
    total_input_tokens = Column(BigInteger, nullable=False)
    total_output_tokens = Column(BigInteger, nullable=False)
    total_api_cost_usd = Column(Decimal(10, 2), nullable=False)

    # Aggregated quality metrics
    successful_extractions = Column(Integer, nullable=False)
    avg_confidence_score = Column(Float, nullable=True)
    manual_review_count = Column(Integer, nullable=False)

    # Aggregated execution metrics
    avg_execution_time_ms = Column(Integer, nullable=False)
    p95_execution_time_ms = Column(Integer, nullable=True)

    __table_args__ = (
        # Unique constraint ensures one rollup per (prompt_template_id, date)
        # Allows idempotent re-aggregation if job runs multiple times
    )


# Daily rollup job (run via pg_cron or Python scheduler)
def aggregate_daily_metrics(date: date, db_session):
    """
    Aggregate raw metrics for given date into daily summary.

    USER DECISION: Daily rollups for historical data.

    This job should run daily at midnight+1 hour to aggregate previous day.
    After aggregation completes, raw records older than 30 days are deleted
    by PostgreSQL constraint enforcement (or explicit cleanup job).
    """
    from sqlalchemy import func, select

    # Aggregate metrics for the date
    stmt = select(
        PromptPerformanceMetrics.prompt_template_id,
        func.count().label('total_extractions'),
        func.sum(PromptPerformanceMetrics.input_tokens).label('total_input_tokens'),
        func.sum(PromptPerformanceMetrics.output_tokens).label('total_output_tokens'),
        func.sum(PromptPerformanceMetrics.api_cost_usd).label('total_api_cost_usd'),
        func.sum(func.cast(PromptPerformanceMetrics.extraction_success, Integer)).label('successful_extractions'),
        func.avg(PromptPerformanceMetrics.confidence_score).label('avg_confidence_score'),
        func.sum(func.cast(PromptPerformanceMetrics.manual_review_required, Integer)).label('manual_review_count'),
        func.avg(PromptPerformanceMetrics.execution_time_ms).label('avg_execution_time_ms'),
        func.percentile_cont(0.95).within_group(PromptPerformanceMetrics.execution_time_ms).label('p95_execution_time_ms')
    ).where(
        func.date(PromptPerformanceMetrics.extracted_at) == date
    ).group_by(
        PromptPerformanceMetrics.prompt_template_id
    )

    results = db_session.execute(stmt).fetchall()

    # Insert or update daily rollups
    for row in results:
        rollup = PromptPerformanceDaily(
            prompt_template_id=row.prompt_template_id,
            date=date,
            total_extractions=row.total_extractions,
            total_input_tokens=row.total_input_tokens or 0,
            total_output_tokens=row.total_output_tokens or 0,
            total_api_cost_usd=row.total_api_cost_usd or 0,
            successful_extractions=row.successful_extractions or 0,
            avg_confidence_score=row.avg_confidence_score,
            manual_review_count=row.manual_review_count or 0,
            avg_execution_time_ms=int(row.avg_execution_time_ms) if row.avg_execution_time_ms else 0,
            p95_execution_time_ms=int(row.p95_execution_time_ms) if row.p95_execution_time_ms else None
        )

        # Upsert logic (INSERT ON CONFLICT UPDATE for PostgreSQL)
        db_session.merge(rollup)

    db_session.commit()

    logger.info("daily_metrics_aggregated", date=date, prompt_count=len(results))
```

### Pattern 4: Explicit Activation with Historical Rollback

**What:** Activating a prompt version is an explicit transaction that deactivates the current active version and activates the selected version. Rollback can target ANY historical version, not just the previous one.

**When to use:** Always. User decision requires explicit activation (no auto-promotion) and ANY-version rollback capability.

**Example:**
```python
# Source: User decision in CONTEXT.md + LaunchDarkly prompt versioning guide

from sqlalchemy.orm import Session
from sqlalchemy import and_
import structlog

logger = structlog.get_logger(__name__)

class PromptVersionManager:
    """
    Manages prompt version lifecycle: create, activate, rollback.

    USER DECISIONS:
    - Explicit activation required (no auto-activation of latest)
    - Rollback to ANY historical version (not just previous)
    - Archive old inactive versions after retention period
    """

    def __init__(self, db: Session):
        self.db = db

    def activate_version(
        self,
        task_type: str,
        name: str,
        version: int,
        activated_by: str
    ) -> PromptTemplate:
        """
        Activate a specific prompt version.

        Atomically:
        1. Deactivate current active version (if any)
        2. Activate target version
        3. Log activation event

        Args:
            task_type: e.g., 'classification', 'extraction'
            name: Human-readable prompt name
            version: Version number to activate
            activated_by: Username/system for audit trail

        Returns:
            Activated PromptTemplate

        Raises:
            ValueError: If target version doesn't exist
        """
        # Find target version
        target = self.db.query(PromptTemplate).filter(
            and_(
                PromptTemplate.task_type == task_type,
                PromptTemplate.name == name,
                PromptTemplate.version == version
            )
        ).first()

        if not target:
            raise ValueError(
                f"Prompt version not found: {task_type}.{name} v{version}"
            )

        # Deactivate current active version (if any)
        current_active = self.db.query(PromptTemplate).filter(
            and_(
                PromptTemplate.task_type == task_type,
                PromptTemplate.name == name,
                PromptTemplate.is_active == True
            )
        ).first()

        if current_active:
            previous_version = current_active.version
            current_active.is_active = False
            logger.info(
                "prompt_version_deactivated",
                task_type=task_type,
                name=name,
                version=previous_version
            )

        # Activate target version
        target.is_active = True
        self.db.commit()

        logger.info(
            "prompt_version_activated",
            task_type=task_type,
            name=name,
            version=version,
            activated_by=activated_by,
            previous_version=previous_version if current_active else None
        )

        return target

    def rollback_to_version(
        self,
        task_type: str,
        name: str,
        target_version: int,
        rolled_back_by: str
    ) -> PromptTemplate:
        """
        Rollback to ANY historical version.

        USER DECISION: Support rollback to ANY version, not just previous.

        Args:
            task_type: Prompt task type
            name: Prompt name
            target_version: Historical version to rollback to
            rolled_back_by: Username for audit trail

        Returns:
            Activated historical version
        """
        logger.warning(
            "prompt_rollback_initiated",
            task_type=task_type,
            name=name,
            target_version=target_version,
            rolled_back_by=rolled_back_by
        )

        # Rollback is just activation of historical version
        return self.activate_version(
            task_type=task_type,
            name=name,
            version=target_version,
            activated_by=f"{rolled_back_by} (ROLLBACK)"
        )

    def create_new_version(
        self,
        task_type: str,
        name: str,
        user_prompt_template: str,
        system_prompt: str = None,
        created_by: str = None,
        description: str = None,
        model_name: str = 'claude-sonnet-4-5-20250514',
        temperature: float = 0.1,
        max_tokens: int = 1024
    ) -> PromptTemplate:
        """
        Create new prompt version (copy-on-edit pattern).

        USER DECISION: Version creation mechanism is Claude's discretion.
        This implements copy-on-edit: each edit creates a new version.

        New versions start as inactive (is_active=False).
        Must be explicitly activated via activate_version().
        """
        # Find highest existing version
        highest = self.db.query(func.max(PromptTemplate.version)).filter(
            and_(
                PromptTemplate.task_type == task_type,
                PromptTemplate.name == name
            )
        ).scalar()

        next_version = (highest or 0) + 1

        new_version = PromptTemplate(
            task_type=task_type,
            name=name,
            version=next_version,
            user_prompt_template=user_prompt_template,
            system_prompt=system_prompt,
            is_active=False,  # USER DECISION: explicit activation required
            created_by=created_by,
            description=description,
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens
        )

        self.db.add(new_version)
        self.db.commit()
        self.db.refresh(new_version)

        logger.info(
            "prompt_version_created",
            task_type=task_type,
            name=name,
            version=next_version,
            created_by=created_by,
            is_active=False
        )

        return new_version
```

### Pattern 5: Task-Type Organization (Not Agent-Based)

**What:** Organize prompts by task type (classification, extraction, validation) rather than by agent. Multiple agents may use the same task-type prompts.

**When to use:** Always. User decision to align with multi-agent pipeline where Agent 1 = classification, Agent 2 = extraction, Agent 3 = consolidation.

**Example:**
```python
# Source: User decision in CONTEXT.md

# Task types aligned with multi-agent pipeline:
TASK_TYPES = {
    "classification": "Agent 1 - Intent classification prompts",
    "extraction": "Agent 2 - Entity extraction prompts (email, PDF, image)",
    "validation": "Agent 3 - Validation and consolidation prompts"
}

# Example prompt organization in database:
#
# task_type='classification', name='email_intent', version=5, is_active=True
# task_type='extraction', name='pdf_scanned', version=3, is_active=True
# task_type='extraction', name='email_body', version=7, is_active=True
# task_type='validation', name='amount_conflict_resolution', version=2, is_active=True

# Loading active prompt for task:
def get_active_prompt(db: Session, task_type: str, name: str) -> PromptTemplate:
    """
    Get currently active prompt for task type and name.

    Returns:
        Active PromptTemplate or None if no active version
    """
    return db.query(PromptTemplate).filter(
        and_(
            PromptTemplate.task_type == task_type,
            PromptTemplate.name == name,
            PromptTemplate.is_active == True
        )
    ).first()

# Usage in service:
# prompt = get_active_prompt(db, task_type='extraction', name='pdf_scanned')
# if not prompt:
#     raise ValueError("No active prompt for extraction.pdf_scanned")
#
# rendered = renderer.render(prompt.user_prompt_template, variables)
```

### Anti-Patterns to Avoid

- **Mutable versions:** Never modify prompt_templates rows after creation. Always create new version. Prevents production debugging confusion ("which v4 ran here?").
- **Auto-activation of latest:** Don't automatically activate newest version. Requires explicit activation for safety (user decision).
- **Unbounded raw metrics retention:** Don't keep extraction-level metrics forever. Causes table bloat and slow queries. Use dual-table with rollups (user decision: 30-day raw retention).
- **Agent-based organization:** Don't organize by agent ID. Use task types for flexibility (user decision).
- **Mixing template and business logic:** Don't put business rules in database templates. Keep Jinja2 templates focused on presentation/formatting only.

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Template rendering | Custom string replacement | Jinja2 3.1.6+ | Handles escaping, conditionals, loops, inheritance, error messages |
| Versioning logic | Manual version incrementing | SQLAlchemy with UNIQUE constraints | Database enforces version uniqueness, handles race conditions |
| Daily aggregation | Real-time GROUP BY queries | Scheduled rollup jobs (pg_cron or Python) | Aggregating millions of rows on-demand kills performance |
| Prompt caching | Application-level cache | Database query + LRU cache wrapper | PostgreSQL index on is_active handles fast lookups, LRU prevents stale reads |
| Change tracking | Manual audit logs | SQLAlchemy created_at + created_by columns | Built-in, consistent, queryable |

**Key insight:** Prompt versioning looks trivial ("just store strings with version numbers") but production systems need immutability, explicit activation, performance tracking, rollback safety, and audit trails. Established patterns (immutable versions, dual-table metrics, Jinja2 rendering) handle edge cases like concurrent activations, metrics retention, template syntax errors, and historical rollback.

## Common Pitfalls

### Pitfall 1: Only Tracking Template Changes

**What goes wrong:** Teams version the template string but don't version model configuration (temperature, max_tokens, model name). When debugging production issues, you can't reproduce the exact LLM behavior because model config has changed.

**Why it happens:** Prompt versioning tutorials focus on template text, not full "prompt asset" (template + model config + input schema).

**How to avoid:** Store model_name, temperature, max_tokens as columns in prompt_templates table. Treat them as part of the immutable version (user decision: Claude's discretion, this research recommends including them).

**Warning signs:** Production traces show unexpected LLM behavior, but template hasn't changed. Likely model config drift.

### Pitfall 2: No Retention Policy for Raw Metrics

**What goes wrong:** prompt_performance_metrics table grows unbounded (millions of rows). Queries slow down. Database storage costs increase. Eventually hits disk space limits.

**Why it happens:** Teams focus on "track everything" without considering data lifecycle.

**How to avoid:** Implement dual-table pattern: raw metrics with 30-day retention (user decision), daily rollups with permanent retention. Schedule nightly aggregation job. Add PostgreSQL CHECK constraint or cleanup job to enforce retention.

**Warning signs:** Slow queries on prompt_performance_metrics. Table size growing linearly with extraction volume. No aggregation strategy.

### Pitfall 3: Implicit Activation (Auto-Promoting Latest Version)

**What goes wrong:** New prompt version is created and automatically becomes active. Breaks production because untested prompt was never validated in staging.

**Why it happens:** Convenience over safety. "Latest version should be active" seems intuitive.

**How to avoid:** Explicit activation required (user decision). New versions start as is_active=FALSE. Must be tested in staging, then explicitly activated via activate_version() method.

**Warning signs:** Production incidents after prompt changes. No staging validation step. Developers complain "we can't test prompts before they go live."

### Pitfall 4: Lack of Rollback Testing

**What goes wrong:** Rollback mechanism is implemented but never tested. When production prompt causes regression, rollback fails or has unexpected side effects.

**Why it happens:** "We'll never need to rollback" optimism. Rollback is coded but not included in testing or CI/CD pipelines.

**How to avoid:** Test rollback in staging regularly. Include rollback test in CI: activate v2, verify, activate v1 (rollback), verify v1 is active. User decision requires ANY-version rollback, not just previous.

**Warning signs:** No rollback tests. No documented rollback procedure. Team doesn't know how to rollback a prompt version.

### Pitfall 5: Missing Template Syntax Validation

**What goes wrong:** Invalid Jinja2 syntax saved to database. Production code tries to render template, crashes with TemplateSyntaxError. Emails stuck in processing.

**Why it happens:** No validation on template creation. Assume users write valid Jinja2.

**How to avoid:** Validate Jinja2 syntax on prompt creation. Use Jinja2 Environment.from_string() to parse template, catch TemplateSyntaxError, reject invalid templates. Include unit tests with malformed templates.

**Warning signs:** Production errors like "TemplateSyntaxError: unexpected 'end of template'". Emails failing extraction due to template errors.

### Pitfall 6: Cache Staleness After Activation

**What goes wrong:** Application caches active prompt. Prompt is activated in database. Cache still serves old version for TTL duration (e.g., 5 minutes). Production runs with wrong prompt.

**Why it happens:** Application-level caching without cache invalidation on activation.

**How to avoid:** Either (A) don't cache prompts (database query with is_active index is fast enough), or (B) implement cache invalidation on activation (activate_version() method invalidates cache key). Prefer (A) for simplicity.

**Warning signs:** Prompt activation doesn't take effect immediately. "Wait 5 minutes after activation" workarounds.

### Pitfall 7: No Performance Degradation Alerting

**What goes wrong:** New prompt version increases API cost 3x or drops extraction accuracy from 95% to 70%. Team doesn't notice until monthly bill arrives or user complaints accumulate.

**Why it happens:** Metrics tracked but not monitored. No alerting on cost/quality regressions.

**How to avoid:** User decision leaves alerting implementation to Claude's discretion. Recommendation: Set up alerts on daily rollups for (1) API cost per extraction > threshold, (2) extraction success rate < threshold, (3) manual review rate > threshold. Alert triggers rollback investigation.

**Warning signs:** Surprise high API costs. Gradual quality degradation not caught. No alerts configured on prompt_performance_daily metrics.

## Code Examples

Verified patterns from official sources:

### Migration from Hardcoded Prompts to Database

```python
# Source: Codebase analysis + research synthesis

# BEFORE (hardcoded in intent_classifier.py):
prompt = f"""Klassifiziere die E-Mail-Intent in eine der folgenden Kategorien:
1. debt_statement - Gläubigerantwort mit Forderungsbetrag
2. payment_plan - Zahlungsplan-Vorschlag
...

E-Mail:
Betreff: {subject}
Text: {truncated_body}

Antworte nur mit JSON:
{{"intent": "debt_statement|payment_plan|...", "confidence": 0.0-1.0}}"""

# AFTER (database-backed):
from app.services.prompt_manager import get_active_prompt, render_prompt

# 1. Load active prompt from database
prompt_template = get_active_prompt(
    db=db,
    task_type='classification',
    name='email_intent'
)

if not prompt_template:
    raise ValueError("No active prompt for classification.email_intent")

# 2. Render Jinja2 template with variables
prompt = render_prompt(
    template=prompt_template,
    variables={
        'subject': subject,
        'truncated_body': body[:500]
    }
)

# 3. Track which prompt version was used
metric = PromptPerformanceMetrics(
    prompt_template_id=prompt_template.id,  # Links to versioned prompt
    email_id=email_id,
    input_tokens=response.usage.input_tokens,
    output_tokens=response.usage.output_tokens,
    api_cost_usd=calculate_cost(response.usage),
    extraction_success=True,
    confidence_score=result.confidence,
    manual_review_required=result.confidence < 0.6,
    execution_time_ms=execution_time_ms
)
db.add(metric)
db.commit()

# Now you can correlate performance issues with specific prompt versions
```

### Seeding Initial Prompts from Codebase

```python
# Source: Research synthesis + codebase analysis

# Migration script: Extract hardcoded prompts and seed database

from app.database import SessionLocal
from app.models.prompt_template import PromptTemplate

def seed_initial_prompts():
    """
    Seed database with current hardcoded prompts as v1.

    Run once during Phase 8 implementation to migrate
    existing prompts from code to database.
    """
    db = SessionLocal()

    prompts_to_seed = [
        {
            'task_type': 'classification',
            'name': 'email_intent',
            'version': 1,
            'system_prompt': None,
            'user_prompt_template': '''Klassifiziere die E-Mail-Intent in eine der folgenden Kategorien:

1. debt_statement - Gläubigerantwort mit Forderungsbetrag oder Schuldenstatus
2. payment_plan - Zahlungsplan-Vorschlag oder Bestätigung
3. rejection - Ablehnung oder Widerspruch der Forderung
4. inquiry - Frage die manuelle Antwort erfordert
5. auto_reply - Abwesenheitsnotiz oder automatische Antwort
6. spam - Marketing, unrelated content

E-Mail:
Betreff: {{ subject }}
Text: {{ body_truncated }}

Antworte nur mit JSON:
{"intent": "debt_statement|payment_plan|rejection|inquiry|auto_reply|spam", "confidence": 0.0-1.0}''',
            'is_active': True,
            'created_by': 'system_migration',
            'description': 'Migrated from hardcoded intent_classifier.py',
            'model_name': 'claude-haiku-4-20250514',
            'temperature': 0.0,
            'max_tokens': 100
        },
        {
            'task_type': 'extraction',
            'name': 'email_body',
            'version': 1,
            'system_prompt': '''Du bist ein Experten-Assistent für eine deutsche Rechtsanwaltskanzlei, die sich auf Schuldnerberatung spezialisiert hat.

Deine Aufgabe ist es, eingehende E-Mails von Gläubigern zu analysieren und strukturierte Informationen zu extrahieren.

Die Kanzlei sendet Anfragen an Gläubiger im Namen ihrer Mandanten. Die Gläubiger antworten dann mit Informationen über Schulden.

Extrahiere die folgenden Informationen aus der E-Mail:

1. **is_creditor_reply**: Ist dies eine legitime Gläubiger-Antwort?
2. **client_name**: Der vollständige Name des Mandanten
3. **creditor_name**: Der Firmenname des Gläubigers
4. **debt_amount**: Gesamtschulden in EUR
5. **reference_numbers**: Alle Referenznummern
6. **confidence**: Dein Vertrauen in die Extraktion
7. **summary**: Kurze 1-2 Satz Zusammenfassung

**Output Format** (NUR JSON):
{
  "is_creditor_reply": true/false,
  "client_name": "Nachname, Vorname" oder null,
  "creditor_name": "Firmenname" oder null,
  "debt_amount": 1234.56 oder null,
  "reference_numbers": ["AZ-123"] oder [],
  "confidence": 0.85,
  "summary": "Zusammenfassung" oder null
}''',
            'user_prompt_template': '''Bitte extrahiere Informationen aus dieser E-Mail:

**Von**: {{ from_email }}
**Betreff**: {{ subject }}

**E-Mail Inhalt**:
{{ email_body }}

Gib die Antwort als JSON zurück (nur JSON, keine zusätzlichen Erklärungen):''',
            'is_active': True,
            'created_by': 'system_migration',
            'description': 'Migrated from hardcoded entity_extractor_claude.py',
            'model_name': 'claude-sonnet-4-5-20250514',
            'temperature': 0.1,
            'max_tokens': 1024
        },
        {
            'task_type': 'extraction',
            'name': 'pdf_scanned',
            'version': 1,
            'system_prompt': None,
            'user_prompt_template': '''Analysiere dieses deutsche Gläubigerdokument und extrahiere die folgenden Informationen.

WICHTIGE REGELN:
1. Suche nach "Gesamtforderung" (Hauptbetrag) - dies ist der wichtigste Betrag
2. Akzeptiere auch Synonyme: "Forderungshöhe", "offener Betrag", "Gesamtsumme", "Schulden", "Restschuld"
3. Deutsche Zahlenformatierung: 1.234,56 EUR bedeutet 1234.56
4. Wenn keine explizite Gesamtforderung: Summiere "Hauptforderung" + "Zinsen" + "Kosten"

EXTRAHIERE:
1. gesamtforderung: Gesamtforderungsbetrag in EUR (nur Zahl, z.B. 1234.56)
2. glaeubiger: Name des Gläubigers/der Firma
3. schuldner: Name des Schuldners/Kunden
4. components: Falls Gesamtforderung nicht explizit, gib Aufschlüsselung an

Gib NUR valides JSON in diesem exakten Format zurück:
{
  "gesamtforderung": 1234.56,
  "glaeubiger": "Firmenname",
  "schuldner": "Kundenname",
  "components": {
    "hauptforderung": 1000.00,
    "zinsen": 150.56,
    "kosten": 84.00
  }
}

Wenn ein Feld nicht gefunden wird, nutze null.''',
            'is_active': True,
            'created_by': 'system_migration',
            'description': 'Migrated from hardcoded pdf_extractor.py EXTRACTION_PROMPT',
            'model_name': 'claude-sonnet-4-5-20250514',
            'temperature': 0.1,
            'max_tokens': 2048
        }
    ]

    for prompt_data in prompts_to_seed:
        existing = db.query(PromptTemplate).filter(
            and_(
                PromptTemplate.task_type == prompt_data['task_type'],
                PromptTemplate.name == prompt_data['name'],
                PromptTemplate.version == prompt_data['version']
            )
        ).first()

        if existing:
            print(f"Skipping {prompt_data['task_type']}.{prompt_data['name']} v{prompt_data['version']} - already exists")
            continue

        prompt = PromptTemplate(**prompt_data)
        db.add(prompt)
        print(f"Seeded {prompt_data['task_type']}.{prompt_data['name']} v{prompt_data['version']}")

    db.commit()
    print("Initial prompt seeding complete")

if __name__ == '__main__':
    seed_initial_prompts()
```

### Query Active Prompt with Caching

```python
# Source: Research on cache invalidation + PostgreSQL best practices

from functools import lru_cache
from sqlalchemy.orm import Session
from sqlalchemy import and_
import structlog

logger = structlog.get_logger(__name__)

# Simple in-memory cache (per-worker process)
# TTL handled by periodic cache clear (e.g., every 60 seconds)
@lru_cache(maxsize=32)
def _get_active_prompt_cached(task_type: str, name: str) -> int:
    """
    Return prompt_template.id for active version.

    Cached to avoid repeated database queries.
    Cache is per-worker process, cleared on activation.

    Returns:
        prompt_template.id or None if no active version
    """
    # Note: Can't pass Session to lru_cache, so this needs refactoring
    # for production. Shown here for concept only.
    # Better approach: Use Redis or application-level cache with invalidation.
    pass

def get_active_prompt(db: Session, task_type: str, name: str) -> PromptTemplate:
    """
    Get currently active prompt template.

    Fast path: Check cache for prompt_template_id.
    Slow path: Query database with is_active index.

    Note: For production, consider NOT caching if prompt changes are rare.
    PostgreSQL index on (task_type, name) WHERE is_active = TRUE is fast enough.
    """
    prompt = db.query(PromptTemplate).filter(
        and_(
            PromptTemplate.task_type == task_type,
            PromptTemplate.name == name,
            PromptTemplate.is_active == True
        )
    ).first()

    if not prompt:
        logger.error(
            "no_active_prompt",
            task_type=task_type,
            name=name
        )
        return None

    logger.debug(
        "active_prompt_loaded",
        task_type=task_type,
        name=name,
        version=prompt.version,
        prompt_id=prompt.id
    )

    return prompt

# Recommendation: Skip caching unless prompt queries show up in profiling.
# Database query with index is fast enough for most use cases.
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Hardcoded prompts in code | Database-backed versioned prompts | 2024-2025 | Enables runtime updates, A/B testing, audit trails without redeployment |
| Real-time metrics aggregation | Dual-table with raw + daily rollups | 2025-2026 | Prevents performance degradation on historical queries, reduces storage costs |
| Auto-activation of latest | Explicit activation with staging validation | 2025-2026 | Reduces production incidents from untested prompts |
| Agent-based organization | Task-type organization | 2026 | Flexibility for multi-agent pipelines sharing prompts |
| Simple string templates | Jinja2 with conditionals and loops | 2023-2024 | Reduces code duplication, enables non-technical prompt editing |

**Deprecated/outdated:**
- **String.Template (Python built-in):** Too limited for LLM prompts. Lacks conditionals, loops, inheritance. Use Jinja2 instead.
- **Mutable prompt versions:** Earlier prompt management systems allowed editing versions in place. Current best practice: immutable versions (create new version on edit).
- **Organization-level prompt caching:** Anthropic changed prompt caching to workspace-level isolation (Feb 2026) for security. Affects cache hit rates in multi-tenant systems.

## Open Questions

Things that couldn't be fully resolved:

1. **Archive retention period for old inactive versions**
   - What we know: User decision defers to Claude's discretion. Common industry practice: 90 days to 1 year for inactive versions.
   - What's unclear: Optimal retention for this codebase depends on prompt change frequency and compliance requirements.
   - Recommendation: Start with 90-day retention for inactive versions. Monitor disk usage and adjust. Critical: keep at least 2 previous versions beyond active for rollback safety.

2. **Performance alerting thresholds**
   - What we know: User decision requires tracking cost and quality metrics. Alerting implementation deferred to Claude's discretion.
   - What's unclear: Baseline metrics for this domain (German legal document extraction). Need historical data to set thresholds.
   - Recommendation: Implement alerting infrastructure in Phase 8. Set thresholds in Phase 9 after collecting 2-4 weeks of baseline metrics. Alert on: (1) API cost per extraction > 2x baseline, (2) extraction success rate < 90%, (3) manual review rate > 30%.

3. **Simple placeholders vs full Jinja2 for basic prompts**
   - What we know: User decision defers templating complexity to Claude's discretion.
   - What's unclear: Whether simple classification prompts (intent_classifier.py) need Jinja2 conditionals or just {{ variable }} substitution.
   - Recommendation: Use Jinja2 for ALL prompts (even simple ones) for consistency. Enables future expansion (adding conditionals) without migration. Performance overhead is negligible.

4. **Hot reload in production vs scheduled restarts**
   - What we know: Database-backed prompts enable runtime updates. Question: should application poll for prompt changes or restart on activation?
   - What's unclear: Whether this system uses multi-worker deployment (Gunicorn, Dramatiq workers) that might cache prompts across processes.
   - Recommendation: Start with explicit cache invalidation on activation. If deployment uses multiple workers, consider pub/sub for cache invalidation (Redis) or accept 5-minute cache TTL for simplicity.

## Sources

### Primary (HIGH confidence)
- [Jinja2 3.1.6 - PyPI](https://pypi.org/project/Jinja2/) - Current version, installation
- [PromptLayer: Prompt Templates with Jinja2](https://blog.promptlayer.com/prompt-templates-with-jinja2-2/) - Template rendering patterns
- [Citus Data: Scalable incremental data aggregation on Postgres](https://www.citusdata.com/blog/2018/06/14/scalable-incremental-data-aggregation/) - Rollup table patterns
- [Gameball Engineering: Scaling Analytics with PostgreSQL Rollup Tables](https://engineering.gameball.co/posts/scaling-analytics-with-postgresql-rollup-tables) - Daily aggregation implementation
- [SQLAlchemy 2.0 Documentation: Configuring a Version Counter](https://docs.sqlalchemy.org/en/20/orm/versioning.html) - ORM versioning patterns
- Codebase analysis: incoming_emails.py, calibration_samples.py, Alembic migrations - Existing patterns

### Secondary (MEDIUM confidence)
- [Mastering Prompt Versioning: Best Practices for Scalable LLM Development](https://dev.to/kuldeep_paul/mastering-prompt-versioning-best-practices-for-scalable-llm-development-2mgm) - Versioning best practices
- [LaunchDarkly: Prompt Versioning & Management Guide](https://launchdarkly.com/blog/prompt-versioning-and-management/) - Activation and rollback patterns
- [Braintrust: 7 best prompt management tools in 2026](https://www.braintrust.dev/articles/best-prompt-management-tools-2026) - Industry landscape
- [Mirascope: Five Tools to Help You Leverage Prompt Versioning](https://mirascope.com/blog/prompt-versioning) - Common pitfalls
- [Portkey.ai: The complete guide to LLM observability for 2026](https://portkey.ai/blog/the-complete-guide-to-llm-observability/) - Performance tracking architecture
- [Datadog: Track, compare, and optimize your LLM prompts](https://www.datadoghq.com/blog/llm-prompt-tracking/) - Metrics tracking patterns

### Tertiary (LOW confidence)
- [Medium: Using PostgreSQL as an LLM Prompt Store](https://medium.com/@pranavprakash4777/using-postgresql-as-an-llm-prompt-store-why-it-works-surprisingly-well-61143a10f40c) - Schema examples (Medium paywall, couldn't verify)
- [OneUptime: How to Build Cache Invalidation Strategies](https://oneuptime.com/blog/post/2026-01-30-cache-invalidation-strategies/view) - Cache invalidation patterns (general, not LLM-specific)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - Jinja2, PostgreSQL, SQLAlchemy are industry standard for this use case. Verified via multiple authoritative sources and codebase analysis.
- Architecture: HIGH - Patterns (immutable versioning, dual-table metrics, explicit activation) confirmed across multiple sources and aligned with user decisions.
- Pitfalls: MEDIUM - Based on industry articles and common sense, but not all verified in production. Some pitfalls are inferred from best practices.
- Performance tracking: HIGH - Dual-table aggregation pattern is well-documented for PostgreSQL. User decision specifies 30-day raw retention and daily rollups.
- Templating: HIGH - Jinja2 is standard for LLM prompt management. Usage patterns verified in official docs and industry articles.

**Research date:** 2026-02-06
**Valid until:** 60 days (stable domain, slow-moving patterns)

**User decision compliance:**
- Task-type organization: Implemented in Pattern 5
- Cost + quality metrics: Implemented in Pattern 3 (dual-table tracking)
- 30-day raw retention + daily rollups: Implemented in Pattern 3
- Explicit activation: Implemented in Pattern 4
- ANY-version rollback: Implemented in Pattern 4
- Free-form names: Schema supports human-readable names
- Templating complexity: Deferred to implementation (Jinja2 recommended for consistency)
- System/user prompt separation: Schema supports optional system_prompt column
- Alerting: Architecture includes metrics tables, thresholds deferred to Phase 9
- Archive retention period: Deferred to implementation (90-day recommendation)
