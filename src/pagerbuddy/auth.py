import base64
import secrets

from starlette.datastructures import Headers

from pagerbuddy.config import Settings

PROTECTED_PREFIXES = (
    "/dashboard",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/users",
    "/services",
    "/schedules",
    "/escalation-policies",
    "/incidents",
)

PUBLIC_PREFIXES = (
    "/dashboard/assets",
    "/healthz",
    "/webhooks/twilio",
    "/incident-actions",
)


def admin_auth_required(path: str, settings: Settings) -> bool:
    if path == "/":
        return True
    if any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
        return False
    return any(path.startswith(prefix) for prefix in PROTECTED_PREFIXES)


def basic_auth_valid(headers: Headers, settings: Settings) -> bool:
    if not settings.admin_password:
        return False
    header = headers.get("authorization", "")
    scheme, _, credentials = header.partition(" ")
    if scheme.lower() != "basic" or not credentials:
        return False
    try:
        decoded = base64.b64decode(credentials, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    username, separator, password = decoded.partition(":")
    if not separator:
        return False
    return secrets.compare_digest(username, settings.admin_username) and secrets.compare_digest(password, settings.admin_password)
