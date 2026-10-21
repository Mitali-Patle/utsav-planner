"""Server-rendered pages."""

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.web.templating import templates

router = APIRouter(tags=["pages"])

FAVICON_PATH = "/static/img/favicon.svg"


@router.get("/favicon.ico", include_in_schema=False)
def favicon() -> RedirectResponse:
    """Redirect legacy `/favicon.ico` requests to the SVG icon served by the CDN.

    Returns:
        A permanent redirect.
    """
    return RedirectResponse(FAVICON_PATH, status_code=status.HTTP_308_PERMANENT_REDIRECT)


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def home(request: Request) -> HTMLResponse:
    """Render the landing page.

    Args:
        request: Incoming request.

    Returns:
        The rendered page.
    """
    return templates.TemplateResponse(request, "base.html")
