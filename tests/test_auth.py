from starlette.datastructures import Headers

from pagerbuddy.auth import (
    SESSION_COOKIE_NAME,
    Principal,
    admin_auth_required,
    authenticate_session_cookie,
    basic_auth_valid,
    create_session_token,
)
from pagerbuddy.config import Settings
from pagerbuddy.models import UserRole
from pagerbuddy.main import _safe_next_path


def test_admin_auth_required_when_password_blank():
    settings = Settings(admin_password="")

    assert admin_auth_required("/dashboard", settings)
    assert not basic_auth_valid(Headers({"authorization": "Basic YWRtaW46"}), settings)


def test_admin_auth_path_rules():
    settings = Settings(admin_username="admin", admin_password="secret")

    assert admin_auth_required("/", settings)
    assert admin_auth_required("/dashboard", settings)
    assert admin_auth_required("/incidents", settings)
    assert not admin_auth_required("/healthz", settings)
    assert not admin_auth_required("/login", settings)
    assert not admin_auth_required("/logout", settings)
    assert not admin_auth_required("/webhooks/twilio/voice", settings)
    assert not admin_auth_required("/incident-actions/token", settings)


def test_basic_auth_validation():
    settings = Settings(admin_username="admin", admin_password="secret")

    assert basic_auth_valid(Headers({"authorization": "Basic YWRtaW46c2VjcmV0"}), settings)
    assert not basic_auth_valid(Headers({"authorization": "Basic YWRtaW46d3Jvbmc="}), settings)
    assert not basic_auth_valid(Headers({}), settings)


def test_session_cookie_round_trips_principal():
    settings = Settings(admin_password="secret", session_ttl_seconds=60)
    principal = Principal(username="admin", role=UserRole.admin, source="config")

    token = create_session_token(principal, settings, now=1000)
    restored = authenticate_session_cookie(token, settings, now=1010)

    assert restored is not None
    assert restored.username == "admin"
    assert restored.role == UserRole.admin
    assert restored.source == "config"


def test_session_cookie_rejects_tampered_or_expired_token():
    settings = Settings(admin_password="secret", session_ttl_seconds=60)
    principal = Principal(username="admin", role=UserRole.admin, source="config")
    token = create_session_token(principal, settings, now=1000)

    assert authenticate_session_cookie(f"{token}x", settings, now=1010) is None
    assert authenticate_session_cookie(token, settings, now=1200) is None
    assert SESSION_COOKIE_NAME == "pagerbuddy_session"


def test_session_cookie_rejects_hardcoded_development_secret_forgery():
    settings = Settings(admin_password="", session_secret="", twilio_auth_token="", session_ttl_seconds=60)
    forged_payload = (
        "eyJleHAiOjEwNjAsInJvbGUiOiJhZG1pbiIsInNvdXJjZSI6ImNvbmZpZyIsInVzZXJfaWQiOm51bGws"
        "InVzZXJuYW1lIjoiYXR0YWNrZXJAZXhhbXBsZS5jb20ifQ=="
    )
    forged_signature = "6d758f9490edc4536ed047e011ad5d71e882d68ca626368e90dd44f0068117cb"

    assert authenticate_session_cookie(f"{forged_payload}.{forged_signature}", settings, now=1010) is None


def test_login_next_path_is_local_only():
    assert _safe_next_path("/dashboard") == "/dashboard"
    assert _safe_next_path("/docs") == "/docs"
    assert _safe_next_path("https://example.com") == "/dashboard"
    assert _safe_next_path("//example.com") == "/dashboard"
    assert _safe_next_path("/login") == "/dashboard"
    assert _safe_next_path("/logout") == "/dashboard"
