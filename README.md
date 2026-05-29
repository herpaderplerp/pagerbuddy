# PagerBuddy

PagerBuddy is a Dockerized, Twilio-based on-call incident management service. It accepts inbound voicemail calls, creates incidents, transcribes recordings through Twilio callbacks, pages responders through escalation policies, and records an immutable incident timeline.

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

## Admin Auth

Set `ADMIN_PASSWORD` in `.env` for HTTP Basic authentication on the dashboard, API docs, and admin REST API. `ADMIN_USERNAME` defaults to `admin`. If `ADMIN_PASSWORD` is blank, protected admin routes still require authentication and will reject all credentials until a password is configured.

Twilio webhooks, `/healthz`, dashboard static assets, and tokenized `/incident-actions/...` links stay public so external callbacks and emailed action links continue to work.

## Twilio Security

Twilio webhook signature validation is enabled by default with `TWILIO_VALIDATE_REQUESTS=true`. Set `PUBLIC_BASE_URL` to the exact public URL configured in Twilio, because Twilio signs the externally visible callback URL. For local webhook simulation without real Twilio signatures, temporarily set `TWILIO_VALIDATE_REQUESTS=false`.

## Notes

- v1 is REST API only, matching the spec default.
- All inbound calls create `P2` incidents unless an API caller sets another priority later.
- One Twilio number maps to one service.
- Recordings are streamed from Twilio by default. Local storage is represented by configuration for a future storage adapter.
- Database tables are created at app startup for the draft implementation. Replace this with Alembic before production use.
- Compose waits for Postgres readiness before starting the app, worker, and scheduler. This matters on first boot with rootless Podman because database initialization can take a few seconds.
