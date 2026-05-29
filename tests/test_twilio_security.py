from starlette.datastructures import Headers, QueryParams
from twilio.request_validator import RequestValidator

from pagerbuddy.config import Settings
from pagerbuddy.twilio_security import twilio_validation_url, valid_twilio_signature


def test_twilio_validation_url_uses_public_base_url_and_query_string():
    settings = Settings(public_base_url="https://pagerbuddy.example.com/")

    assert (
        twilio_validation_url("/webhooks/twilio/outbound-response", b"incident_id=123", settings)
        == "https://pagerbuddy.example.com/webhooks/twilio/outbound-response?incident_id=123"
    )


def test_valid_twilio_signature_accepts_signed_form_request():
    settings = Settings(public_base_url="https://pagerbuddy.example.com", twilio_auth_token="secret")
    body = b"From=%2B15550101010&Body=ACK"
    url = "https://pagerbuddy.example.com/webhooks/twilio/sms"
    signature = RequestValidator("secret").compute_signature(url, {"From": "+15550101010", "Body": "ACK"})

    assert valid_twilio_signature(
        "POST",
        "/webhooks/twilio/sms",
        b"",
        QueryParams(""),
        body,
        Headers({"x-twilio-signature": signature}),
        settings,
    )


def test_valid_twilio_signature_rejects_missing_or_bad_signature():
    settings = Settings(public_base_url="https://pagerbuddy.example.com", twilio_auth_token="secret")

    assert not valid_twilio_signature(
        "POST",
        "/webhooks/twilio/sms",
        b"",
        QueryParams(""),
        b"From=%2B15550101010&Body=ACK",
        Headers({}),
        settings,
    )
