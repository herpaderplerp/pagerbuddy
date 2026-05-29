from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./pagerbuddy.db"
    public_base_url: str = "http://localhost:8000"
    admin_username: str = "admin"
    admin_password: str | None = None

    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None
    twilio_trial_allowed_number: str | None = None
    twilio_validate_requests: bool = True

    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "alerts@pagerbuddy.local"

    store_recordings_locally: bool = False
    recording_storage_dir: str = "recordings"
    local_transcription_enabled: bool = True
    local_transcription_model: str = "base.en"
    local_transcription_device: str = "cpu"
    local_transcription_compute_type: str = "int8"
    admin_alert_emails: str = Field(default="")
    worker_poll_seconds: int = 10
    scheduler_poll_seconds: int = 3600

    @property
    def admin_alert_email_list(self) -> list[str]:
        return [email.strip() for email in self.admin_alert_emails.split(",") if email.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
