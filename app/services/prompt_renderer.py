"""
PromptRenderer Service
Handles Jinja2 template rendering with variable validation
"""

from jinja2 import Environment, TemplateSyntaxError, UndefinedError
import structlog

logger = structlog.get_logger(__name__)


class PromptRenderer:
    """
    Renders Jinja2 prompt templates with variable validation.

    Handles:
    - Template syntax errors (log and raise)
    - Missing variables (log and raise)
    - Consistent Jinja2 environment configuration

    Per RESEARCH.md Pattern 2: Jinja2 with autoescape=False for LLM prompts.
    """

    def __init__(self):
        """
        Initialize Jinja2 environment with LLM-optimized configuration.

        Config:
        - autoescape=False: LLM prompts don't need HTML escaping
        - trim_blocks=True: Remove first newline after block
        - lstrip_blocks=True: Remove leading spaces before blocks
        """
        self.env = Environment(
            autoescape=False,  # LLM prompts don't need HTML escaping
            trim_blocks=True,
            lstrip_blocks=True,
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
            template_name: For error logging (e.g., "classification.email_intent")

        Returns:
            Rendered prompt string

        Raises:
            TemplateSyntaxError: Invalid Jinja2 syntax
            UndefinedError: Missing required variable

        Example:
            renderer = PromptRenderer()
            prompt = renderer.render(
                "Hello {{ name }}!",
                {"name": "World"},
                "greeting"
            )
            # Returns: "Hello World!"
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

    def validate_template(self, template_str: str) -> tuple[bool, str | None]:
        """
        Validate Jinja2 template syntax without rendering.

        Per RESEARCH.md Pitfall 5: validate syntax on creation to prevent
        runtime errors during production extraction.

        Args:
            template_str: Jinja2 template string to validate

        Returns:
            (is_valid, error_message or None)

        Example:
            renderer = PromptRenderer()
            valid, error = renderer.validate_template("Hello {{ name }}")
            # Returns: (True, None)

            valid, error = renderer.validate_template("Hello {{ name")
            # Returns: (False, "unexpected 'end of template'")
        """
        try:
            # Parse template to check syntax
            self.env.from_string(template_str)
            return (True, None)

        except TemplateSyntaxError as e:
            error_msg = f"Line {e.lineno}: {e.message}"
            logger.warning(
                "template_validation_failed",
                error=error_msg
            )
            return (False, error_msg)

        except Exception as e:
            # Catch-all for unexpected errors
            error_msg = f"Unexpected error: {str(e)}"
            logger.error(
                "template_validation_error",
                error=error_msg
            )
            return (False, error_msg)
