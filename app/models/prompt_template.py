"""
PromptTemplate Model
Stores versioned prompt templates with task-type organization
"""

from sqlalchemy import Column, Integer, String, Text, Boolean, Float, DateTime, CheckConstraint, Index
from sqlalchemy.sql import func, text
from app.database import Base


class PromptTemplate(Base):
    """
    Immutable versioned prompt template.

    Activation lifecycle:
    1. Create new version (is_active=False by default)
    2. Test in staging/dev
    3. Explicitly activate (deactivates previous active version)
    4. Track performance metrics tied to this prompt_template_id
    5. Rollback = activate ANY historical version (not just previous)

    Organization:
    - task_type: 'classification', 'extraction', 'validation'
    - name: Human-readable, free-form (e.g., 'email_intent', 'pdf_scanned')
    - version: Auto-incremented per (task_type, name) pair

    Version immutability:
    Once created, prompt content (system_prompt, user_prompt_template, model config)
    cannot be modified. New versions are created via copy-on-edit pattern.
    """
    __tablename__ = "prompt_templates"

    # Primary Key
    id = Column(Integer, primary_key=True, index=True)

    # Organization
    task_type = Column(String(50), nullable=False, index=True)  # 'classification', 'extraction', 'validation'
    name = Column(String(100), nullable=False)  # Human-readable, free-form
    version = Column(Integer, nullable=False)  # Auto-incremented per (task_type, name)

    # Template content (immutable after creation)
    system_prompt = Column(Text, nullable=True)  # Optional system message
    user_prompt_template = Column(Text, nullable=False)  # Jinja2 template

    # Activation state (only one active per task_type + name)
    is_active = Column(Boolean, default=False, nullable=False)

    # Metadata
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    description = Column(Text, nullable=True)  # What changed, why

    # Model configuration (part of versioned asset per RESEARCH.md)
    model_name = Column(String(50), default='claude-sonnet-4-5-20250514')
    temperature = Column(Float, default=0.1)
    max_tokens = Column(Integer, default=1024)

    __table_args__ = (
        CheckConstraint('version > 0', name='version_positive'),
        # Partial index for fast active prompt lookup
        Index('idx_prompt_templates_active', 'task_type', 'name', postgresql_where=text('is_active = TRUE')),
    )

    def __repr__(self):
        active_status = "ACTIVE" if self.is_active else "inactive"
        return f"<PromptTemplate({self.task_type}.{self.name} v{self.version} [{active_status}])>"
