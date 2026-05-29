from pagerbuddy.config import Settings
from pagerbuddy.models import Incident, Service, User
from pagerbuddy.notifications import NotificationClient


def test_twilio_trial_mode_rejects_non_allowed_recipient():
    client = NotificationClient(
        Settings(
            twilio_account_sid="sid",
            twilio_auth_token="token",
            twilio_from_number="+15550000000",
            twilio_trial_allowed_number="+15550101010",
        )
    )
    user = User(name="Responder", email="responder@example.com", phone_number="+15550000001")
    service = Service(name="API", escalation_policy_id=None, inbound_phone_number="+15551112222")
    incident = Incident(service=service, service_id=None, title="Incident")

    try:
        client.send_sms_text(incident, user, "hello")
    except ValueError as exc:
        assert "+15550101010" in str(exc)
    else:
        raise AssertionError("trial mode should reject non-allowed phone numbers")
