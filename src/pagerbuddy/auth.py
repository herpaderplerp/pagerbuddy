import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import Headers

from pagerbuddy.config import Settings, get_settings
from pagerbuddy.database import get_db
from pagerbuddy.models import User, UserRole

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
    "/auth",
)

PUBLIC_PREFIXES = (
    "/login",
    "/logout",
    "/dashboard/assets",
    "/healthz",
    "/webhooks/twilio",
    "/incident-actions",
)

PASSWORD_ALGORITHM = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 260_000
SESSION_COOKIE_NAME = "pagerbuddy_session"
# Last-resort process-local key keeps unsigned deployments from sharing a public fallback secret.
_PROCESS_SESSION_SECRET = secrets.token_urlsafe(32)


@dataclass(frozen=True)
class Principal:
    username: str
    role: UserRole
    user_id: str | None = None
    source: str = "user"

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.admin


def admin_auth_required(path: str, settings: Settings) -> bool:
    if path == "/":
        return True
    if any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
        return False
    return any(path.startswith(prefix) for prefix in PROTECTED_PREFIXES)


def _decode_basic_auth(headers: Headers) -> tuple[str, str] | None:
    header = headers.get("authorization", "")
    scheme, _, credentials = header.partition(" ")
    if scheme.lower() != "basic" or not credentials:
        return None
    try:
        decoded = base64.b64decode(credentials, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    username, separator, password = decoded.partition(":")
    if not separator:
        return None
    return username, password


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PASSWORD_ITERATIONS)
    return f"{PASSWORD_ALGORITHM}${PASSWORD_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        algorithm, iterations, salt, expected = password_hash.split("$", 3)
        if algorithm != PASSWORD_ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), expected)


def basic_auth_valid(headers: Headers, settings: Settings) -> bool:
    principal = authenticate_basic(headers, settings)
    return principal is not None and principal.source == "config"


def authenticate_credentials(username: str, password: str, settings: Settings, db: Session | None = None) -> Principal | None:
    if settings.admin_password and secrets.compare_digest(username, settings.admin_username):
        if secrets.compare_digest(password, settings.admin_password):
            return Principal(username=username, role=UserRole.admin, source="config")
    if db is None:
        return None
    user = db.scalar(select(User).where(User.email == username))
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        return None
    return Principal(username=user.email, role=user.role, user_id=str(user.id), source="user")


def authenticate_basic(headers: Headers, settings: Settings, db: Session | None = None) -> Principal | None:
    credentials = _decode_basic_auth(headers)
    if credentials is None:
        return None
    username, password = credentials
    return authenticate_credentials(username, password, settings, db)


def _session_secret(settings: Settings) -> str:
    return settings.session_secret or settings.admin_password or settings.twilio_auth_token or _PROCESS_SESSION_SECRET


def create_session_token(principal: Principal, settings: Settings, now: int | None = None) -> str:
    issued_at = int(now or time.time())
    payload = {
        "username": principal.username,
        "role": principal.role.value,
        "user_id": principal.user_id,
        "source": principal.source,
        "exp": issued_at + settings.session_ttl_seconds,
    }
    encoded_payload = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii")
    signature = hmac.new(_session_secret(settings).encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{encoded_payload}.{signature}"


def authenticate_session_cookie(token: str | None, settings: Settings, now: int | None = None) -> Principal | None:
    if not token or "." not in token:
        return None
    encoded_payload, signature = token.rsplit(".", 1)
    expected = hmac.new(_session_secret(settings).encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(encoded_payload.encode("ascii")).decode("utf-8"))
        if int(payload["exp"]) < int(now or time.time()):
            return None
        return Principal(
            username=str(payload["username"]),
            role=UserRole(str(payload["role"])),
            user_id=payload.get("user_id"),
            source=str(payload.get("source", "user")),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def authenticate_request(request: Request, settings: Settings, db: Session | None = None) -> Principal | None:
    principal = authenticate_session_cookie(request.cookies.get(SESSION_COOKIE_NAME), settings)
    if principal is not None:
        if principal.source == "user" and db is not None and principal.user_id:
            user = db.get(User, principal.user_id)
            if user is None or not user.is_active:
                return None
            return Principal(username=user.email, role=user.role, user_id=str(user.id), source="user")
        return principal
    return authenticate_basic(request.headers, settings, db)


def current_principal(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Principal:
    principal = getattr(request.state, "principal", None) or authenticate_request(request, settings, db)
    if principal is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return principal


def require_roles(*roles: UserRole):
    allowed_roles = set(roles)

    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if principal.is_admin or principal.role in allowed_roles:
            return principal
        raise HTTPException(status_code=403, detail="Forbidden")

    return dependency
