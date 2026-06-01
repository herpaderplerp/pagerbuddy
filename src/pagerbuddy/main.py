from pathlib import Path
from html import escape

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from pagerbuddy.api import router as api_router
from pagerbuddy.auth import (
    SESSION_COOKIE_NAME,
    admin_auth_required,
    authenticate_credentials,
    authenticate_request,
    create_session_token,
)
from pagerbuddy.config import get_settings
from pagerbuddy.database import SessionLocal, init_db
from pagerbuddy.twilio_security import valid_twilio_signature
from pagerbuddy.twilio_webhooks import router as twilio_router

app = FastAPI(title="PagerBuddy", version="0.1.0")
UI_DIR = Path(__file__).parent / "ui"
DEFAULT_LOGIN_REDIRECT = "/dashboard"


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
            request.state.principal = authenticate_request(request, settings, db)
        if request.state.principal is None:
            if request.url.path in {"/", "/dashboard", "/docs", "/redoc"}:
                return RedirectResponse(f"/login?next={_safe_next_path(request.url.path)}", status_code=303)
            return Response("Authentication required", status_code=401)
    return await call_next(request)


app.include_router(api_router)
app.include_router(twilio_router)

app.mount("/dashboard/assets", StaticFiles(directory=UI_DIR), name="dashboard-assets")


def _safe_next_path(next_path: str | None) -> str:
    if not next_path or not next_path.startswith("/") or next_path.startswith("//"):
        return DEFAULT_LOGIN_REDIRECT
    if next_path.startswith("/login") or next_path.startswith("/logout"):
        return DEFAULT_LOGIN_REDIRECT
    return next_path


def _login_page(error: str = "", next_path: str = DEFAULT_LOGIN_REDIRECT) -> HTMLResponse:
    next_path = _safe_next_path(next_path)
    error_html = f'<div class="error">{escape(error)}</div>' if error else ""
    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>PagerBuddy Login</title>
    <style>
      body {{
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        background: #f4f6f8;
        color: #17202a;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }}
      main {{
        width: min(420px, calc(100vw - 32px));
        border: 1px solid #d9e0e7;
        border-radius: 8px;
        background: #ffffff;
        box-shadow: 0 16px 40px rgba(30, 45, 62, 0.08);
        overflow: hidden;
      }}
      header {{
        padding: 22px 24px;
        border-bottom: 1px solid #d9e0e7;
      }}
      h1 {{
        margin: 0;
        font-size: 22px;
      }}
      p {{
        margin: 6px 0 0;
        color: #687485;
      }}
      form {{
        display: grid;
        gap: 14px;
        padding: 24px;
      }}
      label {{
        display: grid;
        gap: 6px;
        color: #3f4a56;
        font-size: 13px;
        font-weight: 700;
      }}
      input {{
        min-height: 40px;
        border: 1px solid #d9e0e7;
        border-radius: 8px;
        padding: 8px 10px;
        font: inherit;
      }}
      button {{
        min-height: 42px;
        border: 0;
        border-radius: 8px;
        background: #146c94;
        color: #ffffff;
        cursor: pointer;
        font: inherit;
        font-weight: 700;
      }}
      .error {{
        border: 1px solid #ffd1cc;
        border-radius: 8px;
        padding: 10px 12px;
        background: #fff1f0;
        color: #b42318;
      }}
    </style>
  </head>
  <body>
    <main>
      <header>
        <h1>PagerBuddy</h1>
        <p>Sign in to continue</p>
      </header>
      <form method="post" action="/login">
        {error_html}
        <input type="hidden" name="next" value="{escape(next_path)}" />
        <label>Email or admin username<input name="username" autocomplete="username" required autofocus /></label>
        <label>Password<input name="password" type="password" autocomplete="current-password" required /></label>
        <button type="submit">Sign in</button>
      </form>
    </main>
  </body>
</html>"""
    return HTMLResponse(html)


@app.get("/login", include_in_schema=False)
def login(next: str = DEFAULT_LOGIN_REDIRECT) -> HTMLResponse:
    return _login_page(next_path=next)


@app.post("/login", include_in_schema=False)
async def login_submit(request: Request) -> Response:
    form = await request.form()
    username = str(form.get("username") or "")
    password = str(form.get("password") or "")
    next_path = _safe_next_path(str(form.get("next") or DEFAULT_LOGIN_REDIRECT))
    settings = get_settings()
    with SessionLocal() as db:
        principal = authenticate_credentials(username, password, settings, db)
    if principal is None:
        return _login_page("Invalid username or password", next_path=next_path)
    response = RedirectResponse(next_path, status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        create_session_token(principal, settings),
        httponly=True,
        samesite="lax",
        max_age=settings.session_ttl_seconds,
    )
    return response


@app.post("/logout", include_in_schema=False)
def logout() -> RedirectResponse:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/dashboard")


@app.get("/dashboard", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(UI_DIR / "index.html")
