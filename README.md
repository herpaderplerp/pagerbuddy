# PagerBuddy

PagerBuddy is a Dockerized, Twilio-based on-call incident management service. It accepts inbound voicemail calls, creates incidents, downloads and transcribes recordings locally, pages responders through escalation policies, and records an immutable incident timeline.

## Run

```bash
cp .env.example .env
docker compose up --build
```

With rootless Podman:

```bash
cp .env.example .env
podman compose up --build
```

The admin dashboard is available at `http://localhost:8000/dashboard`.

The API listens on `http://localhost:8000`. Configure Twilio webhooks against:

- Voice webhook: `POST /webhooks/twilio/voice`
- Recording callback: `POST /webhooks/twilio/recording-complete`
- Transcription callback: `POST /webhooks/twilio/transcription-complete`
- SMS webhook: `POST /webhooks/twilio/sms`

## Authentication And RBAC

Set `ADMIN_PASSWORD` in `.env` to enable the bootstrap HTTP Basic admin account. `ADMIN_USERNAME` defaults to `admin`.

Database users can also authenticate with HTTP Basic using their email address and password. User roles are enforced on protected API routes:

- `admin`: full management of users, policies, services, schedules, stakeholder subscriptions, and incidents.
- `responder`: incident operations and read access to operational configuration.
- `stakeholder`: read-only operational access.

If `ADMIN_PASSWORD` is blank, the bootstrap admin account is disabled. At least one active database admin with a password is then required for admin access.

Twilio webhooks, `/healthz`, dashboard static assets, and tokenized `/incident-actions/...` links stay public so external callbacks and emailed action links continue to work.

## Notifications

Each user has configurable notification channels in `notification_preferences.channels`. Supported channels are `phone_call`, `sms`, and `email`. PagerBuddy sends every configured channel for the current escalation attempt, so a user configured for `phone_call` and `sms` receives both at the same time.

## Twilio Security

Twilio webhook signature validation is enabled by default with `TWILIO_VALIDATE_REQUESTS=true`. Set `PUBLIC_BASE_URL` to the exact public URL configured in Twilio, because Twilio signs the externally visible callback URL. For local webhook simulation without real Twilio signatures, temporarily set `TWILIO_VALIDATE_REQUESTS=false`.

## Local Recordings

Set `STORE_RECORDINGS_LOCALLY=true` to download Twilio voicemail recordings during the recording callback. In Compose, `./recordings` is mounted at `/app/recordings`; set `RECORDING_STORAGE_DIR=/app/recordings` for persisted local files. The `recordings/` directory is ignored by Git.

Set `LOCAL_TRANSCRIPTION_ENABLED=true` to transcribe the downloaded recording with `faster-whisper` before escalation starts. Default local settings are `LOCAL_TRANSCRIPTION_MODEL=base.en`, `LOCAL_TRANSCRIPTION_DEVICE=cpu`, and `LOCAL_TRANSCRIPTION_COMPUTE_TYPE=int8`. When local transcription is enabled, Twilio transcription is disabled in the inbound `<Record>` TwiML.

Outbound responder calls play the Twilio-hosted recording media URL with `<Play>` when `recording_url` is available. The local recording file is for transcription and retention, not current outbound playback.

## Notes

- All inbound calls create `P2` incidents unless an API caller sets another priority later.
- One Twilio number maps to one service.
- Recordings are streamed from Twilio by default unless local recording storage is enabled.
- Database tables are created at app startup for the draft implementation. Replace this with Alembic before production use.
- Compose waits for Postgres readiness before starting the app, worker, and scheduler. This matters on first boot with rootless Podman because database initialization can take a few seconds.
