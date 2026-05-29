from urllib.parse import parse_qsl

from starlette.datastructures import Headers, QueryParams
from twilio.request_validator import RequestValidator

from pagerbuddy.config import Settings


def twilio_validation_url(path: str, query_string: bytes, settings: Settings) -> str:
    base_url = settings.public_base_url.rstrip("/")
    url = f"{base_url}{path}"
    if query_string:
        url = f"{url}?{query_string.decode('utf-8')}"
    return url


def twilio_params(method: str, query_params: QueryParams, body: bytes) -> dict[str, str]:
    if method.upper() == "GET":
        return dict(query_params.multi_items())
    return dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True))


def valid_twilio_signature(
    method: str,
    path: str,
    query_string: bytes,
    query_params: QueryParams,
    body: bytes,
    headers: Headers,
    settings: Settings,
) -> bool:
    if not settings.twilio_auth_token:
        return False
    signature = headers.get("x-twilio-signature", "")
    if not signature:
        return False
    validator = RequestValidator(settings.twilio_auth_token)
    return validator.validate(
        twilio_validation_url(path, query_string, settings),
        twilio_params(method, query_params, body),
        signature,
    )
