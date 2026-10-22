"""Responses that work with and without JavaScript.

Plain form posts get redirect-after-POST with a one-time status message; htmx requests get
just the changed fragment, plus an out-of-band notice for the live status region.
"""

from dataclasses import dataclass
from typing import Any

from fastapi import Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.web.errors import is_htmx
from app.web.session import FlashKind, flash
from app.web.templating import templates


@dataclass(frozen=True)
class Notice:
    """A status message for the user."""

    message: str
    kind: FlashKind = "success"


def after_post(
    request: Request,
    redirect_url: str,
    notice: Notice,
    fragment: str | None = None,
    context: dict[str, Any] | None = None,
) -> Response:
    """Finish a successful POST.

    Args:
        request: The request.
        redirect_url: Where a plain form post goes next.
        notice: Status message for the user.
        fragment: Template to return to htmx; when None, htmx loads `redirect_url`.
        context: Data for the fragment.

    Returns:
        A fragment for htmx, otherwise a 303 redirect.
    """
    if not is_htmx(request):
        flash(request, notice.message, notice.kind)
        return RedirectResponse(redirect_url, status_code=status.HTTP_303_SEE_OTHER)
    if fragment is None:
        flash(request, notice.message, notice.kind)
        return Response(headers={"HX-Redirect": redirect_url})
    return render(request, fragment, {**(context or {}), "notice": notice})


def render(
    request: Request, template: str, context: dict[str, Any], status_code: int = status.HTTP_200_OK
) -> HTMLResponse:
    """Render a page or fragment. Fragments may carry a `notice` shown in the live region."""
    context.setdefault("notice", None)
    return templates.TemplateResponse(request, template, context, status_code=status_code)
