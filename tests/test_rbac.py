import base64

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pagerbuddy.api import router
from pagerbuddy.auth import hash_password
from pagerbuddy.config import Settings, get_settings
from pagerbuddy.database import Base, get_db
from pagerbuddy.models import User, UserRole


def basic(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def make_client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = session_factory()

    app = FastAPI()
    app.include_router(router)

    def override_db():
        try:
            yield db
        finally:
            pass

    def override_settings():
        return Settings(admin_username="bootstrap-admin", admin_password="bootstrap-secret")

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = override_settings
    return TestClient(app), db


def test_config_admin_authenticates_as_bootstrap_admin():
    client, _ = make_client()

    response = client.get("/auth/me", headers=basic("bootstrap-admin", "bootstrap-secret"))

    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    assert response.json()["source"] == "config"


def test_database_user_can_authenticate_and_rbac_blocks_admin_action():
    client, db = make_client()
    responder = User(
        name="Responder",
        email="responder@example.com",
        phone_number="+15550000001",
        role=UserRole.responder,
        password_hash=hash_password("responder-secret"),
    )
    db.add(responder)
    db.commit()

    me_response = client.get("/auth/me", headers=basic("responder@example.com", "responder-secret"))
    delete_response = client.delete(f"/users/{responder.id}", headers=basic("responder@example.com", "responder-secret"))

    assert me_response.status_code == 200
    assert me_response.json()["role"] == "responder"
    assert delete_response.status_code == 403


def test_inactive_database_user_cannot_authenticate():
    client, db = make_client()
    disabled_user = User(
        name="Disabled",
        email="disabled@example.com",
        phone_number="+15550000002",
        role=UserRole.admin,
        password_hash=hash_password("disabled-secret"),
        is_active=False,
    )
    db.add(disabled_user)
    db.commit()

    response = client.get("/auth/me", headers=basic("disabled@example.com", "disabled-secret"))

    assert response.status_code == 401
