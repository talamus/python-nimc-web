"""HTTP endpoints for the console subpackage.

Wire it into the app with a single call (see ``init_console`` below)::

    from .console import init_console
    init_console(app, templates)

That mounts the console's static assets, makes its ``console.html`` partial
available to your templates, and registers the router behind authentication.
"""

import signal
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader

from nimc.web.auth import get_current_user_or_fail
from fastapi import Depends

from . import registry, runner

PACKAGE_DIR = Path(__file__).parent
STATIC_DIR = PACKAGE_DIR / "static"
TEMPLATES_DIR = PACKAGE_DIR / "templates"

router = APIRouter(prefix="/console", tags=["console"])

# Set by init_console so the panel endpoint can render the partial.
_templates: Jinja2Templates | None = None


def init_console(app: FastAPI, templates: Jinja2Templates) -> None:
    """Mount static assets, register templates, and add the router (auth-gated)."""
    global _templates
    _templates = templates

    # Let the host app's templates include "console.html".
    templates.env.loader = ChoiceLoader(
        [templates.env.loader, FileSystemLoader(str(TEMPLATES_DIR))]
    )

    app.mount(
        "/console-static",
        StaticFiles(directory=str(STATIC_DIR)),
        name="console_static",
    )
    app.include_router(router, dependencies=[Depends(get_current_user_or_fail)])


@router.get("/{slug}/panel", response_class=HTMLResponse)
async def panel(request: Request, slug: str):
    """Render the console panel partial for one server (handy for HTMX loads)."""
    if not registry.server_exists(slug):
        raise HTTPException(status_code=404, detail="Unknown server")
    assert _templates is not None
    return _templates.TemplateResponse(
        "console.html",
        {"request": request, "slug": slug, "actions": registry.list_actions(slug)},
    )


@router.get("/{slug}/run/{action_id}")
async def run(slug: str, action_id: str):
    """Stream an action's output as Server-Sent Events."""
    if not registry.server_exists(slug):
        raise HTTPException(status_code=404, detail="Unknown server")
    try:
        action = registry.resolve(slug, action_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown action")

    # One lifecycle action per server at a time; log tails may always attach.
    if not action.kill_on_disconnect and runner.server_busy(slug):
        raise HTTPException(
            status_code=409, detail="Another action is already running for this server"
        )

    return StreamingResponse(
        runner.stream_action(slug, action),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable Nginx response buffering
        },
    )


@router.post("/{slug}/stop/{run_id}")
async def stop(slug: str, run_id: str, signal_name: str = "SIGINT"):
    """Send a signal to a running action. Defaults to SIGINT (graceful stop)."""
    sig = getattr(signal.Signals, signal_name, None)
    if sig is None:
        raise HTTPException(status_code=400, detail=f"Unknown signal: {signal_name}")
    if not runner.stop_run(run_id, sig):
        raise HTTPException(status_code=404, detail="Run not found or already stopped")
    return {"detail": f"Sent {signal_name} to {run_id}"}
