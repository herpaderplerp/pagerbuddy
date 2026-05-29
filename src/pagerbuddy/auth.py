import base64
import hashlib
import hmac
import secrets
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
    "/dashboard/assets",
    "/healthz",
    "/webhooks/twilio",
    "/incident-actions",
)

PASSWORD_ALGORITHM = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 260_000


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


def authenticate_basic(headers: Headers, settings: Settings, db: Session | None = None) -> Principal | None:
    credentials = _decode_basic_auth(headers)
    if credentials is None:
        return None
    username, password = credentials
    if settings.admin_password and secrets.compare_digest(username, settings.admin_username):
        if secrets.compare_digest(password, settings.admin_password):
            return Principal(username=username, role=UserRole.admin, source="config")
    if db is None:
        return None
    user = db.scalar(select(User).where(User.email == username))
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        return None
    return Principal(username=user.email, role=user.role, user_id=str(user.id), source="user")


def current_principal(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Principal:
    principal = authenticate_basic(request.headers, settings, db)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": 'Basic realm="PagerBuddy Admin"'},
        )
    return principal


def require_roles(*roles: UserRole):
    allowed_roles = set(roles)

    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if principal.is_admin or principal.role in allowed_roles:
            return principal
        raise HTTPException(status_code=403, detail="Forbidden")

    return dependency
