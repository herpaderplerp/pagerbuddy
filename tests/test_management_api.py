from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from pagerbuddy import api, schemas
from pagerbuddy.database import Base
from pagerbuddy.models import StakeholderSubscription, User, UserRole


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def test_user_update_and_delete_management_endpoints():
    db = make_session()
    user = api.create_user(
        schemas.UserCreate(
            name="Responder",
            email="responder@example.com",
            phone_number="+15550000001",
            timezone="America/Toronto",
        ),
        db,
    )

    updated = api.update_user(user.id, schemas.UserUpdate(name="Primary responder", role=UserRole.admin), db)

    assert updated.name == "Primary responder"
    assert updated.role == UserRole.admin

    api.delete_user(user.id, db)

    assert db.get(User, user.id) is None


def test_service_schedule_policy_and_stakeholder_management_endpoints():
    db = make_session()
    stakeholder = api.create_user(
        schemas.UserCreate(
            name="Stakeholder",
            email="stakeholder@example.com",
            phone_number="+15550000002",
            role=UserRole.stakeholder,
        ),
        db,
    )
    policy = api.create_policy(schemas.EscalationPolicyCreate(name="Production", steps=[]), db)
    service = api.create_service(
        schemas.ServiceCreate(
            name="API",
            escalation_policy_id=policy.id,
            inbound_phone_number="+15551112222",
            description="Old description",
        ),
        db,
    )
    schedule = api.create_schedule(schemas.ScheduleCreate(name="Primary", timezone="UTC", layers=[]), db)

    service = api.update_service(service.id, schemas.ServiceUpdate(description="Updated description"), db)
    schedule = api.update_schedule(schedule.id, schemas.ScheduleUpdate(timezone="America/Toronto"), db)
    policy = api.update_policy(policy.id, schemas.EscalationPolicyUpdate(repeat_enabled=True, repeat_count=2), db)

    assert service.description == "Updated description"
    assert schedule.timezone == "America/Toronto"
    assert policy.repeat_enabled is True
    assert policy.repeat_count == 2

    api.subscribe_stakeholder(service.id, stakeholder.id, db)
    duplicate_failed = False
    try:
        api.subscribe_stakeholder(service.id, stakeholder.id, db)
    except HTTPException as exc:
        duplicate_failed = exc.status_code == 409
    assert duplicate_failed

    api.unsubscribe_stakeholder(service.id, stakeholder.id, db)
    assert db.scalar(select(StakeholderSubscription)) is None

    api.delete_service(service.id, db)
    api.delete_schedule(schedule.id, db)
    api.delete_policy(policy.id, db)
