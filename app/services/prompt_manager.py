"""
PromptVersionManager Service
Manages prompt version lifecycle: create, activate, rollback
"""

from sqlalchemy.orm import Session
from sqlalchemy import and_, func
import structlog

from app.models.prompt_template import PromptTemplate

logger = structlog.get_logger(__name__)


class PromptVersionManager:
    """
    Manages prompt version lifecycle: create, activate, rollback.

    USER DECISIONS honored (per CONTEXT.md):
    - Explicit activation required (no auto-activation of latest)
    - Rollback to ANY historical version (not just previous)
    - Free-form names

    Per RESEARCH.md Pattern 4: Explicit activation with historical rollback.
    """

    def __init__(self, db: Session):
        """
        Initialize manager with database session.

        Args:
            db: SQLAlchemy database session
        """
        self.db = db

    def get_active_prompt(self, task_type: str, name: str) -> PromptTemplate | None:
        """
        Get currently active prompt template.

        Uses partial index on (task_type, name) WHERE is_active = TRUE
        for fast lookups (per RESEARCH.md).

        Args:
            task_type: e.g., 'classification', 'extraction', 'validation'
            name: Human-readable prompt name

        Returns:
            Active PromptTemplate or None if no active version

        Example:
            manager = PromptVersionManager(db)
            prompt = manager.get_active_prompt('classification', 'email_intent')
        """
        prompt = self.db.query(PromptTemplate).filter(
            and_(
                PromptTemplate.task_type == task_type,
                PromptTemplate.name == name,
                PromptTemplate.is_active == True
            )
        ).first()

        if not prompt:
            logger.warning(
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

    def activate_version(
        self,
        task_type: str,
        name: str,
        version: int,
        activated_by: str
    ) -> PromptTemplate:
        """
        Activate a specific prompt version (atomically deactivates current).

        Per USER DECISION: explicit activation required.

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

        Example:
            manager = PromptVersionManager(db)
            prompt = manager.activate_version(
                'classification', 'email_intent', 2, 'admin@example.com'
            )
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

        previous_version = None
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
            previous_version=previous_version
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

        Per USER DECISION: not just previous version.

        Args:
            task_type: Prompt task type
            name: Prompt name
            target_version: Historical version to rollback to
            rolled_back_by: Username for audit trail

        Returns:
            Activated historical version

        Raises:
            ValueError: If target version doesn't exist

        Example:
            manager = PromptVersionManager(db)
            prompt = manager.rollback_to_version(
                'extraction', 'pdf_scanned', 3, 'admin@example.com'
            )
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

        New versions start as inactive (is_active=False).
        Per USER DECISION: explicit activation required.

        Args:
            task_type: e.g., 'classification', 'extraction', 'validation'
            name: Human-readable prompt name
            user_prompt_template: Jinja2 template string
            system_prompt: Optional system message
            created_by: Username for audit trail
            description: What changed, why
            model_name: Model to use for this prompt
            temperature: Temperature parameter
            max_tokens: Max tokens parameter

        Returns:
            Created PromptTemplate (inactive)

        Example:
            manager = PromptVersionManager(db)
            prompt = manager.create_new_version(
                task_type='extraction',
                name='pdf_scanned',
                user_prompt_template='Extract from: {{ document }}',
                created_by='admin@example.com',
                description='Added better German amount parsing'
            )
            # prompt.is_active == False (must explicitly activate)
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

    def list_versions(self, task_type: str, name: str) -> list[PromptTemplate]:
        """
        List all versions for a prompt, ordered by version descending.

        Args:
            task_type: Prompt task type
            name: Prompt name

        Returns:
            List of PromptTemplate versions (newest first)

        Example:
            manager = PromptVersionManager(db)
            versions = manager.list_versions('classification', 'email_intent')
            for v in versions:
                print(f"v{v.version}: {v.description} {'[ACTIVE]' if v.is_active else ''}")
        """
        versions = self.db.query(PromptTemplate).filter(
            and_(
                PromptTemplate.task_type == task_type,
                PromptTemplate.name == name
            )
        ).order_by(PromptTemplate.version.desc()).all()

        logger.debug(
            "prompt_versions_listed",
            task_type=task_type,
            name=name,
            count=len(versions)
        )

        return versions


def get_active_prompt(db: Session, task_type: str, name: str) -> PromptTemplate | None:
    """
    Convenience function for loading active prompt.

    Args:
        db: SQLAlchemy database session
        task_type: e.g., 'classification', 'extraction', 'validation'
        name: Human-readable prompt name

    Returns:
        Active PromptTemplate or None if no active version

    Example:
        from app.services.prompt_manager import get_active_prompt

        prompt = get_active_prompt(db, 'extraction', 'pdf_scanned')
        if not prompt:
            raise ValueError("No active prompt for extraction.pdf_scanned")
    """
    manager = PromptVersionManager(db)
    return manager.get_active_prompt(task_type, name)
