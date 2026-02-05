"""
Schema Validator
Validates data against Pydantic models with partial result preservation
"""

from typing import Any, Type, Dict, List
import structlog
from pydantic import BaseModel, ValidationError

logger = structlog.get_logger(__name__)


def validate_with_partial_results(data: dict, model_class: Type[BaseModel]) -> Dict[str, Any]:
    """
    Validate against Pydantic model, null failed fields, preserve good ones.
    USER DECISION: Proceed with partial results + needs_review flag.

    This function enables the pipeline to continue processing even when some fields
    fail validation, marking them as needs_review rather than blocking the entire process.

    Args:
        data: Dictionary to validate
        model_class: Pydantic model class to validate against

    Returns:
        {
            "data": {...},  # Validated or partial data (failed fields set to None)
            "needs_review": bool,  # True if any validation errors occurred
            "validation_errors": [  # List of validation errors
                {"field": str, "type": str, "msg": str},
                ...
            ]
        }

    Example:
        >>> class DebtData(BaseModel):
        ...     amount: float
        ...     creditor: str
        ...
        >>> result = validate_with_partial_results(
        ...     {"amount": "invalid", "creditor": "Bank XYZ"},
        ...     DebtData
        ... )
        >>> result["needs_review"]  # True
        >>> result["data"]  # {"amount": None, "creditor": "Bank XYZ"}
    """
    try:
        # Try full validation
        validated = model_class(**data)
        logger.info(
            "validation_success",
            model=model_class.__name__,
            fields=list(data.keys())
        )
        return {
            "data": validated.model_dump(),
            "needs_review": False,
            "validation_errors": []
        }

    except ValidationError as e:
        # Extract failed field names from validation errors
        failed_fields = set()
        validation_errors = []

        for error in e.errors():
            # loc[0] contains the field name
            field_name = error["loc"][0] if error["loc"] else "unknown"
            failed_fields.add(field_name)
            validation_errors.append({
                "field": str(field_name),
                "type": error["type"],
                "msg": error["msg"]
            })

        logger.warning(
            "validation_partial_failure",
            model=model_class.__name__,
            failed_fields=list(failed_fields),
            error_count=len(validation_errors)
        )

        # Create partial data: null out failed fields, preserve valid ones
        partial_data = data.copy()
        for field in failed_fields:
            partial_data[field] = None

        return {
            "data": partial_data,
            "needs_review": True,
            "validation_errors": validation_errors
        }
