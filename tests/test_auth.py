from starlette.datastructures import Headers

from pagerbuddy.auth import admin_auth_required, basic_auth_valid
from pagerbuddy.config import Settings


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
    assert not admin_auth_required("/webhooks/twilio/voice", settings)
    assert not admin_auth_required("/incident-actions/token", settings)


def test_basic_auth_validation():
    settings = Settings(admin_username="admin", admin_password="secret")

    assert basic_auth_valid(Headers({"authorization": "Basic YWRtaW46c2VjcmV0"}), settings)
    assert not basic_auth_valid(Headers({"authorization": "Basic YWRtaW46d3Jvbmc="}), settings)
    assert not basic_auth_valid(Headers({}), settings)
