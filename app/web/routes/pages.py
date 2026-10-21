"""Server-rendered pages."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.web.templating import templates

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def home(request: Request) -> HTMLResponse:
    """Render the landing page.

    Args:
        request: Incoming request.

    Returns:
        The rendered page.
    """
    return templates.TemplateResponse(request, "base.html")
