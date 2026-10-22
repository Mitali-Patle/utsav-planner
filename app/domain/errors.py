"""Domain errors: rule violations that are safe to show to users.

Each error carries a stable `code` so the web layer can choose a message without parsing
text. Programming mistakes (for example, calling a function with impossible arguments)
raise `ValueError` instead.
"""

from typing import ClassVar


class DomainError(Exception):
    """Base class for domain rule violations.

    Attributes:
        message: Human-readable explanation, safe to show to users.
        code: Stable machine-readable identifier.
    """

    default_code: ClassVar[str] = "domain_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code


class InvalidInputError(DomainError):
    """A value is outside the allowed range or breaks a rule."""

    default_code = "invalid_input"


class NotFoundError(DomainError):
    """A referenced item does not exist in the plan."""

    default_code = "not_found"


class StaleProposalError(DomainError):
    """A proposal was built on an older version of the plan."""

    default_code = "stale_proposal"


class InfeasibleChangeError(DomainError):
    """A change cannot be applied within the budget.

    Attributes:
        shortfall_inr: How many rupees are missing, when that is known.
    """

    default_code = "infeasible_change"

    def __init__(self, message: str, *, code: str | None = None, shortfall_inr: int = 0) -> None:
        super().__init__(message, code=code)
        self.shortfall_inr = shortfall_inr
