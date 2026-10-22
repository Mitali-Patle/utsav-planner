"""Friendly error pages for domain errors. Users never see a stack trace."""

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from app.domain.errors import DomainError, InvalidInputError, NotFoundError, StaleProposalError
from app.web.templating import templates

STATUS_BY_ERROR: tuple[tuple[type[DomainError], int], ...] = (
    (NotFoundError, 404),
    (StaleProposalError, 409),
    (InvalidInputError, 422),
)
DEFAULT_STATUS = 400


def is_htmx(request: Request) -> bool:
    """Whether the request came from htmx (so a fragment, not a page, is expected)."""
    return request.headers.get("hx-request") == "true"


def render_error(request: Request, message: str, status_code: int) -> HTMLResponse:
    """An error page, or a notice fragment for htmx requests."""
    template = "partials/notice.html" if is_htmx(request) else "error.html"
    response = templates.TemplateResponse(
        request,
        template,
        {"kind": "error", "message": message, "status_code": status_code},
        status_code=status_code,
    )
    if is_htmx(request):
        # Show the notice in the live region, whatever the form targeted.
        response.headers["HX-Retarget"] = "#status-region"
        response.headers["HX-Reswap"] = "innerHTML"
    return response


async def domain_error_handler(request: Request, error: Exception) -> HTMLResponse:
    """Map a domain error to its HTTP status and a readable message."""
    message = error.message if isinstance(error, DomainError) else "Something went wrong."
    status = next(
        (code for kind, code in STATUS_BY_ERROR if isinstance(error, kind)), DEFAULT_STATUS
    )
    return render_error(request, message, status)


def register_error_handlers(app: FastAPI) -> None:
    """Install the handlers on the app."""
    app.add_exception_handler(DomainError, domain_error_handler)
