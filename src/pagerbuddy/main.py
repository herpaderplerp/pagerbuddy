from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from pagerbuddy.api import router as api_router
from pagerbuddy.auth import admin_auth_required, authenticate_basic
from pagerbuddy.config import get_settings
from pagerbuddy.database import SessionLocal, init_db
from pagerbuddy.twilio_security import valid_twilio_signature
from pagerbuddy.twilio_webhooks import router as twilio_router

app = FastAPI(title="PagerBuddy", version="0.1.0")
UI_DIR = Path(__file__).parent / "ui"


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.middleware("http")
async def require_twilio_signature(request: Request, call_next):
    settings = get_settings()
    if request.url.path.startswith("/webhooks/twilio") and settings.twilio_validate_requests:
        body = await request.body()
        if not settings.twilio_auth_token:
            return Response("Twilio request validation is enabled but TWILIO_AUTH_TOKEN is not configured", status_code=503)
        if not valid_twilio_signature(
            request.method,
            request.url.path,
            request.url.query.encode("utf-8"),
            request.query_params,
            body,
            request.headers,
            settings,
        ):
            return Response("Invalid Twilio signature", status_code=403)
    return await call_next(request)


@app.middleware("http")
async def require_admin_auth(request: Request, call_next):
    settings = get_settings()
    if admin_auth_required(request.url.path, settings):
        with SessionLocal() as db:
            request.state.principal = authenticate_basic(request.headers, settings, db)
        if request.state.principal is None:
            return Response(
                "Authentication required",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="PagerBuddy Admin"'},
            )
    return await call_next(request)


app.include_router(api_router)
app.include_router(twilio_router)

app.mount("/dashboard/assets", StaticFiles(directory=UI_DIR), name="dashboard-assets")


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/dashboard")


@app.get("/dashboard", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(UI_DIR / "index.html")
