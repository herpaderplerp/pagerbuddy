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

Apply database migrations before starting services against a new or changed database:

```bash
PYTHONPATH=src alembic upgrade head
```

With Compose, rebuild the image and run migrations from the app container:

```bash
podman compose build app
podman compose run --rm app alembic upgrade head
podman compose up -d
```

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

Twilio webhooks, `/healthz`, dashboard static assets, and tokenized `/incident-actions/...` links stay public so external callbacks and emailed action links continue to work. Incident action links render a confirmation page on GET and require an explicit POST before acknowledging or resolving an incident.

## Notifications

Each user has configurable notification channels in `notification_preferences.channels`. Supported channels are `phone_call`, `sms`, and `email`. PagerBuddy sends every configured channel for the current escalation attempt, so a user configured for `phone_call` and `sms` receives both at the same time.

SMS notifications include the incident ID. Responders can reply `ACK <incident ID>` or `RESOLVE <incident ID>` to disambiguate when more than one open incident is assigned or pending for them.

Email action links open a confirmation page and require an explicit confirmation before changing incident state, so automated email link scanners cannot acknowledge or resolve incidents with a simple GET. They expire after `INCIDENT_ACTION_TOKEN_TTL_SECONDS`, which defaults to 86400 seconds. Set it to `0` to disable time-based expiry; tokens still stop working once used or once the incident is closed.

Disable referenced users instead of hard-deleting them. Disabled users cannot authenticate and are skipped by escalation, while historical incidents, notification attempts, and action tokens remain intact. Users cannot be disabled while they are still configured as a primary escalation-policy contact or catchall.

## Twilio Security

Twilio webhook signature validation is enabled by default with `TWILIO_VALIDATE_REQUESTS=true`. Set `PUBLIC_BASE_URL` to the exact public URL configured in Twilio, because Twilio signs the externally visible callback URL. For local webhook simulation without real Twilio signatures, temporarily set `TWILIO_VALIDATE_REQUESTS=false`.

Set `INBOUND_CALLER_WHITELIST_ENABLED=true` and `INBOUND_CALLER_WHITELIST_NUMBERS=+15551234567,+15557654321` to restrict who can open incidents by calling the inbound Twilio number. Rejected callers hear that they are not approved to open incidents, and PagerBuddy records an `inbound_call_rejected` system event with the caller, target number, call SID, and service ID when available.

## Local Recordings

Set `STORE_RECORDINGS_LOCALLY=true` to download Twilio voicemail recordings during the recording callback. In Compose, `./recordings` is mounted at `/app/recordings`; set `RECORDING_STORAGE_DIR=/app/recordings` for persisted local files. The `recordings/` directory is ignored by Git.

Set `LOCAL_TRANSCRIPTION_ENABLED=true` to transcribe the downloaded recording with `faster-whisper` before escalation starts. Default local settings are `LOCAL_TRANSCRIPTION_MODEL=base.en`, `LOCAL_TRANSCRIPTION_DEVICE=cpu`, and `LOCAL_TRANSCRIPTION_COMPUTE_TYPE=int8`. When local transcription is enabled, Twilio transcription is disabled in the inbound `<Record>` TwiML.

Outbound responder calls play the Twilio-hosted recording media URL with `<Play>` when `recording_url` is available. The local recording file is for transcription and retention, not current outbound playback.

## Notes

- All inbound calls create `P2` incidents unless an API caller sets another priority later.
- One Twilio number maps to one service.
- Recordings are streamed from Twilio by default unless local recording storage is enabled.
- Database schema is managed by Alembic migrations. App, worker, and scheduler startup do not create or alter tables.
- Compose waits for Postgres readiness before starting the app, worker, and scheduler. This matters on first boot with rootless Podman because database initialization can take a few seconds.
